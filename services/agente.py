"""Agente che risponde alle domande dei condòmini sui documenti del condominio.

Architettura a 3 stadi (tutto scopato per condominio, mai contesto globale):

1. PIANIFICATORE — riceve la domanda + l'elenco completo dei documenti del
   condominio con l'indice (sezioni + summary). Sceglie quali sezioni (1 o più)
   interrogare e per ognuna genera una sotto-domanda mirata.
2. INTERROGAZIONE PER-SEZIONE — per ogni sezione scelta, una chiamata LLM che
   riceve la sotto-domanda + il contenuto integrale della sezione e risponde
   SOLO su quella base, con una citazione testuale.
3. COMPOSIZIONE FINALE — una chiamata LLM che mette insieme le risposte parziali
   (con le citazioni) e confeziona la risposta finale per il condomino.

Indipendente e testabile: `rispondi(db, condominio_id, domanda)` ritorna sia la
risposta finale sia la traccia (piano + passi) per ispezione da GUI.
Pensato per essere poi collegato a WhatsApp/mail/telefono.
"""

import json
import logging
import os

from openai import OpenAI
from sqlalchemy.orm import Session

from database import Condominio, Documento, Sezione, StatoDocumento
from services.contesto import contesto_temporale
from services import istruzioni

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("AGENTE_MODEL", "gpt-5-mini")
EFFORT = os.getenv("AGENTE_EFFORT", "medium")

MAX_SEZIONI = 6              # tetto di sezioni interrogabili per domanda
MAX_SECTION_CHARS = 120000  # cap di sicurezza sul contenuto passato per sezione


TRACE_CAP = 6000  # cap per campo input/output salvato nella traccia


def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY non configurata nel .env.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _rec(trace, fase: str, input_text: str, output_text: str):
    """Registra una chiamata LLM nella traccia (se fornita)."""
    if trace is None:
        return
    trace.append({
        "fase": fase,
        "modello": MODEL,
        "input": (input_text or "")[:TRACE_CAP],
        "output": (output_text or "")[:TRACE_CAP],
    })


# ---------- Catalogo (indice per il pianificatore) ----------

def _catalogo(condominio: Condominio) -> tuple[str, dict[int, Sezione]]:
    """Costruisce il testo dell'indice dei documenti + mappa sezione_id -> Sezione.

    Include solo i documenti in stato READY (indicizzati e validati).
    """
    righe = []
    mappa: dict[int, Sezione] = {}
    for doc in condominio.documenti:
        if doc.stato != StatoDocumento.READY or not doc.sezioni:
            continue
        righe.append(
            f"\n# DOCUMENTO id={doc.id} — {doc.nome_file} "
            f"(categoria: {doc.categoria}, anno: {doc.anno if doc.anno else 'n/d'})"
        )
        for s in doc.sezioni:
            mappa[s.id] = s
            tab = " [contiene tabelle]" if s.contiene_tabelle else ""
            righe.append(
                f"  - sezione_id={s.id} | pp. {s.page_start}-{s.page_end}{tab} | {s.titolo}\n"
                f"      {s.summary or ''}"
            )
    return "\n".join(righe).strip(), mappa


# ---------- Stadio 1: pianificatore ----------

PLANNER_SYSTEM = """Sei il pianificatore di un assistente per i condòmini. Ricevi la domanda di un
condomino e l'elenco dei documenti del SUO condominio, ciascuno con l'indice delle sezioni
(titolo + riassunto di cosa contiene e a quali domande risponde).

Il tuo compito: scegliere quali sezioni (una o più) contengono con buona probabilità la risposta,
e per ciascuna formulare una sotto-domanda mirata da porre a chi leggerà SOLO quella sezione.

Regole:
- Scegli solo sezioni davvero pertinenti (di norma 1-3; al massimo 6). Nel dubbio su quale tra due
  sezioni simili, includile entrambe.
- Usa esclusivamente i sezione_id presenti nell'elenco.
- Se NESSUNA sezione è pertinente, restituisci una lista "query" vuota.
- Non rispondere alla domanda: pianifica soltanto."""

PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "ragionamento": {"type": "string"},
        "query": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sezione_id": {"type": "integer"},
                    "sotto_domanda": {"type": "string"},
                },
                "required": ["sezione_id", "sotto_domanda"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["ragionamento", "query"],
    "additionalProperties": False,
}


def pianifica(client: OpenAI, domanda: str, catalogo: str, trace=None) -> dict:
    user = f"DOMANDA DEL CONDOMINO:\n{domanda}\n\nDOCUMENTI DISPONIBILI (indice):\n{catalogo}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": f"{PLANNER_SYSTEM}\n\n{contesto_temporale()}{istruzioni.blocco_prompt()}"},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "piano", "strict": True, "schema": PLANNER_SCHEMA},
        },
        reasoning_effort=EFFORT,
        max_completion_tokens=4000,
    )
    raw = resp.choices[0].message.content or "{}"
    _rec(trace, "Pianificatore", user, raw)
    return json.loads(raw)


