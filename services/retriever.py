"""Agente retriever sui documenti caricati — indipendente dal risponditore.

Riceve una domanda la cui risposta sta in uno o più documenti. Lavora in 2 chiamate LLM:

1. PIANIFICATORE — nella sua context window vede il CATALOGO di tutti i documenti:
   - titolo (nome file), categoria e anno di ogni documento;
   - per i PDF, l'indicizzazione generata dall'LLM in fase di ingestion
     (titolo di ogni sezione + riassunto);
   - per Excel/CSV (e altri file caricati per intero) le prime righe del contenuto;
   - le note in testo libero che l'amministratore ha scritto per ogni categoria.
   In base a questo PIANIFICA quali documenti/sezioni servono e ne restituisce gli id.

2. RISPOSTA — seconda chiamata LLM che ha nella context window il CONTENUTO INTEGRALE
   delle sezioni selezionate (più le note di categoria) e genera la risposta finale.

Testabile in isolamento via `rispondi(db, domanda)`; la GUI (chat nella pagina
Documenti) la usa per provarlo. Non è collegato al risponditore WhatsApp/voce.
"""

import json
import logging
import os

from openai import OpenAI
from sqlalchemy.orm import Session

from database import Documento, Sezione, TestoCategoria
from services.contesto import contesto_temporale
from services import istruzioni

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("RETRIEVER_MODEL", "gpt-5-mini")
EFFORT = os.getenv("RETRIEVER_EFFORT", "low")

MAX_SEZIONI = 8               # tetto di sezioni recuperabili per domanda
MAX_SECTION_CHARS = 120000    # cap di sicurezza sul contenuto passato per sezione
ANTEPRIMA_RIGHE = 5           # righe di anteprima per i file tabellari (Excel/CSV)
ANTEPRIMA_RIGA_CAP = 300      # cap di lunghezza per riga di anteprima
TRACE_CAP = 8000              # cap per campo input/output salvato nella traccia


def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY non configurata nel .env.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _rec(trace, fase: str, input_text: str, output_text: str):
    if trace is None:
        return
    trace.append({
        "fase": fase,
        "modello": MODEL,
        "input": (input_text or "")[:TRACE_CAP],
        "output": (output_text or "")[:TRACE_CAP],
    })


# ---------- Catalogo per il pianificatore ----------

def _prime_righe(testo: str, n: int) -> str:
    """Prime `n` righe non vuote del testo, indentate e cappate (anteprima file tabellari)."""
    righe = []
    for r in (testo or "").splitlines():
        r = r.rstrip()
        if not r.strip():
            continue
        righe.append("      " + r[:ANTEPRIMA_RIGA_CAP])
        if len(righe) >= n:
            break
    return "\n".join(righe) if righe else "      (vuoto)"


def _is_pdf(doc: Documento) -> bool:
    return (doc.nome_file or "").lower().endswith(".pdf")


def _catalogo(db: Session) -> tuple[str, dict[int, Sezione]]:
    """Testo del catalogo dei documenti + mappa sezione_id -> Sezione.

    Solo documenti con contenuto disponibile (almeno una sezione). I PDF mostrano
    l'indice (sezioni + summary); gli altri file (Excel/CSV/testo) mostrano le prime righe.
    """
    righe = []
    mappa: dict[int, Sezione] = {}
    docs = (
        db.query(Documento)
        .order_by(Documento.categoria, Documento.caricato_at)
        .all()
    )
    for doc in docs:
        if not doc.sezioni:
            continue
        anno = doc.anno if doc.anno else "n/d"
        righe.append(
            f"\n# DOCUMENTO «{doc.nome_file}» (categoria: {doc.categoria}, anno: {anno})"
        )
        if _is_pdf(doc):
            for s in doc.sezioni:
                mappa[s.id] = s
                tab = " [contiene tabelle]" if s.contiene_tabelle else ""
                righe.append(
                    f"  - sezione_id={s.id} | pp. {s.page_start}-{s.page_end}{tab} | {s.titolo}\n"
                    f"      {s.summary or ''}"
                )
        else:
            # Excel/CSV/testo: una sola sezione = il documento intero. Mostra le prime righe.
            s = doc.sezioni[0]
            mappa[s.id] = s
            righe.append(
                f"  - sezione_id={s.id} | documento intero (caricato senza sezionamento). "
                f"Prime {ANTEPRIMA_RIGHE} righe:\n{_prime_righe(s.content_md, ANTEPRIMA_RIGHE)}"
            )
    return "\n".join(righe).strip(), mappa


