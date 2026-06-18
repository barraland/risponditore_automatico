"""Dashboard del risponditore (lead capture).

Gestisce l'anagrafica dei contatti (clienti / prospect), i ticket di follow-up,
le impostazioni (profilo aziendale + credenziali) e — parcheggiata — la base
documenti aziendale.
"""

import logging

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import json

from database import (
    get_db, SessionLocal, Azienda, Contatto, ContattoStato,
    Documento, Sezione, StatoDocumento, TestoCategoria, MessaggioChat, ChiamataVoce,
    Ticket, StatoTicket, RispostaTicket, Societa,
)
from services import documenti as documenti_service
from services import ingestion
from services import retriever as retriever_service
from services import config as config_service
from services import istruzioni as istruzioni_service
from services import profilo as profilo_service
from services import email as email_service

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


# Categorie documenti (base di conoscenza, generiche).
CATEGORIE_DOCUMENTI = [
    ("listino", "Listini e prezzi", "bi-file-earmark-spreadsheet"),
    ("schede_prodotto", "Schede prodotto/servizio", "bi-file-earmark-text"),
    ("contratti", "Contratti e condizioni", "bi-file-earmark-ruled"),
    ("faq", "FAQ e materiale informativo", "bi-question-circle"),
    ("altro", "Altri documenti", "bi-folder"),
]


def _azienda(db: Session) -> Azienda:
    return db.query(Azienda).first()


# ---------- Lista contatti (home) ----------

@router.get("/")
async def home():
    """Home: la dashboard HORECA parte dalle Società."""
    return RedirectResponse(url="/societa")


@router.get("/contatti", response_class=HTMLResponse)
async def lista_contatti(request: Request, q: str = "", db: Session = Depends(get_db)):
    """Elenco dei contatti (persone): clienti e prospect."""
    query = db.query(Contatto)
    if q:
        filtro = f"%{q}%"
        query = query.filter(
            (Contatto.nome.ilike(filtro))
            | (Contatto.cognome.ilike(filtro))
            | (Contatto.ragione_sociale.ilike(filtro))
            | (Contatto.email.ilike(filtro))
            | (Contatto.telefono.ilike(filtro))
        )
    contatti = query.order_by(Contatto.created_at.desc()).all()

    return templates.TemplateResponse("contatti.html", {
        "request": request,
        "azienda": _azienda(db),
        "contatti": contatti,
        "query": q,
        "societa": db.query(Societa).order_by(Societa.insegna).all(),
    })


def _form_contatto(form, db: Session) -> dict:
    """Estrae e normalizza i campi anagrafici da un form. La 'società' è un LINK
    (societa_id): la ragione sociale del contatto viene derivata dalla società scelta."""
    def g(k):
        return (form.get(k) or "").strip() or None
    stato_raw = (form.get("stato") or "").strip().lower()
    stato = ContattoStato(stato_raw) if stato_raw in (e.value for e in ContattoStato) else ContattoStato.PROSPECT
    dati = {
        "nome": g("nome"),
        "cognome": g("cognome"),
        "ruolo": g("ruolo"),
        "email": g("email"),
        "telefono": g("telefono"),
        "sede": g("sede"),
        "stato": stato,
        "note": g("note"),
        "societa_id": None,
        "ragione_sociale": None,
    }
    sid = form.get("societa_id")
    if sid and sid.isdigit():
        soc = db.get(Societa, int(sid))
        if soc:
            dati["societa_id"] = soc.id
            dati["ragione_sociale"] = soc.ragione_sociale or soc.insegna   # mirror per il contesto del risponditore
            if not dati["sede"]:
                dati["sede"] = soc.citta
    return dati


@router.post("/contatti")
async def crea_contatto(request: Request, db: Session = Depends(get_db)):
    """Registra un nuovo contatto."""
    form = await request.form()
    contatto = Contatto(**_form_contatto(form, db))
    db.add(contatto)
    db.commit()
    return RedirectResponse(url=f"/contatti/{contatto.id}", status_code=303)


# ---------- Dettaglio contatto ----------

