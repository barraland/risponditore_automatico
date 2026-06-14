"""Dashboard amministratore di condominio.

Gestisce l'anagrafica dei condomìni e, per ciascuno, l'anagrafica degli
inquilini (inserimento manuale + import da Excel destrutturato) e la sezione
documenti (per ora solo interfaccia, nessun salvataggio lato backend).
"""

import logging

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, BackgroundTasks, Body
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import json

from database import (
    get_db, SessionLocal, Studio, Condominio, Inquilino,
    Documento, Sezione, StatoDocumento, MessaggioChat, ChiamataVoce,
    Ticket, StatoTicket, RispostaTicket,
)
from services import import_inquilini
from services import documenti as documenti_service
from services import ingestion
from services import agente
from services import config as config_service
from services import email as email_service

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


# Categorie documenti mostrate nel dettaglio condominio (UI soltanto, per ora).
CATEGORIE_DOCUMENTI = [
    ("verbali", "Verbali d'assemblea", "bi-file-earmark-text"),
    ("bilanci", "Bilanci e riparti", "bi-file-earmark-spreadsheet"),
    ("regolamento", "Regolamento condominiale", "bi-journal-text"),
    ("avvisi", "Avvisi e circolari", "bi-megaphone"),
    ("polizza", "Polizza assicurativa", "bi-shield-check"),
    ("fornitori", "Contratti e fornitori", "bi-tools"),
    ("altro", "Altri documenti", "bi-folder"),
]


def _studio(db: Session) -> Studio:
    return db.query(Studio).first()


# ---------- Lista condomìni ----------

@router.get("/", response_class=HTMLResponse)
async def lista_condomini(request: Request, q: str = "", db: Session = Depends(get_db)):
    """Home: elenco dei condomìni gestiti."""
    query = db.query(Condominio)
    if q:
        filtro = f"%{q}%"
        query = query.filter(
            (Condominio.nome.ilike(filtro))
            | (Condominio.indirizzo.ilike(filtro))
            | (Condominio.citta.ilike(filtro))
        )
    condomini = query.order_by(Condominio.nome).all()

    return templates.TemplateResponse("condomini.html", {
        "request": request,
        "studio": _studio(db),
        "condomini": condomini,
        "query": q,
    })