def _note_categorie(db: Session) -> str:
    """Note in testo libero scritte dall'amministratore per le categorie (non vuote)."""
    out = []
    for t in db.query(TestoCategoria).order_by(TestoCategoria.categoria).all():
        if t.testo and t.testo.strip():
            out.append(f"# Categoria «{t.categoria}»:\n{t.testo.strip()}")
    return "\n\n".join(out).strip()


# ---------- Stadio 1: pianificatore ----------

PLANNER_SYSTEM = """Sei il pianificatore di un agente che risponde a domande consultando i documenti
caricati. Ricevi una domanda e il CATALOGO dei documenti disponibili: per ogni documento vedi il
titolo, la categoria e — per i PDF — l'indice delle sezioni (titolo + riassunto di cosa contiene e a
quali domande risponde); per i file tabellari (Excel/CSV) e testuali vedi le prime righe. Vedi anche
le note che l'amministratore ha scritto per ciascuna categoria.

Il tuo compito: scegliere quali sezioni (una o più) contengono con buona probabilità le informazioni
per rispondere. NON rispondi alla domanda: pianifichi soltanto.

Regole:
- Restituisci i sezione_id delle sezioni utili, scegliendo solo quelle davvero pertinenti
  (di norma 1-4; al massimo 8). Nel dubbio tra due sezioni simili, includile entrambe.
- Usa esclusivamente i sezione_id presenti nel catalogo.
- Se nessuna sezione è pertinente, restituisci una lista "sezioni" vuota."""

PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "ragionamento": {"type": "string"},
        "sezioni": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["ragionamento", "sezioni"],
    "additionalProperties": False,
}


def pianifica(client: OpenAI, domanda: str, catalogo: str, note: str, trace=None) -> dict:
    blocco_note = f"\n\nNOTE DELL'AMMINISTRATORE PER CATEGORIA:\n{note}" if note else ""
    user = (
        f"DOMANDA:\n{domanda}\n\n"
        f"CATALOGO DEI DOCUMENTI:\n{catalogo}{blocco_note}"
    )
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


# ---------- Stadio 2: risposta ----------

ANSWER_SYSTEM = """Sei un servizio di retrieval. La tua risposta NON è letta da un umano: la riceve
un AGENTE TELEFONICO che la riformulerà a voce al cliente. Conta solo il contenuto utile.

OBIETTIVO: massima densità di informazione, minimo numero di token in output. La latenza è critica,
quindi ogni parola in più costa tempo all'agente: sii il più breve possibile.

Regole:
- Rispondi SUBITO con i fatti. NIENTE saluti ("buongiorno"), NIENTE intercalari ("un attimo",
  "certo"), NIENTE preamboli o frasi di cortesia, NIENTE riformulazione della domanda. Solo
  l'informazione che serve, in forma essenziale.
- Usa solo ciò che è nei documenti forniti (e nelle note dell'amministratore); non inventare.
- Cita la fonte in parentesi in modo COMPATTO e solo se utile a identificare il documento
  (es. "(natys_gin_tonica.pdf, p.15)"). Una sola volta, non ripetuta.
- Se i documenti non contengono la risposta, di' solo, in poche parole, che l'informazione non è
  disponibile nei documenti."""