@router.get("/contatti/{contatto_id}", response_class=HTMLResponse)
async def dettaglio_contatto(
    request: Request, contatto_id: int, msg: str = "", err: str = "",
    db: Session = Depends(get_db),
):
    """Dettaglio contatto: anagrafica, conversazioni, chiamate, ticket."""
    contatto = db.get(Contatto, contatto_id)
    if not contatto:
        return RedirectResponse(url="/")

    messaggi = []
    for m in contatto.messaggi:   # già ordinati per timestamp
        try:
            tr = json.loads(m.traccia) if m.traccia else []
        except (ValueError, TypeError):
            tr = []
        messaggi.append({"m": m, "traccia": tr})

    return templates.TemplateResponse("contatto.html", {
        "request": request,
        "azienda": _azienda(db),
        "contatto": contatto,
        "messaggi": messaggi,
        "stati": list(ContattoStato),
        "societa": db.query(Societa).order_by(Societa.insegna).all(),
        "msg": msg,
        "err": err,
    })


@router.post("/contatti/{contatto_id}")
async def aggiorna_contatto(request: Request, contatto_id: int, db: Session = Depends(get_db)):
    """Aggiorna l'anagrafica di un contatto."""
    contatto = db.get(Contatto, contatto_id)
    if contatto:
        for k, v in _form_contatto(await request.form(), db).items():
            setattr(contatto, k, v)
        db.commit()
    return RedirectResponse(url=f"/contatti/{contatto_id}?msg=Anagrafica+aggiornata", status_code=303)


@router.post("/contatti/{contatto_id}/elimina")
async def elimina_contatto(contatto_id: int, db: Session = Depends(get_db)):
    """Elimina un contatto e la sua storia."""
    contatto = db.get(Contatto, contatto_id)
    if contatto:
        db.delete(contatto)
        db.commit()
    return RedirectResponse(url="/contatti", status_code=303)


# ---------- Documenti (base di conoscenza, parcheggiata) ----------

_CATEGORIE_VALIDE = {key for key, _, _ in CATEGORIE_DOCUMENTI}
_CATEGORIE_LABEL = {key: label for key, label, _ in CATEGORIE_DOCUMENTI}


def _ingest_pdf(documento_id: int):
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
        logger.error("Ingestion in background fallita per doc %s: %s", documento_id, e)
        doc = db.get(Documento, documento_id)
        if doc:
            doc.stato = StatoDocumento.ERROR
            doc.errore = f"Errore inatteso: {e}"
            db.commit()
    finally:
        db.close()


@router.get("/documenti", response_class=HTMLResponse)
async def lista_documenti(request: Request, msg: str = "", err: str = "", db: Session = Depends(get_db)):
    """Base documenti aziendale: lista + upload (parcheggiata, non usata dal risponditore)."""
    documenti = db.query(Documento).order_by(Documento.caricato_at.desc()).all()
    doc_per_categoria = {key: [] for key, _, _ in CATEGORIE_DOCUMENTI}
    for doc in documenti:
        doc_per_categoria.setdefault(doc.categoria, []).append(doc)
    testo_per_categoria = {
        t.categoria: t.testo for t in db.query(TestoCategoria).all()
    }
    return templates.TemplateResponse("documenti.html", {
        "request": request,
        "azienda": _azienda(db),
        "categorie_documenti": CATEGORIE_DOCUMENTI,
        "doc_per_categoria": doc_per_categoria,
        "testo_per_categoria": testo_per_categoria,
        "msg": msg,
        "err": err,
    })


