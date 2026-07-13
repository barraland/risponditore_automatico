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
from contextvars import copy_context

from openai import OpenAI
from sqlalchemy.orm import Session

from database import Documento, Sezione, TestoCategoria
from services.contesto import contesto_temporale
from services import istruzioni
from services import perf

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("RETRIEVER_MODEL", "gpt-5-mini")
EFFORT = os.getenv("RETRIEVER_EFFORT", "low")
# Il ROUTER è una classificazione: un modello NON-reasoning veloce (gpt-4.1-mini) è più che
# sufficiente ed evita la latenza del "ragionamento". RETRIEVER_ROUTER_EFFORT resta vuoto perché i
# modelli gpt-4.1 NON accettano reasoning_effort; valorizzalo solo se torni a un modello gpt-5/o*.
ROUTER_MODEL = os.getenv("RETRIEVER_ROUTER_MODEL", "gpt-4.1-mini")
ROUTER_EFFORT = os.getenv("RETRIEVER_ROUTER_EFFORT", "")

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
        if getattr(doc, "sempre_contesto", False):
            continue  # 'Sempre presente': fuori dal retriever (già iniettato fisso nel prompt)
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
                        trace=None, azienda_id: int | None = None, qemb=None,
                        sintetizza: bool = True) -> dict:
    """Retriever SEMANTICO: embedda la domanda, prende i top-K chunk per similarità coseno e fa
    rispondere l'LLM solo su quelli (con citazioni). Ritorna:
      {risposta, chunk: [{score, documento, categoria, pagine, estratto}], fonti: [...], traccia}.
    `qemb`: embedding della domanda già calcolato (es. in parallelo al router); se assente lo calcola.
    `sintetizza`: se False NON chiama la 2ª LLM (componi): `risposta` diventa i chunk grezzi con la
    fonte, che il chiamante (es. l'agente vocale) elaborerà nel proprio turno — un round-trip in meno.
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
        risultati = vettore.cerca(db, domanda, k=k, categoria=categoria, azienda_id=azienda_id, qemb=qemb)
    except Exception as e:
        perf.mark(f"✗ retrieve vettoriale ERRORE: {e}")
        logger.error("Ricerca vettoriale fallita: %s", e)
        return _out(risposta="Errore nella ricerca.", errore=str(e))
    perf.mark(f"retrieve VETTORIALE fatto ({len(risultati)} chunk)")

    if not risultati:
        return _out(risposta="Non ho trovato nulla di pertinente nei documenti indicizzati.",
                    errore="no_match")

    # Contesto per lo stadio risposta + tracce/fonti per la UI. (Le note per-file vengono aggiunte
    # in coda alla risposta finale da `cerca`, così non si perdono anche senza sintesi.)
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

    if sintetizza:
        perf.mark(f"→ chiamata LLM risposta (contesto {len(contesto)} char)")
        try:
            client = _client()
            risposta = componi(client, domanda, contesto, trace=trace)
        except Exception as e:
            perf.mark(f"✗ LLM risposta ERRORE: {e}")
            logger.error("Risposta retriever (vett.) fallita: %s", e)
            risposta = "Errore nella generazione della risposta."
        perf.mark("← LLM risposta pronta")
    else:
        # Niente 2ª LLM: ritorna gli estratti grezzi (con fonte). Li elabora il chiamante.
        risposta = contesto
        _rec(trace, "Risposta (chunk grezzi, no LLM)", domanda, risposta)
        perf.mark(f"sintesi LLM SALTATA — ritorno {len(risultati)} chunk grezzi ({len(contesto)} char)")

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

def _blocco_note_documenti(db: Session, fonti: list) -> str:
    """Note interpretative per-file (scritte dall'admin) dei documenti/tabelle CITATI, da APPENDERE
    in coda alla risposta: così arrivano all'agente anche quando il retriever gira SENZA sintesi
    (nessuna 2ª LLM). Stringa vuota se nessuna fonte ha una nota."""
    ids = [f.get("documento_id") for f in (fonti or []) if f.get("documento_id")]
    if not ids:
        return ""
    righe = []
    for nome, nt in db.query(Documento.nome_file, Documento.note).filter(Documento.id.in_(ids)).all():
        if (nt or "").strip():
            righe.append(f"- {nome}: {nt.strip()}")
    if not righe:
        return ""
    return ("\n\nNOTE SUI DOCUMENTI (indicazioni dell'amministratore su come leggerli/rispondere — "
            "tienile presenti nella risposta):\n" + "\n".join(righe))


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


def _indice_documenti(db: Session, azienda_id: int | None = None) -> str:
    """Indice compatto dei documenti (PDF) del tenant per il router: nome, categoria e riassunto."""
    q = db.query(Documento).filter(Documento.nome_file.ilike("%.pdf"))
    if azienda_id:
        q = q.filter(Documento.azienda_id == azienda_id)
    docs = q.all()
    righe = []
    for d in docs:
        if not d.sezioni:
            continue
        descr = (d.riassunto or "").strip() or "; ".join(s.titolo for s in d.sezioni[:5])
        riga = f"- «{d.nome_file}» (categoria {d.categoria}): {descr[:300]}"
        nota = (getattr(d, "note", "") or "").strip()
        if nota:
            riga += f"\n    NOTA: {nota}"   # nota interpretativa scritta dall'admin per questo file
        righe.append(riga)
    return "\n".join(righe)


def cerca(db: Session, domanda: str, categoria: str | None = None, trace=None,
          azienda_id: int | None = None, sintetizza: bool = True) -> dict:
    """Retriever AGNOSTICO (ristretto ai documenti del tenant): un router-planner decide se la
    risposta sta in una TABELLA (CSV/Excel) o nei DOCUMENTI (PDF) e instrada. Una sola chiamata di
    routing + una di risposta. Ritorna {risposta, fonte, fonti, chunk, righe, query, errore, traccia}.
    `sintetizza`: se False, sul ramo documenti NON chiama la 2ª LLM e ritorna i chunk grezzi (con
    fonte) in `risposta` — l'agente chiamante li elabora nel suo turno (un round-trip in meno)."""
    from services import tabellare
    if trace is None:
        trace = []

    def _out(**kw):
        kw.setdefault("fonte", "nessuna"); kw.setdefault("fonti", []); kw.setdefault("chunk", [])
        kw.setdefault("righe", []); kw.setdefault("query", None); kw["traccia"] = trace
        return kw

    domanda = (domanda or "").strip()
    perf.start(f"cerca (tenant={azienda_id}) q={domanda[:60]!r}")  # ⏱️ richiesta arrivata
    if not domanda:
        return _out(risposta="Scrivi una domanda.", errore="empty")
    schema = tabellare.schema_prompt(db, azienda_id)
    perf.mark(f"schema tabelle costruito ({len(schema)} char)")
    indice = _indice_documenti(db, azienda_id)
    perf.mark(f"indice documenti costruito ({len(indice)} char)")
    if not schema and not indice:
        return _out(risposta="Non ci sono ancora documenti o dati consultabili.", errore="no_sources")
    try:
        client = _client()
    except RuntimeError as e:
        return _out(risposta="Servizio non disponibile.", errore=str(e))

    # Embedding della domanda IN PARALLELO al router: sono indipendenti. Se poi la fonte è "tabella"
    # l'avremo calcolato invano (poco male); se è "documenti" ne abbiamo già il risultato pronto.
    import concurrent.futures
    from services import vettore
    ctx = copy_context()

    def _embed_parallelo():
        try:
            return vettore.embed_uno(domanda)
        except Exception as e:
            logger.warning("Embedding parallelo fallito: %s", e)
            return None

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    emb_future = pool.submit(ctx.run, _embed_parallelo)
    perf.mark("→ embedding domanda avviato in PARALLELO al router")

    user = (f"DOMANDA:\n{domanda}\n\nINDICE DOCUMENTI:\n{indice or '(nessuno)'}"
            f"\n\nSCHEMI TABELLE:\n{schema or '(nessuna)'}")
    perf.mark(f"→ chiamata LLM router (model={ROUTER_MODEL}, effort={ROUTER_EFFORT or 'n/a'}, prompt={len(user)} char)")
    router_kwargs = dict(
        model=ROUTER_MODEL,
        messages=[{"role": "system", "content": f"{ROUTER_SYSTEM}\n\n{contesto_temporale()}"},
                  {"role": "user", "content": user}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "route", "strict": True, "schema": ROUTER_SCHEMA}},
        max_completion_tokens=2000,
    )
    if ROUTER_EFFORT:  # solo per i modelli reasoning (gpt-5/o*); i gpt-4.1 lo rifiutano
        router_kwargs["reasoning_effort"] = ROUTER_EFFORT
    try:
        resp = client.chat.completions.create(**router_kwargs)
        piano = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        pool.shutdown(wait=False)
        perf.mark(f"✗ LLM router ERRORE: {e}")
        logger.error("Router retriever fallito: %s", e)
        return _out(risposta="Errore nell'analisi della domanda.", errore=str(e))
    _rec(trace, "Router", user, json.dumps(piano, ensure_ascii=False))

    fonte = piano.get("fonte")
    did = int(piano.get("documento_id") or 0)
    perf.mark(f"← LLM router: PIANO pronto (fonte={fonte}, doc={did}, filtri={len(piano.get('filtri', []))})")
    if fonte == "tabella" and did:
        pool.shutdown(wait=False)  # embedding parallelo scartato: la risposta sta nella tabella
        righe = tabellare.interroga(db, did, piano.get("filtri", []), piano.get("order_by") or None,
                                    bool(piano.get("ascending", True)), int(piano.get("limit") or 20))
        perf.mark(f"retrieve TABELLARE fatto ({len(righe)} righe)")
        # Esponi la fonte (il file CSV) con documento_id + inviabile: così l'agente può inviarlo
        # con invia_documento. NIENTE LLM sui dati: valori esatti (rendering deterministico).
        doc = db.get(Documento, did)
        fonti = ([{"documento_id": did, "documento": doc.nome_file, "categoria": doc.categoria,
                   "pagine": None, "inviabile": bool(doc.inviabile)}] if doc else [])
        perf.mark("✓ FINE (fonte=tabella)")
        risposta_tab = tabellare.formatta_righe(righe) + _blocco_note_documenti(db, fonti)
        return _out(risposta=risposta_tab, fonte="tabella", righe=righe[:30],
                    fonti=fonti, query=piano, errore=None)
    if fonte == "documenti":
        qemb = emb_future.result()  # già pronto (calcolato durante il router): attesa ~0
        pool.shutdown(wait=False)
        perf.mark("embedding (parallelo) recuperato" + ("" if qemb else " — FALLITO, ricalcolo in-linea"))
        ris = rispondi_vettoriale(db, domanda, categoria=categoria, trace=trace,
                                  azienda_id=azienda_id, qemb=qemb, sintetizza=sintetizza)
        perf.mark("✓ FINE (fonte=documenti)")
        # Note per-file APPESE alla risposta: garantite anche senza sintesi.
        risposta_doc = (ris.get("risposta", "") or "") + _blocco_note_documenti(db, ris.get("fonti", []))
        return _out(risposta=risposta_doc, fonte="documenti", fonti=ris.get("fonti", []),
                    chunk=ris.get("chunk", []), query=piano, errore=ris.get("errore"))
    pool.shutdown(wait=False)
    perf.mark("✓ FINE (fonte=nessuna)")
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