def componi(client: OpenAI, domanda: str, contesto: str, trace=None) -> str:
    user = f"DOMANDA:\n{domanda}\n\nCONTENUTO DEI DOCUMENTI SELEZIONATI:\n{contesto}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": f"{ANSWER_SYSTEM}\n\n{contesto_temporale()}{istruzioni.blocco_prompt()}"},
            {"role": "user", "content": user},
        ],
        reasoning_effort=EFFORT,
        max_completion_tokens=int(os.getenv("RETRIEVER_RISPOSTA_MAX_TOKENS", "4000")),
    )
    out = (resp.choices[0].message.content or "").strip()
    if not out:
        logger.warning("Risposta retriever: output vuoto (finish_reason=%s)", resp.choices[0].finish_reason)
    _rec(trace, "Risposta", user, out)
    return out


# ---------- Orchestrazione ----------

def _fonte_label(doc: Documento, sez: Sezione) -> str:
    if _is_pdf(doc):
        return f"{doc.nome_file}, pp. {sez.page_start}-{sez.page_end} ({sez.titolo})"
    return doc.nome_file


def rispondi_vettoriale(db: Session, domanda: str, categoria: str | None = None, k: int = 6,
                        trace=None) -> dict:
    """Retriever SEMANTICO: embedda la domanda, prende i top-K chunk per similarità coseno e fa
    rispondere l'LLM solo su quelli (con citazioni). Ritorna:
      {risposta, chunk: [{score, documento, categoria, pagine, estratto}], fonti: [...], traccia}.
    """
    from services import vettore
    if trace is None:
        trace = []

    def _out(**kw):
        kw.setdefault("chunk", [])
        kw.setdefault("fonti", [])
        kw["traccia"] = trace
        return kw

    domanda = (domanda or "").strip()
    if not domanda:
        return _out(risposta="Scrivi una domanda.", errore="empty")

    try:
        risultati = vettore.cerca(db, domanda, k=k, categoria=categoria)
    except Exception as e:
        logger.error("Ricerca vettoriale fallita: %s", e)
        return _out(risposta="Errore nella ricerca.", errore=str(e))

    if not risultati:
        return _out(risposta="Non ho trovato nulla di pertinente nei documenti indicizzati.",
                    errore="no_match")

    # Contesto per lo stadio risposta + tracce/fonti per la UI.
    parti, fonti, viste = [], [], set()
    for r in risultati:
        etichetta = r["documento"] + (f", pp. {r['pagine']}" if r.get("pagine") else "")
        parti.append(f"FONTE: {etichetta}\n{r['testo']}")
        if r["documento_id"] not in viste:
            viste.add(r["documento_id"])
            fonti.append({"documento_id": r["documento_id"], "documento": r["documento"],
                          "categoria": r["categoria"], "pagine": r.get("pagine"),
                          "inviabile": r.get("inviabile", True)})
    contesto = "\n\n---\n\n".join(parti)

    try:
        client = _client()
        risposta = componi(client, domanda, contesto, trace=trace)
    except Exception as e:
        logger.error("Risposta retriever (vett.) fallita: %s", e)
        risposta = "Errore nella generazione della risposta."

    chunk = [{"score": r["score"], "documento_id": r["documento_id"], "documento": r["documento"],
              "categoria": r["categoria"], "pagine": r.get("pagine"), "inviabile": r.get("inviabile", True),
              "estratto": (r["testo"][:300] + ("…" if len(r["testo"]) > 300 else ""))}
             for r in risultati]
    return _out(risposta=risposta, chunk=chunk, fonti=fonti, errore=None)


TAB_PLAN_SYSTEM = """Sei un servizio che interroga TABELLE strutturate (CSV/Excel). Ricevi una domanda
e lo SCHEMA delle tabelle: per ogni colonna vedi tipo e facet.

REGOLE DEI FILTRI (tassative):
- '=' o 'in' (valore esatto): SOLO su colonne con "valori AMMESSI (lista COMPLETA)". Mappa il termine
  del cliente sul valore canonico della lista (es. "succhi" -> "Succhi").
- '<','<=','>','>=' (intervalli): SOLO su colonne numeriche.
- 'contains' (sottostringa, case-insensitive): per colonne testo marcate "CAMPIONE NON esaustivo".
  NON usare MAI '=' su queste colonne (non conosci tutti i valori).
Scegli UNA tabella (documento_id) pertinente e costruisci i filtri. Usa order_by/ascending/limit se
utile (es. "il più economico" -> order_by sul prezzo, ascending=true, limit=1). I valori vanno come
stringa. Se NESSUNA tabella è pertinente alla domanda, metti documento_id = 0. Non rispondere alla
domanda: produci solo la query."""