@router.post("/documenti")
async def carica_documento(
    background: BackgroundTasks,
    categoria: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Carica un documento. I PDF vengono indicizzati in background (async)."""
    if categoria not in _CATEGORIE_VALIDE:
        categoria = "altro"
    azienda = _azienda(db)

    content = await file.read()
    try:
        info = documenti_service.salva_documento(azienda.id if azienda else 0, file.filename, content)
    except ValueError as e:
        return RedirectResponse(url=f"/documenti?err={str(e).replace(' ', '+')}", status_code=303)

    nome_lower = info["nome_file"].lower()
    is_pdf = nome_lower.endswith(".pdf")
    # Excel/CSV: caricati per intero, senza alcuna chiamata LLM (né sezionatore né anno da contenuto).
    is_tabellare = nome_lower.endswith(documenti_service.ESTENSIONI_TABELLARI)
    doc = Documento(
        azienda_id=azienda.id if azienda else None, categoria=categoria,
        stato=StatoDocumento.PROCESSING if is_pdf else StatoDocumento.READY,
        anno=ingestion.estrai_anno(info["nome_file"]),
        **info,
    )
    db.add(doc)
    db.commit()

    if is_pdf:
        background.add_task(_ingest_pdf, doc.id)
        msg = "Documento+caricato,+indicizzazione+in+corso"
    else:
        testo = documenti_service.estrai_testo_semplice(doc.percorso)
        # Anno da contenuto via LLM solo per i documenti testuali; per Excel/CSV niente LLM.
        if doc.anno is None and not is_tabellare:
            doc.anno = ingestion.estrai_anno(doc.nome_file, testo)
        db.add(Sezione(
            documento_id=doc.id, ordine=0, titolo=doc.nome_file,
            summary=None, page_start=1, page_end=1,
            contiene_tabelle=is_tabellare, content_md=testo or None,
        ))
        db.commit()
        msg = "Documento+caricato"

    return RedirectResponse(url=f"/documenti?msg={msg}", status_code=303)


@router.post("/documenti/testo")
async def salva_testo_categoria(
    categoria: str = Form(...),
    testo: str = Form(""),
    db: Session = Depends(get_db),
):
    """Salva il testo libero associato a una categoria (note che accompagnano i documenti)."""
    if categoria not in _CATEGORIE_VALIDE:
        return RedirectResponse(url="/documenti?err=Categoria+non+valida", status_code=303)
    azienda = _azienda(db)
    rec = db.query(TestoCategoria).filter(TestoCategoria.categoria == categoria).first()
    if not rec:
        rec = TestoCategoria(azienda_id=azienda.id if azienda else None, categoria=categoria)
        db.add(rec)
    rec.testo = testo.strip() or None
    db.commit()
    return RedirectResponse(url="/documenti?msg=Testo+della+categoria+salvato", status_code=303)


@router.post("/documenti/chat")
async def documenti_chat(domanda: str = Form(...), db: Session = Depends(get_db)):
    """Banco di prova dell'agente retriever: data una domanda, pianifica gli accessi
    ai documenti e genera la risposta. Ritorna JSON (risposta + piano + fonti)."""
    esito = retriever_service.rispondi(db, domanda)
    return JSONResponse({
        "risposta": esito.get("risposta", ""),
        "piano": esito.get("piano") or {},
        "fonti": esito.get("fonti") or [],
        "errore": esito.get("errore"),
    })


@router.get("/documenti/{documento_id}", response_class=HTMLResponse)
async def dettaglio_documento(request: Request, documento_id: int, db: Session = Depends(get_db)):
    """Pagina dettaglio: anteprima del PDF + indice generato (sezioni)."""
    doc = db.get(Documento, documento_id)
    if not doc:
        return RedirectResponse(url="/documenti")
    return templates.TemplateResponse("documento.html", {
        "request": request,
        "azienda": _azienda(db),
        "doc": doc,
        "categoria_label": _CATEGORIE_LABEL.get(doc.categoria, doc.categoria),
    })


@router.get("/documenti/{documento_id}/preview")
async def anteprima_documento(documento_id: int, db: Session = Depends(get_db)):
    """Serve il file inline (per l'<iframe> di anteprima)."""
    doc = db.get(Documento, documento_id)
    if not doc:
        return RedirectResponse(url="/documenti")
    return FileResponse(doc.percorso, content_disposition_type="inline")


@router.get("/documenti/{documento_id}/download")
async def scarica_documento(documento_id: int, db: Session = Depends(get_db)):
    """Restituisce il file originale (download)."""
    doc = db.get(Documento, documento_id)
    if not doc:
        return RedirectResponse(url="/documenti")
    return FileResponse(doc.percorso, filename=doc.nome_file)


@router.post("/documenti/{documento_id}/anno")
async def aggiorna_anno_documento(documento_id: int, anno: str = Form(""), db: Session = Depends(get_db)):
    """Imposta/corregge a mano l'anno di riferimento del documento."""
    doc = db.get(Documento, documento_id)
    if not doc:
        return RedirectResponse(url="/documenti")
    val = anno.strip()
    doc.anno = int(val) if val.isdigit() else None
    db.commit()
    return RedirectResponse(url=f"/documenti/{documento_id}?msg=Anno+aggiornato", status_code=303)


@router.post("/documenti/{documento_id}/elimina")
async def elimina_documento(documento_id: int, db: Session = Depends(get_db)):
    """Elimina un documento (file su disco + record + sezioni)."""
    doc = db.get(Documento, documento_id)
    if doc:
        documenti_service.elimina_file(doc.percorso)
        db.delete(doc)
        db.commit()
    return RedirectResponse(url="/documenti", status_code=303)


# ---------- Storico interazioni ----------

@router.get("/contatti/{contatto_id}/chiamate", response_class=HTMLResponse)
async def chiamate_contatto(request: Request, contatto_id: int, db: Session = Depends(get_db)):
    """Log delle telefonate di un contatto: riassunto + trascrizione completa."""
    contatto = db.get(Contatto, contatto_id)
    if not contatto:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("chiamate.html", {
        "request": request,
        "azienda": _azienda(db),
        "contatto": contatto,
        "chiamate": contatto.chiamate,  # già ordinate per iniziata_at desc
    })


# ---------- Assistente (profilo aziendale + istruzioni) ----------

@router.get("/assistente", response_class=HTMLResponse)
async def assistente(request: Request, msg: str = "", db: Session = Depends(get_db)):
    """Contenuto del risponditore: profilo aziendale (testi liberi) + istruzioni."""
    return templates.TemplateResponse("assistente.html", {
        "request": request,
        "azienda": _azienda(db),
        "info_qualificazione_default": profilo_service.INFO_QUALIFICAZIONE_DEFAULT,
        "istruzioni": istruzioni_service.leggi(),
        "msg": msg,
    })


@router.post("/assistente")
async def salva_assistente(request: Request, db: Session = Depends(get_db)):
    """Salva profilo aziendale (DB) e istruzioni (file)."""
    form = await request.form()
    azienda = _azienda(db)
    if azienda:
        azienda.nome = (form.get("AZIENDA_NOME") or azienda.nome or "").strip() or azienda.nome
        azienda.telefono = (form.get("AZIENDA_TELEFONO") or "").strip() or None
        azienda.descrizione_servizi = (form.get("descrizione_servizi") or "").strip() or None
        azienda.criteri_priorita = (form.get("criteri_priorita") or "").strip() or None
        azienda.info_qualificazione = (form.get("info_qualificazione") or "").strip() or None
        db.commit()
    istruzioni_service.salva(form.get("ISTRUZIONI_ASSISTENTE", ""))
    return RedirectResponse(url="/assistente?msg=Profilo+salvato", status_code=303)


# ---------- Impostazioni (tecniche) ----------

@router.get("/impostazioni", response_class=HTMLResponse)
async def impostazioni(request: Request, msg: str = "", db: Session = Depends(get_db)):
    """Impostazioni tecniche: endpoint + credenziali."""
    return templates.TemplateResponse("impostazioni.html", {
        "request": request,
        "azienda": _azienda(db),
        "valori": config_service.leggi(),
        "segrete": config_service.SEGRETE,
        "effort_validi": config_service.EFFORT_VALIDI,
        "voci_valide": config_service.VOCI_VALIDE,
        "endpoints": config_service.endpoints(request.headers.get("host")),
        "msg": msg,
    })


@router.post("/impostazioni")
async def salva_impostazioni(request: Request, db: Session = Depends(get_db)):
    """Salva le chiavi tecniche (.env)."""
    form = await request.form()
    updates = {k: form.get(k, "") for k in config_service.CHIAVI}
    config_service.aggiorna(updates)
    return RedirectResponse(url="/impostazioni?msg=Impostazioni+salvate", status_code=303)


# ---------- Ticket ----------

@router.get("/ticket", response_class=HTMLResponse)
async def lista_ticket(request: Request, tutti: int = 0, db: Session = Depends(get_db)):
    """Ticket di follow-up. Di default solo gli aperti."""
    q = db.query(Ticket)
    if not tutti:
        q = q.filter(Ticket.stato == StatoTicket.APERTO)
    ticket = q.order_by(Ticket.created_at.desc()).all()
    n_aperti = db.query(Ticket).filter(Ticket.stato == StatoTicket.APERTO).count()
    return templates.TemplateResponse("ticket.html", {
        "request": request,
        "azienda": _azienda(db),
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
    """L'operatore risponde a un ticket. Opzionalmente inoltra la risposta al contatto via email."""
    form = await request.form()
    testo = (form.get("testo") or "").strip()
    invia = form.get("invia_email") == "on"
    chiudi = form.get("chiudi") == "on"
    t = db.get(Ticket, ticket_id)
    if not t or not testo:
        return RedirectResponse(url="/ticket", status_code=303)

    inviata = False
    if invia and t.contatto and t.contatto.email:
        inviata = bool(email_service.invia_email(
            destinatario=t.contatto.email,
            oggetto=f"Risposta alla sua richiesta: {t.titolo}",
            corpo=(f"Gentile {t.contatto.nome or t.contatto.nome_completo},\n\n{testo}\n\n"
                   f"Cordiali saluti,\n{profilo_service.nome_azienda(db)}"),
        ))

    db.add(RispostaTicket(ticket_id=t.id, testo=testo, inviata_email=inviata))
    if chiudi:
        t.stato = StatoTicket.CHIUSO
    db.commit()
    return RedirectResponse(url="/ticket", status_code=303)
