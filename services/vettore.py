"""Strato vettoriale per la ricerca semantica sui documenti (PDF).

All'ingestion ogni documento viene spezzato in chunk; per ogni chunk salviamo l'embedding
(OpenAI) nella tabella documento_chunk, con i metadati per filtrare/citare (categoria = sezione
della dashboard, pagine). A runtime la ricerca embedda la domanda e prende i top-K per similarità
coseno. Inoltre genera un riassunto AI a livello di documento.

NB v1: la similarità si calcola in Python (ok per la scala dei PDF della demo). La stessa tabella
può migrare a pgvector per scalare a molti/grandi documenti senza cambiare l'interfaccia."""

import os
import re
import json
import math
import logging

from openai import OpenAI
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import Documento, Sezione, DocumentoChunk


def _is_postgres(db: Session) -> bool:
    try:
        return db.bind.dialect.name == "postgresql"
    except Exception:
        return False


def _vec_literal(emb: list[float]) -> str:
    """Rappresentazione testuale di un vettore per pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in emb) + "]"

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
SUMMARY_MODEL = os.getenv("RETRIEVER_MODEL", "gpt-5-mini")
CHUNK_CHARS = int(os.getenv("RETRIEVER_CHUNK_CHARS", "1100"))
CHUNK_OVERLAP = int(os.getenv("RETRIEVER_CHUNK_OVERLAP", "150"))


def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY non configurata nel .env.")
    return OpenAI(api_key=OPENAI_API_KEY)


# ---------- embedding + similarità ----------

def embed_batch(testi: list[str]) -> list[list[float]]:
    """Embedding di una lista di testi (in batch da 100)."""
    out: list[list[float]] = []
    cli = _client()
    for i in range(0, len(testi), 100):
        resp = cli.embeddings.create(model=EMBED_MODEL, input=testi[i:i + 100])
        out.extend(d.embedding for d in resp.data)
    return out


def embed_uno(testo: str) -> list[float]:
    return embed_batch([testo])[0]


def cosine(a: list[float], b: list[float]) -> float:
    s = da = db = 0.0
    for x, y in zip(a, b):
        s += x * y
        da += x * x
        db += y * y
    if da == 0.0 or db == 0.0:
        return 0.0
    return s / math.sqrt(da * db)


# ---------- chunking ----------

def chunk_testo(testo: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Spezza il testo in chunk di ~`size` caratteri, tagliando su un confine (spazio/newline)
    e con un piccolo overlap per non perdere il contesto a cavallo del taglio."""
    testo = re.sub(r"\n{3,}", "\n\n", (testo or "").strip())
    if not testo:
        return []
    chunks, i, n = [], 0, len(testo)
    while i < n:
        end = min(i + size, n)
        if end < n:  # estendi all'indietro fino a un confine pulito
            taglio = max(testo.rfind(" ", i + int(size * 0.6), end),
                         testo.rfind("\n", i + int(size * 0.6), end))
            if taglio > i:
                end = taglio
        pezzo = testo[i:end].strip()
        if pezzo:
            chunks.append(pezzo)
        if end >= n:
            break
        i = max(end - overlap, i + 1)
    return chunks


# ---------- indicizzazione (all'ingestion) ----------

def indicizza_documento(db: Session, documento_id: int) -> int:
    """(Ri)costruisce i chunk + embedding di un documento e ne genera il riassunto AI.
    Ritorna il numero di chunk creati. Va chiamata dopo che le Sezioni sono state salvate."""
    doc = db.get(Documento, documento_id)
    if not doc or not doc.sezioni:
        return 0

    db.query(DocumentoChunk).filter(DocumentoChunk.documento_id == doc.id).delete()

    pezzi: list[dict] = []
    for sez in sorted(doc.sezioni, key=lambda s: s.ordine):
        for testo in chunk_testo(sez.content_md or ""):
            pezzi.append({"testo": testo, "page_start": sez.page_start, "page_end": sez.page_end,
                          "sezione_id": sez.id})
    if not pezzi:
        return 0

    embeddings = embed_batch([p["testo"] for p in pezzi])
    creati = []
    for ordine, (p, emb) in enumerate(zip(pezzi, embeddings)):
        ch = DocumentoChunk(
            documento_id=doc.id, sezione_id=p["sezione_id"], ordine=ordine, categoria=doc.categoria,
            page_start=p["page_start"], page_end=p["page_end"], testo=p["testo"],
            embedding=json.dumps(emb),
        )
        db.add(ch)
        creati.append((ch, emb))

    # Su Postgres popola anche la colonna pgvector (la similarità la calcola poi il DB).
    if _is_postgres(db):
        try:
            db.flush()  # serve l'id dei chunk
            for ch, emb in creati:
                db.execute(text("UPDATE documento_chunk SET embedding_vec = CAST(:v AS vector) WHERE id = :id"),
                           {"v": _vec_literal(emb), "id": ch.id})
        except Exception as e:
            logger.warning("Popolamento pgvector saltato (colonna/estensione assente?): %s", e)

    doc.riassunto = genera_riassunto(doc)
    db.commit()
    logger.info("🔎 Indicizzato '%s': %d chunk (categoria=%s)", doc.nome_file, len(pezzi), doc.categoria)
    return len(pezzi)