TAB_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "ragionamento": {"type": "string"},
        "documento_id": {"type": "integer"},
        "filtri": {"type": "array", "items": {
            "type": "object",
            "properties": {"campo": {"type": "string"}, "op": {"type": "string"}, "valore": {"type": "string"}},
            "required": ["campo", "op", "valore"], "additionalProperties": False}},
        "order_by": {"type": "string"},
        "ascending": {"type": "boolean"},
        "limit": {"type": "integer"},
    },
    "required": ["ragionamento", "documento_id", "filtri", "order_by", "ascending", "limit"],
    "additionalProperties": False,
}


def rispondi_tabellare(db: Session, domanda: str, trace=None) -> dict:
    """Interroga le TABELLE strutturate (CSV/Excel): l'LLM costruisce una query rispettando i facet
    (filtro esatto solo su colonne esaustive, range su numeri, 'contains' sulle altre), poi filtra le
    righe e compone la risposta. Ritorna {risposta, righe, query, errore, traccia}."""
    from services import tabellare
    if trace is None:
        trace = []
    schema = tabellare.schema_prompt(db)
    if not schema:
        return {"risposta": "", "righe": [], "query": None, "errore": "no_tables", "traccia": trace}
    try:
        client = _client()
    except RuntimeError as e:
        return {"risposta": "Servizio non disponibile.", "righe": [], "query": None, "errore": str(e), "traccia": trace}

    user = f"DOMANDA:\n{domanda}\n\nSCHEMA TABELLE:\n{schema}"
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": f"{TAB_PLAN_SYSTEM}\n\n{contesto_temporale()}"},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "query", "strict": True, "schema": TAB_PLAN_SCHEMA}},
            reasoning_effort=EFFORT, max_completion_tokens=2000,
        )
        piano = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        logger.error("Pianificatore tabellare fallito: %s", e)
        return {"risposta": "Errore nell'analisi della domanda.", "righe": [], "query": None,
                "errore": str(e), "traccia": trace}
    _rec(trace, "Query tabellare", user, json.dumps(piano, ensure_ascii=False))

    did = int(piano.get("documento_id") or 0)
    if not did:
        return {"risposta": "", "righe": [], "query": piano, "errore": "no_match", "traccia": trace}
    righe = tabellare.interroga(db, did, piano.get("filtri", []), piano.get("order_by") or None,
                                bool(piano.get("ascending", True)), int(piano.get("limit") or 20))
    contesto = json.dumps(righe[:30], ensure_ascii=False)
    risposta = componi(client, domanda, f"RIGHE RISULTANTI DALLA TABELLA (rispondi sinteticamente):\n{contesto}",
                       trace=trace)
    return {"risposta": risposta, "righe": righe[:30], "query": piano, "errore": None, "traccia": trace}


# ---------- Retriever AGNOSTICO: un router decide tabella vs documenti ----------

