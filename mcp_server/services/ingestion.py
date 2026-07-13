"""Ingestion e indicizzazione dei documenti condominiali (PDF).

Pipeline (una sola chiamata LLM per documento):
1. Estrazione testo pagina-per-pagina con `pdftotext -layout` (poppler), che
   preserva l'allineamento delle colonne meglio delle librerie Python.
2. Sezionamento via Claude Sonnet: il modello riceve le pagine *cappate*
   (~2000 caratteri l'una: gli bastano i segnali strutturali) e ritorna un
   indice a sezioni (titolo, summary, page_start, page_end, contiene_tabelle).
3. Validazione meccanica in codice (copertura 1→N, niente buchi/overlap).
   Se fallisce → un retry passando gli errori al modello → se rifallisce →
   stato needs_review (output grezzo conservato).
4. Assemblaggio del content_md integrale per ogni sezione, dal testo completo
   delle pagine del range.

Il sezionatore DESCRIVE soltanto: nessun parsing, regex, normalizzazione di
nomi/codici unità, né estrazione strutturata delle tabelle.
"""

import json
import logging
import os
import re
import subprocess

from openai import OpenAI

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("INGESTION_MODEL", "gpt-5-mini")
REASONING_EFFORT = os.getenv("INGESTION_EFFORT", "medium")
MAX_COMPLETION_TOKENS = int(os.getenv("INGESTION_MAX_TOKENS", "16000"))

# Caratteri per pagina passati al sezionatore. Servono i segnali strutturali
# (intestazioni, titoli, marcatori di continuazione tabella), non ogni riga.
PAGE_CAP = 2000


# ---------- Estrazione testo ----------

def estrai_pagine(pdf_path: str, n_pagine: int | None = None) -> list[str]:
    """Estrae il testo pagina-per-pagina con pdftotext -layout.

    Ritorna una lista di stringhe, una per pagina (indice 0 = pagina 1).
    Allinea la lunghezza a `n_pagine` se fornito (pad/troncamento del
    form-feed finale), così la numerazione resta 1:1 con il PDF.
    """
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"pdftotext non disponibile o timeout: {e}")

    testo = out.stdout.decode("utf-8", errors="replace")
    # pdftotext separa le pagine con il form-feed (\f), con uno finale spurio.
    pagine = testo.split("\f")
    if pagine and pagine[-1].strip() == "":
        pagine.pop()

    if n_pagine is not None:
        if len(pagine) > n_pagine:
            pagine = pagine[:n_pagine]
        while len(pagine) < n_pagine:
            pagine.append("")

    return pagine


def _conta_pagine(pdf_path: str) -> int:
    import fitz
    with fitz.open(pdf_path) as doc:
        return doc.page_count


# ---------- Anno di riferimento del documento ----------

# Anno 1950-2049 non attaccato ad altre cifre. NB: niente \b (l'underscore è un
# carattere di parola, quindi "bilancio_2025" romperebbe \b); uso i lookaround sulle cifre.
_ANNO_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")

ANNO_SYSTEM = (
    "Sei un assistente che cataloga documenti condominiali. Dato il testo iniziale di un documento "
    "(bilancio, riparto, verbale, consumi, regolamento, avviso...), individua l'ANNO DI ESERCIZIO o "
    "di RIFERIMENTO del documento. Rispondi SOLO con l'anno a 4 cifre (es. 2025), oppure con la parola "
    "NESSUNO se non è determinabile con ragionevole certezza. Niente altro testo."
)


def _primo_anno(testo: str) -> int | None:
    m = _ANNO_RE.search(testo or "")
    return int(m.group(0)) if m else None


def estrai_anno(nome_file: str, testo: str | None = None) -> int | None:
    """Anno di riferimento del documento: prima dal nome file (regex), poi dal contenuto (LLM)."""
    # 1) Dal nome file (gratis, deterministico).
    anno = _primo_anno(nome_file)
    if anno:
        return anno

    # 2) Dal contenuto, via LLM (solo se serve).
    if testo and OPENAI_API_KEY:
        try:
            client = OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": ANNO_SYSTEM},
                    {"role": "user", "content": testo[:4000]},
                ],
                reasoning_effort="low",
                max_completion_tokens=300,
            )
            return _primo_anno(resp.choices[0].message.content or "")
        except Exception as e:
            logger.error("Estrazione anno via LLM fallita: %s", e)
    return None


# ---------- Sezionatore (LLM) ----------

SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "sezioni": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titolo": {"type": "string"},
                    "summary": {"type": "string"},
                    "page_start": {"type": "integer"},
                    "page_end": {"type": "integer"},
                    "contiene_tabelle": {"type": "boolean"},
                },
                "required": ["titolo", "summary", "page_start", "page_end", "contiene_tabelle"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sezioni"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Sei un indicizzatore di documenti condominiali (bilanci, riparti, verbali,
regolamenti, tabelle consumi). Ricevi il testo di un PDF pagina per pagina (ogni pagina è
troncata: vedi i segnali strutturali, non ogni riga). Produci un INDICE a sezioni del documento.

Per OGNI sezione fornisci:
- titolo: titolo conciso della sezione.
- summary: 2-3 frasi in italiano che descrivono COSA contiene la sezione e A QUALI DOMANDE di un
  condomino può rispondere (es. "Dettaglio delle spese di manutenzione ascensore; utile per sapere
  quanto è costato un intervento o perché una voce è aumentata").
- page_start, page_end: intervallo di pagine 1-based, inclusivo.
- contiene_tabelle: true se la sezione contiene tabelle (riparti, importi, consumi), altrimenti false.

REGOLE DI SEZIONAMENTO (vincolanti):
1. Le sezioni devono coprire TUTTE le pagine da 1 a N, SENZA buchi e SENZA sovrapposizioni.
   La pagina successiva inizia sempre a page_end + 1. La copertina è una sezione a sé.
2. NON spezzare MAI una tabella tra due sezioni. Le tabelle reali proseguono per molte pagine: si
   riconoscono da intestazioni di colonna ripetute, marcatori di continuazione (es. ">>>"), stessa
   struttura di colonne. Una tabella multi-pagina appartiene a UNA sola sezione, anche se la sezione
   risulta lunga decine di pagine. NEL DUBBIO, ACCORPA.
3. I confini sono sempre a granularità di pagina (mai a metà pagina).

NON interpretare e NON normalizzare i dati: descrivi soltanto. Niente parsing di nomi, codici unità,
piani o interni; niente estrazione delle tabelle. Ti limiti a delimitare e riassumere le sezioni.

Rispondi con un oggetto JSON della forma: {"sezioni": [{"titolo": "...", "summary": "...",
"page_start": 1, "page_end": 3, "contiene_tabelle": false}, ...]}."""


def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY non configurata nel .env.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _input_pagine(pagine: list[str]) -> str:
    parti = []
    for i, p in enumerate(pagine, start=1):
        testo = (p or "").strip()[:PAGE_CAP]
        parti.append(f"===== PAGINA {i} =====\n{testo}")
    return "\n\n".join(parti)


def chiama_sezionatore(pagine: list[str], n_pagine: int, errori: list[str] | None = None) -> dict:
    """Una chiamata al sezionatore. Ritorna il dict grezzo {'sezioni': [...]}.

    Se `errori` è fornito (retry), li passa al modello chiedendo di correggere.
    """
    client = _client()

    user = (
        f"Il documento ha esattamente {n_pagine} pagine. Genera l'indice a sezioni che copre "
        f"le pagine da 1 a {n_pagine} secondo le regole.\n\n{_input_pagine(pagine)}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    if errori:
        messages.append({"role": "user", "content": (
            "La proposta precedente NON ha superato la validazione per questi motivi:\n- "
            + "\n- ".join(errori)
            + f"\n\nRigenera l'indice completo correggendo gli errori. Ricorda: copertura totale "
              f"1→{n_pagine}, nessun buco né sovrapposizione, nessuna tabella spezzata."
        )})

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "indice_documento", "strict": True, "schema": SECTION_SCHEMA},
        },
        reasoning_effort=REASONING_EFFORT,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
    )
    testo = (response.choices[0].message.content or "").strip()
    return json.loads(testo)


# ---------- Validazione meccanica ----------

def valida_sezioni(sezioni: list[dict], n_pagine: int) -> list[str]:
    """Validazione meccanica: copertura completa 1→N, niente overlap, range validi.

    Ritorna la lista degli errori (vuota se valida). Assume sezioni già ordinate
    per page_start.
    """
    errori = []
    if not sezioni:
        return ["Nessuna sezione prodotta."]

    atteso = 1
    for i, s in enumerate(sezioni):
        ps, pe = s.get("page_start"), s.get("page_end")
        if not isinstance(ps, int) or not isinstance(pe, int):
            errori.append(f"Sezione {i+1} ('{s.get('titolo','?')}'): page_start/page_end non interi.")
            continue
        if ps > pe:
            errori.append(f"Sezione {i+1} ('{s.get('titolo','?')}'): page_start {ps} > page_end {pe}.")
        if ps < 1 or pe > n_pagine:
            errori.append(f"Sezione {i+1} ('{s.get('titolo','?')}'): range {ps}-{pe} fuori da 1-{n_pagine}.")
        if ps != atteso:
            if ps > atteso:
                errori.append(f"Buco di copertura: la pagina {atteso} non è coperta (sezione {i+1} inizia a {ps}).")
            else:
                errori.append(f"Sovrapposizione: la sezione {i+1} ('{s.get('titolo','?')}') inizia a {ps}, attesa {atteso}.")
        atteso = max(atteso, pe + 1)

    if atteso != n_pagine + 1:
        errori.append(f"Copertura incompleta: ultima pagina coperta {atteso-1}, attese {n_pagine}.")

    return errori


# ---------- Assemblaggio content_md ----------

def assembla_content_md(pagine: list[str], page_start: int, page_end: int) -> str:
    """Testo integrale delle pagine del range, con marcatori di pagina."""
    blocchi = []
    for n in range(page_start, page_end + 1):
        testo = pagine[n - 1] if 1 <= n <= len(pagine) else ""
        blocchi.append(f"----- Pagina {n} -----\n{testo.rstrip()}")
    return "\n\n".join(blocchi)


# ---------- Orchestrazione ----------

def processa_documento(pdf_path: str) -> dict:
    """Esegue l'intera pipeline su un PDF.

    Ritorna un dict:
      {"stato": "ready"|"needs_review"|"error",
       "n_pagine": int|None,
       "errore": str|None,
       "indice_raw": str|None,          # JSON grezzo del sezionatore (per ispezione)
       "sezioni": [ {titolo, summary, page_start, page_end, contiene_tabelle, content_md, ordine} ]}
    Non solleva: incapsula gli errori nello stato.
    """
    # 1. Conteggio pagine ed estrazione testo.
    try:
        n_pagine = _conta_pagine(pdf_path)
        pagine = estrai_pagine(pdf_path, n_pagine)
    except Exception as e:
        logger.error("Estrazione fallita per %s: %s", pdf_path, e)
        return {"stato": "error", "n_pagine": None, "errore": f"Estrazione testo fallita: {e}",
                "indice_raw": None, "sezioni": []}

    # PDF scansionato / senza testo estraibile → error (niente OCR per ora).
    if not any((p or "").strip() for p in pagine):
        return {"stato": "error", "n_pagine": n_pagine,
                "errore": "Nessun testo estraibile: il PDF sembra scansionato (OCR non supportato).",
                "indice_raw": None, "sezioni": []}

    # 2. Sezionatore + 3. validazione (con un retry).
    errori = None
    raw = None
    sezioni_valide = None
    for tentativo in range(2):
        try:
            data = chiama_sezionatore(pagine, n_pagine, errori=errori)
        except Exception as e:
            logger.error("Sezionatore fallito (%s) per %s: %s", tentativo, pdf_path, e)
            return {"stato": "error", "n_pagine": n_pagine, "errore": f"Sezionatore fallito: {e}",
                    "indice_raw": raw, "sezioni": []}

        sezioni = sorted(data.get("sezioni", []), key=lambda s: (s.get("page_start") or 0))
        raw = json.dumps({"sezioni": sezioni}, ensure_ascii=False, indent=2)
        errori = valida_sezioni(sezioni, n_pagine)
        if not errori:
            sezioni_valide = sezioni
            break
        logger.warning("Validazione tentativo %s fallita: %s", tentativo, errori)

    if sezioni_valide is None:
        # Mai dati invalidi marcati come buoni: conserva l'output grezzo per ispezione.
        return {"stato": "needs_review", "n_pagine": n_pagine,
                "errore": "; ".join(errori or ["indice non validabile"]),
                "indice_raw": raw, "sezioni": []}

    # 4. Assemblaggio content_md integrale.
    out_sezioni = []
    for ordine, s in enumerate(sezioni_valide):
        out_sezioni.append({
            "ordine": ordine,
            "titolo": s["titolo"],
            "summary": s.get("summary"),
            "page_start": s["page_start"],
            "page_end": s["page_end"],
            "contiene_tabelle": bool(s.get("contiene_tabelle")),
            "content_md": assembla_content_md(pagine, s["page_start"], s["page_end"]),
        })

    return {"stato": "ready", "n_pagine": n_pagine, "errore": None,
            "indice_raw": raw, "sezioni": out_sezioni}