def genera_riassunto(doc: Documento) -> str:
    """Riassunto AI del documento (2-3 frasi) a partire dai riassunti di sezione o dal contenuto."""
    base = "\n".join(f"- {s.titolo}: {s.summary or ''}" for s in doc.sezioni if (s.summary or s.titolo))
    if not base.strip():
        base = "\n\n".join((s.content_md or "")[:1500] for s in doc.sezioni[:3])
    base = base[:8000]
    try:
        resp = _client().chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": "Riassumi in 2-3 frasi, in italiano, cosa contiene questo "
                 "documento e a quali domande risponde. Solo il riassunto, niente preamboli."},
                {"role": "user", "content": f"Documento «{doc.nome_file}» (categoria: {doc.categoria}).\n{base}"},
            ],
            reasoning_effort="low",
            max_completion_tokens=400,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("Riassunto AI non generato per %s: %s", doc.nome_file, e)
        return ""


# ---------- ricerca ----------

def cerca(db: Session, domanda: str, k: int = 6, categoria: str | None = None,
          azienda_id: int | None = None) -> list[dict]:
    """Top-K chunk per similarità con la domanda, RISTRETTI ai documenti del tenant. Su Postgres la
    distanza la calcola pgvector; altrove si ripiega sul coseno in Python."""
    domanda = (domanda or "").strip()
    if not domanda:
        return []
    qemb = embed_uno(domanda)
    if _is_postgres(db):
        try:
            return _cerca_pg(db, qemb, k, categoria, azienda_id)
        except Exception as e:
            logger.warning("Ricerca pgvector fallita, uso fallback Python: %s", e)
    return _cerca_python(db, qemb, k, categoria, azienda_id)


def _riga(documento_id, documento, categoria, page_start, page_end, testo, score, inviabile=True) -> dict:
    return {
        "score": round(float(score), 4), "testo": testo, "documento_id": documento_id,
        "documento": documento or "", "categoria": categoria,
        "pagine": f"{page_start}-{page_end}" if page_start else None,
        "inviabile": bool(inviabile),
    }


def _cerca_pg(db: Session, qemb: list[float], k: int, categoria: str | None,
             azienda_id: int | None = None) -> list[dict]:
    filtro = " and c.categoria = :cat" if categoria else ""
    if azienda_id:
        filtro += " and d.azienda_id = :aid"
    sql = text(
        "select c.documento_id, c.categoria, c.page_start, c.page_end, c.testo, d.nome_file, d.inviabile, "
        "1 - (c.embedding_vec <=> cast(:q as vector)) as score "
        "from documento_chunk c join documenti d on d.id = c.documento_id "
        f"where c.embedding_vec is not null{filtro} "
        "order by c.embedding_vec <=> cast(:q as vector) limit :k"
    )
    params = {"q": _vec_literal(qemb), "k": k}
    if categoria:
        params["cat"] = categoria
    if azienda_id:
        params["aid"] = azienda_id
    rows = db.execute(sql, params).mappings().all()
    return [_riga(r["documento_id"], r["nome_file"], r["categoria"], r["page_start"], r["page_end"],
                  r["testo"], r["score"], r["inviabile"]) for r in rows]


def _cerca_python(db: Session, qemb: list[float], k: int, categoria: str | None,
                  azienda_id: int | None = None) -> list[dict]:
    q = db.query(DocumentoChunk).filter(DocumentoChunk.embedding.isnot(None))
    if categoria:
        q = q.filter(DocumentoChunk.categoria == categoria)
    if azienda_id:
        from database import Documento
        q = q.filter(DocumentoChunk.documento_id.in_(
            db.query(Documento.id).filter(Documento.azienda_id == azienda_id)))
    scored = []
    for c in q.all():
        try:
            emb = json.loads(c.embedding)
        except Exception:
            continue
        scored.append((cosine(qemb, emb), c))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [_riga(c.documento_id, c.documento.nome_file if c.documento else "", c.categoria,
                  c.page_start, c.page_end, c.testo, score,
                  c.documento.inviabile if c.documento else True) for score, c in scored[:k]]