ROUTER_SYSTEM = """Sei il ROUTER di un servizio di retrieval. Decidi DOVE sta la risposta a una domanda:
- "tabella": dati strutturati (CSV/Excel: prezzi, disponibilità, formati, anagrafiche, record precisi).
- "documenti": testi/PDF (condizioni di vendita, FAQ, descrizioni, spiegazioni).
- "nessuna": nessuna fonte pertinente.
Vedi l'INDICE DOCUMENTI e gli SCHEMI TABELLE (con i facet di ogni colonna).

Se fonte="tabella": scegli documento_id e costruisci i filtri rispettando i FACET:
- '=' o 'in' (valore esatto): SOLO su colonne con "valori AMMESSI (lista COMPLETA)". Mappa il termine
  del cliente sul valore canonico (es. "succhi" -> "succhi").
- '<','<=','>','>=': SOLO su colonne numeriche. Usa la SOGLIA numerica ESATTA detta dal cliente
  (se dice "sotto i 2 euro" il valore è 2, non un altro numero).
- 'contains' (sottostringa): sulle colonne testo "CAMPIONE NON esaustivo". MAI '=' su queste.
- order_by/ascending/limit se utile ("il più economico" -> order_by sul prezzo, ascending=true, limit=1).
Se fonte="documenti" o "nessuna": documento_id=0 e filtri=[].
Produci solo il JSON; non rispondere alla domanda."""

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "ragionamento": {"type": "string"},
        "fonte": {"type": "string", "enum": ["tabella", "documenti", "nessuna"]},
        "documento_id": {"type": "integer"},
        "filtri": {"type": "array", "items": {
            "type": "object",
            "properties": {"campo": {"type": "string"}, "op": {"type": "string"}, "valore": {"type": "string"}},
            "required": ["campo", "op", "valore"], "additionalProperties": False}},
        "order_by": {"type": "string"},
        "ascending": {"type": "boolean"},
        "limit": {"type": "integer"},
    },
    "required": ["ragionamento", "fonte", "documento_id", "filtri", "order_by", "ascending", "limit"],
    "additionalProperties": False,
}


def _indice_documenti(db: Session) -> str:
    """Indice compatto dei documenti (PDF) per il router: nome, categoria e riassunto/titoli."""
    docs = db.query(Documento).filter(Documento.nome_file.ilike("%.pdf")).all()
    righe = []
    for d in docs:
        if not d.sezioni:
            continue
        descr = (d.riassunto or "").strip() or "; ".join(s.titolo for s in d.sezioni[:5])
        righe.append(f"- «{d.nome_file}» (categoria {d.categoria}): {descr[:300]}")
    return "\n".join(righe)


def cerca(db: Session, domanda: str, categoria: str | None = None, trace=None) -> dict:
    """Retriever AGNOSTICO: un router-planner decide se la risposta sta in una TABELLA (CSV/Excel) o
    nei DOCUMENTI (PDF) e instrada di conseguenza. Una sola chiamata di routing + una di risposta.
    Ritorna {risposta, fonte, fonti, chunk, righe, query, errore, traccia}."""
    from services import tabellare
    if trace is None:
        trace = []

    def _out(**kw):
        kw.setdefault("fonte", "nessuna"); kw.setdefault("fonti", []); kw.setdefault("chunk", [])
        kw.setdefault("righe", []); kw.setdefault("query", None); kw["traccia"] = trace
        return kw

    domanda = (domanda or "").strip()
    if not domanda:
        return _out(risposta="Scrivi una domanda.", errore="empty")
    schema = tabellare.schema_prompt(db)
    indice = _indice_documenti(db)
    if not schema and not indice:
        return _out(risposta="Non ci sono ancora documenti o dati consultabili.", errore="no_sources")
    try:
        client = _client()
    except RuntimeError as e:
        return _out(risposta="Servizio non disponibile.", errore=str(e))

    user = (f"DOMANDA:\n{domanda}\n\nINDICE DOCUMENTI:\n{indice or '(nessuno)'}"
            f"\n\nSCHEMI TABELLE:\n{schema or '(nessuna)'}")
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": f"{ROUTER_SYSTEM}\n\n{contesto_temporale()}"},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "route", "strict": True, "schema": ROUTER_SCHEMA}},
            reasoning_effort=EFFORT, max_completion_tokens=2000,
        )
        piano = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        logger.error("Router retriever fallito: %s", e)
        return _out(risposta="Errore nell'analisi della domanda.", errore=str(e))
    _rec(trace, "Router", user, json.dumps(piano, ensure_ascii=False))

    fonte = piano.get("fonte")
    did = int(piano.get("documento_id") or 0)
    if fonte == "tabella" and did:
        righe = tabellare.interroga(db, did, piano.get("filtri", []), piano.get("order_by") or None,
                                    bool(piano.get("ascending", True)), int(piano.get("limit") or 20))
        # NIENTE LLM sui dati: i valori restano ESATTAMENTE quelli del CSV (rendering deterministico).
        return _out(risposta=tabellare.formatta_righe(righe), fonte="tabella", righe=righe[:30],
                    query=piano, errore=None)
    if fonte == "documenti":
        ris = rispondi_vettoriale(db, domanda, categoria=categoria, trace=trace)
        return _out(risposta=ris.get("risposta", ""), fonte="documenti", fonti=ris.get("fonti", []),
                    chunk=ris.get("chunk", []), query=piano, errore=ris.get("errore"))
    return _out(risposta="Non disponibile nei documenti né nei dati a disposizione.",
                fonte="nessuna", query=piano, errore=None)