# ---------- Stadio 2: interrogazione per-sezione ----------

SECTION_SYSTEM = """Rispondi alla domanda basandoti ESCLUSIVAMENTE sul testo della sezione fornita.
- Se l'informazione è presente: rispondi in modo conciso e fattuale, e includi una citazione, cioè
  una breve frase/dato copiato letteralmente dal testo a supporto.
- Se l'informazione NON è presente nella sezione: imposta trovato=false e lascia la risposta vuota.
- Non inventare nulla e non usare conoscenza esterna al testo."""

SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "trovato": {"type": "boolean"},
        "risposta": {"type": "string"},
        "citazione": {"type": "string"},
    },
    "required": ["trovato", "risposta", "citazione"],
    "additionalProperties": False,
}


def interroga_sezione(client: OpenAI, sotto_domanda: str, sezione: Sezione, trace=None) -> dict:
    contenuto = (sezione.content_md or "")[:MAX_SECTION_CHARS]
    user = (
        f"DOMANDA:\n{sotto_domanda}\n\n"
        f"SEZIONE: {sezione.titolo} (pagine {sezione.page_start}-{sezione.page_end})\n\n"
        f"TESTO DELLA SEZIONE:\n{contenuto}"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": f"{SECTION_SYSTEM}{istruzioni.blocco_prompt()}"},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "risposta_sezione", "strict": True, "schema": SECTION_SCHEMA},
        },
        reasoning_effort=EFFORT,
        # Tetto generoso: con effort alto/tabelle grandi il ragionamento consuma molti
        # token; con un tetto basso l'output usciva VUOTO (finish_reason=length) e veniva
        # scambiato per "non trovato". Vedi gestione esplicita sotto.
        max_completion_tokens=int(os.getenv("AGENTE_SEZIONE_MAX_TOKENS", "6000")),
    )
    choice = resp.choices[0]
    content = (choice.message.content or "").strip()
    # Nella traccia non salviamo l'intero content_md (può essere enorme): solo un'anteprima.
    input_log = (
        f"DOMANDA: {sotto_domanda}\n"
        f"SEZIONE: {sezione.titolo} (pp. {sezione.page_start}-{sezione.page_end}) "
        f"[testo: {len(sezione.content_md or '')} caratteri — l'INPUT all'LLM è integrale, "
        f"qui sotto solo un'anteprima]\n\n"
        f"--- anteprima testo sezione ---\n{contenuto[:1500]}"
    )

    if not content:
        # Output vuoto: tipicamente il ragionamento ha esaurito max_completion_tokens.
        logger.warning("Sezione '%s' (%d char): output VUOTO (finish_reason=%s) — token esauriti dal reasoning",
                       sezione.titolo, len(contenuto), choice.finish_reason)
        _rec(trace, f"Sezione: {sezione.titolo}", input_log,
             f"⚠ OUTPUT VUOTO (finish_reason={choice.finish_reason}): il modello ha esaurito i token "
             f"sul ragionamento prima di rispondere. Alza AGENTE_SEZIONE_MAX_TOKENS o abbassa l'effort. "
             f"(Trattato come 'non trovato'.)")
        return {"trovato": False, "risposta": "", "citazione": ""}

    _rec(trace, f"Sezione: {sezione.titolo}", input_log, content)
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        return {"trovato": False, "risposta": "", "citazione": ""}


# ---------- Stadio 3: composizione finale ----------

FINAL_SYSTEM = """Sei l'assistente virtuale di uno studio di amministrazione condominiale. Parli al
condomino con tono cortese, chiaro e diretto. Ricevi la sua domanda e le informazioni raccolte dai
documenti del suo condominio (ciascuna con la fonte: documento e pagine).

Confeziona UNA risposta:
- Usa solo le informazioni raccolte; non inventare e non aggiungere conoscenza esterna.
- Cita le fonti tra parentesi alla fine delle affermazioni rilevanti, es. "(Bilancio consuntivo 2025, pp. 58-93)".
- Se le informazioni raccolte non rispondono alla domanda, dillo onestamente e aggiungi che prenderai
  nota e l'amministratore ricontatterà il condomino. Non improvvisare una risposta.
- Sii sintetico: vai al punto."""


def componi(client: OpenAI, domanda: str, contesto: str, trace=None) -> str:
    user = f"DOMANDA DEL CONDOMINO:\n{domanda}\n\nINFORMAZIONI RACCOLTE:\n{contesto}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": f"{FINAL_SYSTEM}\n\n{contesto_temporale()}{istruzioni.blocco_prompt()}"},
            {"role": "user", "content": user},
        ],
        reasoning_effort=EFFORT,
        max_completion_tokens=int(os.getenv("AGENTE_FINALE_MAX_TOKENS", "3000")),
    )
    out = (resp.choices[0].message.content or "").strip()
    if not out:
        logger.warning("Composizione finale: output vuoto (finish_reason=%s)", resp.choices[0].finish_reason)
    _rec(trace, "Composizione finale", user, out)
    return out