@router.post("/condomini")
async def crea_condominio(
    nome: str = Form(...),
    indirizzo: str = Form(""),
    citta: str = Form(""),
    cap: str = Form(""),
    codice_fiscale: str = Form(""),
    iban: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """Registra un nuovo condominio."""
    condominio = Condominio(
        nome=nome.strip(),
        indirizzo=indirizzo.strip() or None,
        citta=citta.strip() or None,
        cap=cap.strip() or None,
        codice_fiscale=codice_fiscale.strip() or None,
        iban=iban.strip() or None,
        note=note.strip() or None,
    )
    db.add(condominio)
    db.commit()
    return RedirectResponse(url=f"/condomini/{condominio.id}", status_code=303)


# ---------- Dettaglio condominio ----------

@router.get("/condomini/{condominio_id}", response_class=HTMLResponse)
async def dettaglio_condominio(
    request: Request, condominio_id: int, msg: str = "", err: str = "",
    db: Session = Depends(get_db),
):
    """Dettaglio condominio: anagrafica, inquilini, documenti."""
    condominio = db.query(Condominio).get(condominio_id)
    if not condominio:
        return RedirectResponse(url="/")

    # Documenti raggruppati per categoria, per mostrarli sotto la card giusta.
    doc_per_categoria = {key: [] for key, _, _ in CATEGORIE_DOCUMENTI}
    for doc in condominio.documenti:
        doc_per_categoria.setdefault(doc.categoria, []).append(doc)

    return templates.TemplateResponse("condominio.html", {
        "request": request,
        "studio": _studio(db),
        "condominio": condominio,
        "categorie_documenti": CATEGORIE_DOCUMENTI,
        "doc_per_categoria": doc_per_categoria,
        "msg": msg,
        "err": err,
    })


@router.post("/condomini/{condominio_id}")
async def aggiorna_condominio(
    condominio_id: int,
    nome: str = Form(...),
    indirizzo: str = Form(""),
    citta: str = Form(""),
    cap: str = Form(""),
    codice_fiscale: str = Form(""),
    iban: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """Aggiorna l'anagrafica di un condominio."""
    condominio = db.query(Condominio).get(condominio_id)
    if condominio:
        condominio.nome = nome.strip()
        condominio.indirizzo = indirizzo.strip() or None
        condominio.citta = citta.strip() or None
        condominio.cap = cap.strip() or None
        condominio.codice_fiscale = codice_fiscale.strip() or None
        condominio.iban = iban.strip() or None
        condominio.note = note.strip() or None
        db.commit()
    return RedirectResponse(url=f"/condomini/{condominio_id}?msg=Anagrafica+aggiornata", status_code=303)


@router.post("/condomini/{condominio_id}/elimina")
async def elimina_condominio(condominio_id: int, db: Session = Depends(get_db)):
    """Elimina un condominio e i suoi inquilini."""
    condominio = db.query(Condominio).get(condominio_id)
    if condominio:
        db.delete(condominio)
        db.commit()
    return RedirectResponse(url="/", status_code=303)


# ---------- Inquilini ----------

@router.post("/condomini/{condominio_id}/inquilini")
async def aggiungi_inquilino(
    condominio_id: int,
    nome: str = Form(...),
    cognome: str = Form(""),
    unita: str = Form(""),
    millesimi: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    """Aggiunge manualmente un inquilino al condominio."""
    condominio = db.query(Condominio).get(condominio_id)
    if not condominio:
        return RedirectResponse(url="/")

    try:
        millesimi_val = float(millesimi.replace(",", ".")) if millesimi.strip() else None
    except ValueError:
        millesimi_val = None

    inquilino = Inquilino(
        condominio_id=condominio_id,
        nome=nome.strip(),
        cognome=cognome.strip() or None,
        unita=unita.strip() or None,
        millesimi=millesimi_val,
        telefono=telefono.strip() or None,
        email=email.strip() or None,
    )
    db.add(inquilino)
    db.commit()
    return RedirectResponse(url=f"/condomini/{condominio_id}?msg=Inquilino+aggiunto", status_code=303)


@router.post("/inquilini/{inquilino_id}")
async def aggiorna_inquilino(
    inquilino_id: int,
    nome: str = Form(...),
    cognome: str = Form(""),
    unita: str = Form(""),
    millesimi: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    """Aggiorna l'anagrafica di un inquilino."""
    inquilino = db.get(Inquilino, inquilino_id)
    if not inquilino:
        return RedirectResponse(url="/")

    try:
        millesimi_val = float(millesimi.replace(",", ".")) if millesimi.strip() else None
    except ValueError:
        millesimi_val = None

    inquilino.nome = nome.strip()
    inquilino.cognome = cognome.strip() or None
    inquilino.unita = unita.strip() or None
    inquilino.millesimi = millesimi_val
    inquilino.telefono = telefono.strip() or None
    inquilino.email = email.strip() or None
    db.commit()
    return RedirectResponse(
        url=f"/condomini/{inquilino.condominio_id}?msg=Inquilino+aggiornato", status_code=303
    )


@router.post("/inquilini/{inquilino_id}/elimina")
async def elimina_inquilino(inquilino_id: int, db: Session = Depends(get_db)):
    """Elimina un inquilino."""
    inquilino = db.query(Inquilino).get(inquilino_id)
    condominio_id = inquilino.condominio_id if inquilino else None
    if inquilino:
        db.delete(inquilino)
        db.commit()
    if condominio_id:
        return RedirectResponse(url=f"/condomini/{condominio_id}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@router.post("/condomini/{condominio_id}/inquilini/import")
async def importa_inquilini(
    condominio_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Import da Excel/CSV destrutturato: GPT-5-mini struttura le righe."""
    condominio = db.query(Condominio).get(condominio_id)
    if not condominio:
        return RedirectResponse(url="/")

    content = await file.read()
    try:
        righe = import_inquilini.estrai_inquilini(file.filename, content)
    except ValueError as e:
        return RedirectResponse(
            url=f"/condomini/{condominio_id}?err={str(e).replace(' ', '+')}",
            status_code=303,
        )

    for r in righe:
        db.add(Inquilino(condominio_id=condominio_id, **r))
    db.commit()

    return RedirectResponse(
        url=f"/condomini/{condominio_id}?msg={len(righe)}+inquilini+importati",
        status_code=303,
    )


# ---------- Documenti ----------

_CATEGORIE_VALIDE = {key for key, _, _ in CATEGORIE_DOCUMENTI}
_CATEGORIE_LABEL = {key: label for key, label, _ in CATEGORIE_DOCUMENTI}


def _ingest_pdf(documento_id: int):
    """Task in background: esegue la pipeline di ingestion su un PDF e salva
    sezioni + stato. Apre una sessione DB propria (la richiesta è già chiusa)."""
    db = SessionLocal()
    try:
        doc = db.query(Documento).get(documento_id)
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
        # Anno: se non già ricavato dal nome file all'upload, prova dal contenuto (LLM).
        if doc.anno is None:
            snippet = (sezioni[0].get("content_md") if sezioni else "") or ""
            doc.anno = ingestion.estrai_anno(doc.nome_file, snippet[:4000])
        db.commit()
        logger.info("Ingestion %s -> %s (%d sezioni)", doc.nome_file, doc.stato.value, len(esito.get("sezioni", [])))
    except Exception as e:
        logger.error("Ingestion in background fallita per doc %s: %s", documento_id, e)
        doc = db.query(Documento).get(documento_id)
        if doc:
            doc.stato = StatoDocumento.ERROR
            doc.errore = f"Errore inatteso: {e}"
            db.commit()
    finally:
        db.close()


@router.post("/condomini/{condominio_id}/documenti")
async def carica_documento(
    condominio_id: int,
    background: BackgroundTasks,
    categoria: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Carica un documento. I PDF vengono indicizzati in background (async)."""
    condominio = db.query(Condominio).get(condominio_id)
    if not condominio:
        return RedirectResponse(url="/")
    if categoria not in _CATEGORIE_VALIDE:
        categoria = "altro"

    content = await file.read()
    try:
        info = documenti_service.salva_documento(condominio_id, file.filename, content)
    except ValueError as e:
        return RedirectResponse(
            url=f"/condomini/{condominio_id}?err={str(e).replace(' ', '+')}",
            status_code=303,
        )

    is_pdf = info["nome_file"].lower().endswith(".pdf")
    doc = Documento(
        condominio_id=condominio_id, categoria=categoria,
        stato=StatoDocumento.PROCESSING if is_pdf else StatoDocumento.READY,
        anno=ingestion.estrai_anno(info["nome_file"]),   # dal nome file, subito (robusto)
        **info,
    )
    db.add(doc)
    db.commit()

    if is_pdf:
        background.add_task(_ingest_pdf, doc.id)
        msg = "Documento+caricato,+indicizzazione+in+corso"
    else:
        # Non-PDF: una sola sezione con il testo integrale.
        testo = documenti_service.estrai_testo_semplice(doc.percorso)
        if doc.anno is None:
            doc.anno = ingestion.estrai_anno(doc.nome_file, testo)
        db.add(Sezione(
            documento_id=doc.id, ordine=0, titolo=doc.nome_file,
            summary=None, page_start=1, page_end=1,
            contiene_tabelle=False, content_md=testo or None,
        ))
        db.commit()
        msg = "Documento+caricato"

    return RedirectResponse(url=f"/condomini/{condominio_id}?msg={msg}", status_code=303)


@router.get("/documenti/{documento_id}", response_class=HTMLResponse)
async def dettaglio_documento(request: Request, documento_id: int, db: Session = Depends(get_db)):
    """Pagina dettaglio: anteprima del PDF + indice generato (sezioni)."""
    doc = db.query(Documento).get(documento_id)
    if not doc:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("documento.html", {
        "request": request,
        "studio": _studio(db),
        "doc": doc,
        "categoria_label": _CATEGORIE_LABEL.get(doc.categoria, doc.categoria),
    })


@router.get("/documenti/{documento_id}/preview")
async def anteprima_documento(documento_id: int, db: Session = Depends(get_db)):
    """Serve il file inline (per l'<iframe> di anteprima)."""
    doc = db.query(Documento).get(documento_id)
    if not doc:
        return RedirectResponse(url="/")
    return FileResponse(doc.percorso, content_disposition_type="inline")


@router.get("/documenti/{documento_id}/download")
async def scarica_documento(documento_id: int, db: Session = Depends(get_db)):
    """Restituisce il file originale (download)."""
    doc = db.query(Documento).get(documento_id)
    if not doc:
        return RedirectResponse(url="/")
    return FileResponse(doc.percorso, filename=doc.nome_file)


@router.post("/documenti/{documento_id}/anno")
async def aggiorna_anno_documento(documento_id: int, anno: str = Form(""), db: Session = Depends(get_db)):
    """Imposta/corregge a mano l'anno di riferimento del documento."""
    doc = db.query(Documento).get(documento_id)
    if not doc:
        return RedirectResponse(url="/")
    val = anno.strip()
    doc.anno = int(val) if val.isdigit() else None
    db.commit()
    return RedirectResponse(url=f"/documenti/{documento_id}?msg=Anno+aggiornato", status_code=303)


@router.post("/documenti/{documento_id}/elimina")
async def elimina_documento(documento_id: int, db: Session = Depends(get_db)):
    """Elimina un documento (file su disco + record + sezioni)."""
    doc = db.query(Documento).get(documento_id)
    condominio_id = doc.condominio_id if doc else None
    if doc:
        documenti_service.elimina_file(doc.percorso)
        db.delete(doc)
        db.commit()
    if condominio_id:
        return RedirectResponse(url=f"/condomini/{condominio_id}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


# ---------- Chat di test dell'agente (Q&A sui documenti del condominio) ----------

@router.post("/condomini/{condominio_id}/chat")
def chat_agente(condominio_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Risponde a una domanda interrogando i documenti del condominio.

    Endpoint sincrono: le chiamate LLM bloccanti girano nel threadpool di FastAPI.
    Ritorna JSON con la risposta finale e la traccia (piano + passi) per ispezione.
    """
    domanda = (payload or {}).get("domanda", "")
    return agente.rispondi(db, condominio_id, domanda)


@router.get("/inquilini/{inquilino_id}/conversazione", response_class=HTMLResponse)
async def conversazione_inquilino(request: Request, inquilino_id: int, db: Session = Depends(get_db)):
    """Storia chat WhatsApp di un inquilino + traccia delle chiamate LLM."""
    inq = db.get(Inquilino, inquilino_id)
    if not inq:
        return RedirectResponse(url="/")

    messaggi = []
    for m in inq.messaggi:   # già ordinati per timestamp
        try:
            tr = json.loads(m.traccia) if m.traccia else []
        except (ValueError, TypeError):
            tr = []
        messaggi.append({"m": m, "traccia": tr})

    return templates.TemplateResponse("conversazione.html", {
        "request": request,
        "studio": _studio(db),
        "inquilino": inq,
        "condominio": inq.condominio,
        "messaggi": messaggi,
    })


@router.get("/impostazioni", response_class=HTMLResponse)
async def impostazioni(request: Request, msg: str = "", db: Session = Depends(get_db)):
    """Impostazioni tecniche: endpoint (sola lettura) + credenziali (editabili)."""
    return templates.TemplateResponse("impostazioni.html", {
        "request": request,
        "studio": _studio(db),
        "valori": config_service.leggi(),
        "segrete": config_service.SEGRETE,
        "effort_validi": config_service.EFFORT_VALIDI,
        "endpoints": config_service.endpoints(request.headers.get("host")),
        "msg": msg,
    })


@router.post("/impostazioni")
async def salva_impostazioni(request: Request, db: Session = Depends(get_db)):
    """Sovrascrive le chiavi nel .env."""
    form = await request.form()
    updates = {k: form.get(k, "") for k in config_service.CHIAVI}
    config_service.aggiorna(updates)
    return RedirectResponse(url="/impostazioni?msg=Impostazioni+salvate+nel+.env", status_code=303)


@router.get("/ticket", response_class=HTMLResponse)
async def lista_ticket(request: Request, tutti: int = 0, db: Session = Depends(get_db)):
    """Ticket trasversali su tutti i condomìni. Di default solo gli aperti."""
    q = db.query(Ticket)
    if not tutti:
        q = q.filter(Ticket.stato == StatoTicket.APERTO)
    ticket = q.order_by(Ticket.created_at.desc()).all()
    n_aperti = db.query(Ticket).filter(Ticket.stato == StatoTicket.APERTO).count()
    return templates.TemplateResponse("ticket.html", {
        "request": request,
        "studio": _studio(db),
        "ticket": ticket,
        "tutti": bool(tutti),
        "n_aperti": n_aperti,
    })


@router.get("/ticket/count")
async def conta_ticket(db: Session = Depends(get_db)):
    """Numero di ticket aperti (per il badge in navbar)."""
    return {"aperti": db.query(Ticket).filter(Ticket.stato == StatoTicket.APERTO).count()}


@router.post("/ticket/{ticket_id}/chiudi")
async def chiudi_ticket_endpoint(ticket_id: int, db: Session = Depends(get_db)):
    """Segna un ticket come risolto/chiuso."""
    t = db.get(Ticket, ticket_id)
    if t:
        t.stato = StatoTicket.CHIUSO
        db.commit()
    return RedirectResponse(url="/ticket", status_code=303)


@router.post("/ticket/{ticket_id}/rispondi")
async def rispondi_ticket(ticket_id: int, request: Request, db: Session = Depends(get_db)):
    """L'amministratore risponde a un ticket. Opzionalmente inoltra la risposta
    al condomino via email."""
    form = await request.form()
    testo = (form.get("testo") or "").strip()
    invia = form.get("invia_email") == "on"
    chiudi = form.get("chiudi") == "on"
    t = db.get(Ticket, ticket_id)
    if not t or not testo:
        return RedirectResponse(url="/ticket", status_code=303)

    inviata = False
    if invia and t.inquilino and t.inquilino.email:
        inviata = bool(email_service.invia_email(
            destinatario=t.inquilino.email,
            oggetto=f"Risposta alla sua segnalazione: {t.titolo}",
            corpo=(f"Gentile {t.inquilino.nome},\n\n{testo}\n\n"
                   f"Cordiali saluti,\nL'amministratore del condominio"),
        ))

    db.add(RispostaTicket(ticket_id=t.id, testo=testo, inviata_email=inviata))
    if chiudi:
        t.stato = StatoTicket.CHIUSO
    db.commit()
    return RedirectResponse(url="/ticket", status_code=303)


@router.get("/inquilini/{inquilino_id}/chiamate", response_class=HTMLResponse)
async def chiamate_inquilino(request: Request, inquilino_id: int, db: Session = Depends(get_db)):
    """Log delle telefonate di un inquilino: riassunto + trascrizione completa."""
    inq = db.get(Inquilino, inquilino_id)
    if not inq:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("chiamate.html", {
        "request": request,
        "studio": _studio(db),
        "inquilino": inq,
        "condominio": inq.condominio,
        "chiamate": inq.chiamate,  # già ordinate per iniziata_at desc
    })