def rispondi(db: Session, domanda: str, trace=None) -> dict:
    """Esegue l'intero flusso del retriever. Ritorna:
      {"risposta": str,
       "piano": {"ragionamento": str, "sezioni": [int]},
       "fonti": [ {documento_id, documento, sezione, pagine, is_pdf} ],
       "traccia": [ {fase, modello, input, output} ],
       "errore": str|None}
    Non solleva: incapsula gli errori nello stato.
    """
    if trace is None:
        trace = []

    def _out(**kw):
        kw.setdefault("piano", None)
        kw.setdefault("fonti", [])
        kw["traccia"] = trace
        return kw

    domanda = (domanda or "").strip()
    if not domanda:
        return _out(risposta="Scrivi una domanda.", errore="empty")

    catalogo, mappa = _catalogo(db)
    note = _note_categorie(db)
    if not mappa and not note:
        return _out(
            risposta="Non ci sono ancora documenti consultabili: carica documenti (e attendi "
                     "l'indicizzazione dei PDF), poi riprova.",
            errore="no_docs",
        )

    try:
        client = _client()
    except RuntimeError as e:
        return _out(risposta="Servizio non disponibile.", errore=str(e))

    # Stadio 1: pianificazione.
    try:
        piano = pianifica(client, domanda, catalogo, note, trace=trace)
    except Exception as e:
        logger.error("Pianificatore retriever fallito: %s", e)
        return _out(risposta="Si è verificato un errore nell'analisi della domanda.", errore=f"planner: {e}")

    ids = []
    for sid in piano.get("sezioni", []):
        if sid in mappa and sid not in ids:
            ids.append(sid)
    ids = ids[:MAX_SEZIONI]

    # Stadio 2: assemblaggio contesto + risposta.
    parti = []
    if note:
        parti.append(f"NOTE DELL'AMMINISTRATORE PER CATEGORIA:\n{note}")
    fonti = []
    for sid in ids:
        sez = mappa[sid]
        doc = sez.documento
        contenuto = (sez.content_md or "")[:MAX_SECTION_CHARS]
        parti.append(f"FONTE: {_fonte_label(doc, sez)}\n{contenuto}")
        fonti.append({
            "documento_id": doc.id,
            "documento": doc.nome_file,
            "sezione": sez.titolo,
            "pagine": f"{sez.page_start}-{sez.page_end}" if _is_pdf(doc) else None,
            "is_pdf": _is_pdf(doc),
        })

    if not parti:
        contesto = "(Nessun documento pertinente individuato.)"
    else:
        contesto = "\n\n---\n\n".join(parti)

    try:
        risposta = componi(client, domanda, contesto, trace=trace)
    except Exception as e:
        logger.error("Risposta retriever fallita: %s", e)
        risposta = "Si è verificato un errore nella generazione della risposta."

    return _out(
        risposta=risposta,
        piano={"ragionamento": piano.get("ragionamento", ""), "sezioni": ids},
        fonti=fonti,
        errore=None,
    )