# ---------- Orchestrazione ----------

def rispondi(db: Session, condominio_id: int, domanda: str, trace=None) -> dict:
    """Esegue l'intero flusso. Ritorna:
      {"risposta": str,
       "piano": {"ragionamento": str, "query": [...]},
       "passi": [ {documento, sezione, pagine, sotto_domanda, trovato, risposta, citazione} ],
       "traccia": [ {fase, modello, input, output} ],
       "errore": str|None}
    Se `trace` è una lista, le chiamate LLM vi vengono accodate (per persistenza).
    Non solleva: incapsula gli errori.
    """
    if trace is None:
        trace = []

    def _out(**kw):
        kw.setdefault("piano", None)
        kw.setdefault("passi", [])
        kw["traccia"] = trace
        return kw

    condominio = db.get(Condominio, condominio_id)
    if not condominio:
        return _out(risposta="Condominio non trovato.", errore="not_found")

    domanda = (domanda or "").strip()
    if not domanda:
        return _out(risposta="Scrivi una domanda.", errore="empty")

    catalogo, mappa = _catalogo(condominio)
    if not mappa:
        return _out(
            risposta="Non ci sono ancora documenti indicizzati per questo condominio: carica e attendi "
                     "l'indicizzazione, poi riprova.",
            errore="no_docs",
        )

    try:
        client = _client()
    except RuntimeError as e:
        return _out(risposta="Servizio non disponibile.", errore=str(e))

    # Stadio 1: pianificazione.
    try:
        piano = pianifica(client, domanda, catalogo, trace=trace)
    except Exception as e:
        logger.error("Pianificatore fallito: %s", e)
        return _out(risposta="Si è verificato un errore nell'analisi della domanda.", errore=f"planner: {e}")

    query = [q for q in piano.get("query", []) if q.get("sezione_id") in mappa][:MAX_SEZIONI]

    # Stadio 2: interrogazione per-sezione.
    passi = []
    for q in query:
        sez = mappa[q["sezione_id"]]
        try:
            res = interroga_sezione(client, q["sotto_domanda"], sez, trace=trace)
        except Exception as e:
            logger.error("Interrogazione sezione %s fallita: %s", sez.id, e)
            continue
        passi.append({
            "documento_id": sez.documento.id,
            "documento": sez.documento.nome_file,
            "sezione": sez.titolo,
            "pagine": f"{sez.page_start}-{sez.page_end}",
            "sotto_domanda": q["sotto_domanda"],
            "trovato": bool(res.get("trovato")),
            "risposta": res.get("risposta", ""),
            "citazione": res.get("citazione", ""),
        })

    # Stadio 3: composizione finale.
    trovati = [p for p in passi if p["trovato"] and (p["risposta"] or "").strip()]
    if trovati:
        contesto = "\n\n".join(
            f"FONTE: {p['documento']}, pp. {p['pagine']} ({p['sezione']})\n"
            f"Risposta: {p['risposta']}\nCitazione: «{p['citazione']}»"
            for p in trovati
        )
    else:
        contesto = "(Nessuna informazione pertinente trovata nei documenti del condominio.)"

    try:
        risposta = componi(client, domanda, contesto, trace=trace)
    except Exception as e:
        logger.error("Composizione finale fallita: %s", e)
        risposta = "Si è verificato un errore nella generazione della risposta."

    return _out(
        risposta=risposta,
        piano={"ragionamento": piano.get("ragionamento", ""), "query": query},
        passi=passi,
        errore=None,
    )


def formatta_accessi(esito: dict) -> str:
    """Log leggibile di cosa ha consultato il risponditore: quali sezioni di quali
    documenti, e con quale esito. Usato nelle trascrizioni WhatsApp e voce."""
    piano = esito.get("piano") or {}
    passi = esito.get("passi") or []
    righe = []
    rag = (piano.get("ragionamento") or "").strip()
    if rag:
        righe.append(f"Piano: {rag}")
    if passi:
        righe.append("Sezioni consultate:")
        for p in passi:
            esito_p = "✓ trovato" if p.get("trovato") else "✗ niente"
            righe.append(f"• «{p.get('documento')}» › {p.get('sezione')} "
                         f"(pp. {p.get('pagine')}) → {esito_p}")
    else:
        motivo = esito.get("errore")
        if motivo == "no_docs":
            righe.append("Nessuna sezione consultata: il condominio non ha documenti indicizzati.")
        else:
            righe.append("Nessuna sezione consultata (nessun documento pertinente individuato).")
    return "\n".join(righe)
