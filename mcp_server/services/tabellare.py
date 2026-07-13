"""Interrogazione di file TABELLARI (CSV/Excel) strutturati.

All'ingestion ogni file tabellare viene salvato come righe (JSON colonna->valore) + i FACET di ogni
colonna: tipo, valori distinti e un flag `esaustivo`. Se i valori distinti sono ≤ soglia (20) la
lista è COMPLETA (sicura per un filtro esatto); altrimenti teniamo solo un CAMPIONE e marchiamo la
colonna come NON sicura per filtri esatti (l'agente lo deve sapere). I CSV possono avere strutture
diverse: lo schema lo scopriamo a runtime e lo passiamo all'agente.

NB v1: il filtro si applica in Python sulle righe caricate (ok per la scala della demo). Per cataloghi
enormi (1M SKU) la stessa interfaccia migra a una vera tabella SQL con indici."""

import os
import json
import shutil
import logging
import tempfile

import pandas as pd
from sqlalchemy.orm import Session

from database import Documento, DocumentoColonna, DocumentoRiga

logger = logging.getLogger(__name__)

MAX_DISTINTI = int(os.getenv("TAB_MAX_DISTINTI", "20"))   # oltre questa soglia: campione, non lista esaustiva
MAX_RIGHE = int(os.getenv("TAB_MAX_RIGHE", "20000"))
_OPS = {"=", "!=", "<", "<=", ">", ">=", "contains", "in"}


def _leggi_df(percorso: str) -> pd.DataFrame | None:
    ext = os.path.splitext(percorso)[1].lower()
    try:
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(percorso)
        return pd.read_csv(percorso, sep=None, engine="python")  # autodetect separatore
    except Exception as e:
        logger.warning("Lettura tabella fallita %s: %s", percorso, e)
        return None


def _tipo(serie) -> str:
    if pd.api.types.is_numeric_dtype(serie):
        return "numero"
    if pd.api.types.is_datetime64_any_dtype(serie):
        return "data"
    return "testo"


