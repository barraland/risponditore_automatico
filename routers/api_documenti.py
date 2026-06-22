"""API JSON per i documenti, consumata dalla SPA (Vercel).

Flusso: la SPA carica l'originale su Supabase Storage (copia durevole) e invia lo
stesso file qui per l'INDICIZZAZIONE. Riusa la pipeline esistente (`documenti_service`
+ `ingestion`): estrae il testo, crea le Sezioni, aggiorna lo stato. Il retriever
(`consulta_documenti`) continua a leggere dalle Sezioni, quindi voce/WhatsApp tornano
a consultare i documenti caricati.

Autenticazione: token Supabase dell'utente loggato (verificato via /auth/v1/user).
CORS e apertura del prefisso /api sono gestiti in main.py.
"""

import os
import logging

import httpx
from fastapi import APIRouter, Depends, UploadFile, File, Form, Header, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from database import SessionLocal, Documento, Sezione, StatoDocumento, Azienda
from services import documenti as documenti_service
from services import ingestion

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_CATEGORIE_VALIDE = {"listino", "schede_prodotto", "contratti", "faq", "altro"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def _verify_user(authorization: str | None) -> None:
    """Verifica che la richiesta arrivi da un utente Supabase loggato.

    Se SUPABASE_URL/ANON non sono configurati (es. dev locale), la verifica è disattivata.
    """
    url = os.getenv("SUPABASE_URL", "").strip()
    anon = os.getenv("SUPABASE_ANON_KEY", "").strip()
    if not url or not anon:
        return
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token mancante")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{url}/auth/v1/user",
                                  headers={"Authorization": f"Bearer {token}", "apikey": anon})
    except httpx.HTTPError as e:
        logger.warning("Verifica token fallita (rete): %s", e)
        raise HTTPException(status_code=503, detail="Verifica token non disponibile")
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Token non valido")


def _ingest_pdf(documento_id: int) -> None:
    """Task in background: pipeline di ingestion su un PDF + salvataggio sezioni/stato."""
    db = SessionLocal()
    try:
        doc = db.get(Documento, documento_id)
        if not doc:
            return
        esito = ingestion.processa_documento(doc.percorso)
        doc.stato = StatoDocumento(esito["stato"])
        doc.n_pagine = esito.get("n_pagine")
        doc.errore = esito.get("errore")
        doc.indice_raw = esito.get("indice_raw")
        sezioni = esito.get("sezioni", [])
        for s in sezioni:
            db.add(Sezione(documento_id=doc.id, **s))
        if doc.anno is None:
            snippet = (sezioni[0].get("content_md") if sezioni else "") or ""
            doc.anno = ingestion.estrai_anno(doc.nome_file, snippet[:4000])
        db.commit()
        logger.info("Ingestion %s -> %s (%d sezioni)", doc.nome_file, doc.stato.value, len(sezioni))
    except Exception as e:
        logger.error("Ingestion fallita per doc %s: %s", documento_id, e)
        d = db.get(Documento, documento_id)
        if d:
            d.stato = StatoDocumento.ERROR
            d.errore = str(e)
            db.commit()
    finally:
        db.close()


@router.post("/documenti")
async def upload_documento(
    background: BackgroundTasks,
    categoria: str = Form("altro"),
    storage_path: str = Form(""),
    file: UploadFile = File(...),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Riceve il file dalla SPA, lo indicizza e ne registra i metadati.

    L'originale durevole vive su Supabase Storage (caricato dalla SPA): qui salviamo
    solo il `storage_path` per download/email futuri.
    """
    await _verify_user(authorization)
    if categoria not in _CATEGORIE_VALIDE:
        categoria = "altro"
    azienda = db.query(Azienda).first()

    content = await file.read()
    try:
        info = documenti_service.salva_documento(azienda.id if azienda else 0, file.filename, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    nome_lower = info["nome_file"].lower()
    is_pdf = nome_lower.endswith(".pdf")
    is_tabellare = nome_lower.endswith(documenti_service.ESTENSIONI_TABELLARI)
    doc = Documento(
        azienda_id=azienda.id if azienda else None, categoria=categoria,
        stato=StatoDocumento.PROCESSING if is_pdf else StatoDocumento.READY,
        anno=ingestion.estrai_anno(info["nome_file"]),
        storage_path=storage_path or None, **info,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    if is_pdf:
        background.add_task(_ingest_pdf, doc.id)
    else:
        testo = documenti_service.estrai_testo_semplice(doc.percorso)
        if doc.anno is None and not is_tabellare:
            doc.anno = ingestion.estrai_anno(doc.nome_file, testo)
        db.add(Sezione(
            documento_id=doc.id, ordine=0, titolo=doc.nome_file,
            summary=None, page_start=1, page_end=1,
            contiene_tabelle=is_tabellare, content_md=testo or None,
        ))
        db.commit()

    return {"id": doc.id, "nome_file": doc.nome_file, "categoria": doc.categoria, "stato": doc.stato.value}