def _val(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    return s or None


def _file_locale(doc) -> tuple[str | None, str | None]:
    """Path del file da leggere: disco se presente, altrimenti scaricato da Supabase Storage in un
    temp (a prova di restart del container). Ritorna (percorso, cartella_temp_da_pulire | None)."""
    if doc.percorso and os.path.exists(doc.percorso):
        return doc.percorso, None
    if getattr(doc, "storage_path", None):
        from services.documenti import _scarica_da_storage, _safe_filename
        data = _scarica_da_storage(doc.storage_path)
        if data:
            tmp = tempfile.mkdtemp(prefix="tab_")
            p = os.path.join(tmp, _safe_filename(doc.nome_file))
            with open(p, "wb") as f:
                f.write(data)
            return p, tmp
    return None, None


def indicizza_tabella(db: Session, documento_id: int) -> int:
    """(Ri)costruisce righe + facet colonne di un file tabellare. Ritorna il numero di righe."""
    doc = db.get(Documento, documento_id)
    if not doc:
        return 0
    percorso, da_pulire = _file_locale(doc)
    if not percorso:
        return 0
    try:
        df = _leggi_df(percorso)
    finally:
        if da_pulire:
            shutil.rmtree(da_pulire, ignore_errors=True)
    if df is None or df.empty:
        return 0
    df.columns = [str(c).strip() for c in df.columns]

    db.query(DocumentoRiga).filter(DocumentoRiga.documento_id == doc.id).delete()
    db.query(DocumentoColonna).filter(DocumentoColonna.documento_id == doc.id).delete()

    for nome in df.columns:
        serie = df[nome]
        tipo = _tipo(serie)
        valori = [x for x in (_val(v) for v in serie.tolist()) if x is not None and x != ""]
        distinti = list(dict.fromkeys(valori))   # unici, ordine preservato
        n = len(distinti)
        esaustivo = n <= MAX_DISTINTI
        col = DocumentoColonna(
            documento_id=doc.id, nome=nome, tipo=tipo, n_distinti=n, esaustivo=esaustivo,
            distinti=json.dumps(distinti if esaustivo else distinti[:MAX_DISTINTI], ensure_ascii=False),
        )
        if tipo == "numero":
            nums = [v for v in valori if isinstance(v, (int, float))]
            if nums:
                col.min_val, col.max_val = str(min(nums)), str(max(nums))
        db.add(col)

    n_righe = 0
    for i, (_, row) in enumerate(df.iterrows()):
        if i >= MAX_RIGHE:
            logger.warning("Tabella %s troncata a %d righe", doc.nome_file, MAX_RIGHE)
            break
        dati = {c: _val(row[c]) for c in df.columns}
        db.add(DocumentoRiga(documento_id=doc.id, ordine=i, dati=json.dumps(dati, ensure_ascii=False)))
        n_righe += 1
    db.commit()
    logger.info("📊 Tabella '%s' indicizzata: %d righe, %d colonne", doc.nome_file, n_righe, len(df.columns))
    return n_righe


def tabelle(db: Session, azienda_id: int | None = None) -> list[Documento]:
    ids = [r[0] for r in db.query(DocumentoColonna.documento_id).distinct().all()]
    if not ids:
        return []
    q = db.query(Documento).filter(Documento.id.in_(ids))
    if azienda_id:
        q = q.filter(Documento.azienda_id == azienda_id)
    return q.all()


def schema_prompt(db: Session, azienda_id: int | None = None) -> str:
    """Schema + FACET delle tabelle del tenant, da passare all'agente. Marca chiaramente le colonne
    NON sicure per un filtro esatto (campione, non lista esaustiva)."""
    docs = tabelle(db, azienda_id)
    if not docs:
        return ""
    blocchi = []
    for doc in docs:
        cols = db.query(DocumentoColonna).filter(DocumentoColonna.documento_id == doc.id).all()
        righe = [f"# TABELLA documento_id={doc.id} «{doc.nome_file}» (categoria: {doc.categoria})"]
        for c in cols:
            distinti = json.loads(c.distinti or "[]")
            if c.tipo == "numero" and c.min_val is not None:
                info = f"numero, intervallo {c.min_val}..{c.max_val} (ok filtri <,>,<=,>=)"
            elif c.esaustivo:
                info = f"{c.tipo}, valori AMMESSI ({c.n_distinti}, lista COMPLETA): {distinti}"
            else:
                info = (f"{c.tipo}, {c.n_distinti} valori distinti — CAMPIONE NON esaustivo: {distinti} "
                        f"— NON filtrare per valore esatto su questa colonna, usa solo 'contains'")
            righe.append(f"  - {c.nome}: {info}")
        blocchi.append("\n".join(righe))
    return "\n\n".join(blocchi)


def formatta_righe(righe: list[dict], max_righe: int = 15) -> str:
    """Rendering DETERMINISTICO delle righe (nessun LLM): i dati restano ESATTAMENTE quelli del CSV."""
    if not righe:
        return "Nessun risultato corrisponde alla richiesta."
    out = [f"{len(righe)} risultato/i:"]
    for r in righe[:max_righe]:
        campi = "; ".join(f"{k}: {v}" for k, v in r.items() if v not in (None, ""))
        out.append(f"- {campi}")
    if len(righe) > max_righe:
        out.append(f"… e altri {len(righe) - max_righe}.")
    return "\n".join(out)


def interroga(db: Session, documento_id: int, filtri: list[dict], order_by: str | None = None,
              ascending: bool = True, limit: int = 30) -> list[dict]:
    """Filtra le righe di una tabella. `filtri` = lista di {campo, op, valore}. I filtri su colonne
    inesistenti o con operatori non validi vengono ignorati."""
    cols = {c.nome for c in db.query(DocumentoColonna).filter(DocumentoColonna.documento_id == documento_id).all()}
    if not cols:
        return []
    righe = [json.loads(r.dati) for r in db.query(DocumentoRiga)
             .filter(DocumentoRiga.documento_id == documento_id).order_by(DocumentoRiga.ordine).all()]

    def passa(riga: dict, f: dict) -> bool:
        campo, op, val = f.get("campo"), f.get("op", "="), f.get("valore")
        if campo not in cols or op not in _OPS:
            return True
        cell = riga.get(campo)
        if cell is None:
            return False
        if op == "contains":
            return str(val).lower() in str(cell).lower()
        if op == "in":
            vals = val if isinstance(val, list) else [val]
            return any(str(cell).strip().lower() == str(x).strip().lower() for x in vals)
        if op in ("<", "<=", ">", ">="):
            try:
                a, b = float(cell), float(val)
            except (TypeError, ValueError):
                return False
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        eq = str(cell).strip().lower() == str(val).strip().lower()
        return eq if op == "=" else not eq

    res = [r for r in righe if all(passa(r, f) for f in (filtri or []))]
    if order_by and order_by in cols:
        def chiave(r):
            v = r.get(order_by)
            try:
                return (0, float(v))
            except (TypeError, ValueError):
                return (1, str(v))
        res.sort(key=chiave, reverse=not ascending)
    return res[:max(1, min(int(limit or 30), 200))]
