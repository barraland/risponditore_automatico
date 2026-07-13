"""GUI HORECA: Società, Agenti, Ordini.

- Società: lista + scheda con timeline omnicanale (messaggi, chiamate, ticket, ordini
  di TUTTI i contatti della società aggregati) + contatti + ordini.
- Agenti: lista + scheda con portafoglio società e ordini attribuiti.
- Ordini: lista + dettaglio (righe), creazione manuale e cambio stato.

Il modello dati e i helper di dominio stanno in database.py e services/crm.py.
"""

import logging

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import (
    get_db, Azienda, Societa, Agente, Ordine, Contatto,
    StatoRelazione, TipoAttivita, CanaleOrdine, StatoOrdine, OrigineOrdine,
    DirezioneMessaggio,
)
from services import crm

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _azienda(db: Session) -> Azienda:
    return db.query(Azienda).first()


def _enum_or(value, enum_cls, default):
    try:
        return enum_cls((value or "").strip().lower())
    except (ValueError, AttributeError):
        return default


# ---------- Società ----------

@router.get("/societa", response_class=HTMLResponse)
async def lista_societa(request: Request, q: str = "", stato: str = "", agente: str = "",
                        db: Session = Depends(get_db)):
    query = db.query(Societa)
    if q:
        f = f"%{q}%"
        query = query.filter(
            (Societa.insegna.ilike(f)) | (Societa.ragione_sociale.ilike(f)) | (Societa.citta.ilike(f))
        )
    if stato in (e.value for e in StatoRelazione):
        query = query.filter(Societa.stato_relazione == StatoRelazione(stato))
    if agente.isdigit():
        query = query.filter(Societa.agente_referente_id == int(agente))
    societa = query.order_by(Societa.created_at.desc()).all()
    return templates.TemplateResponse("societa.html", {
        "request": request, "azienda": _azienda(db),
        "societa": societa, "query": q, "stato": stato, "agente": agente,
        "agenti": db.query(Agente).order_by(Agente.cognome).all(),
        "tipi": list(TipoAttivita), "stati": list(StatoRelazione),
    })


@router.post("/societa")
async def crea_societa(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    def g(k): return (form.get(k) or "").strip() or None
    soc = Societa(
        insegna=g("insegna") or "Nuova società",
        ragione_sociale=g("ragione_sociale"),
        tipo=_enum_or(form.get("tipo"), TipoAttivita, TipoAttivita.RISTORANTE),
        piva=g("piva"), indirizzo=g("indirizzo"), citta=g("citta"),
        stato_relazione=_enum_or(form.get("stato_relazione"), StatoRelazione, StatoRelazione.PROSPECT),
        agente_referente_id=int(form["agente_referente_id"]) if (form.get("agente_referente_id") or "").isdigit() else None,
        note=g("note"),
    )
    db.add(soc)
    db.commit()
    return RedirectResponse(url=f"/societa/{soc.id}", status_code=303)


@router.get("/societa/{societa_id}", response_class=HTMLResponse)
async def scheda_societa(request: Request, societa_id: int, msg: str = "", err: str = "", db: Session = Depends(get_db)):
    soc = db.get(Societa, societa_id)
    if not soc:
        return RedirectResponse(url="/societa")
    return templates.TemplateResponse("societa_scheda.html", {
        "request": request, "azienda": _azienda(db),
        "soc": soc, "timeline": _timeline(soc),
        "agenti": db.query(Agente).order_by(Agente.cognome).all(),
        "tipi": list(TipoAttivita), "stati": list(StatoRelazione),
        "stati_ordine": list(StatoOrdine),
        "msg": msg, "err": err,
    })


@router.post("/societa/{societa_id}")
async def aggiorna_societa(request: Request, societa_id: int, db: Session = Depends(get_db)):
    soc = db.get(Societa, societa_id)
    if not soc:
        return RedirectResponse(url="/societa")
    form = await request.form()
    def g(k): return (form.get(k) or "").strip() or None
    soc.insegna = g("insegna") or soc.insegna
    soc.ragione_sociale = g("ragione_sociale")
    soc.tipo = _enum_or(form.get("tipo"), TipoAttivita, soc.tipo)
    soc.piva = g("piva"); soc.indirizzo = g("indirizzo"); soc.citta = g("citta")
    soc.stato_relazione = _enum_or(form.get("stato_relazione"), StatoRelazione, soc.stato_relazione)
    soc.agente_referente_id = int(form["agente_referente_id"]) if (form.get("agente_referente_id") or "").isdigit() else None
    soc.note = g("note")
    db.commit()
    return RedirectResponse(url=f"/societa/{societa_id}?msg=Società+aggiornata", status_code=303)


@router.post("/societa/{societa_id}/stato")
async def cambia_stato_societa(societa_id: int, stato: str = Form(...), db: Session = Depends(get_db)):
    """Cambia manualmente lo stato commerciale della società (es. prospect → cliente)."""
    soc = db.get(Societa, societa_id)
    if soc:
        soc.stato_relazione = _enum_or(stato, StatoRelazione, soc.stato_relazione)
        db.commit()
    return RedirectResponse(url=f"/societa/{societa_id}?msg=Stato+aggiornato", status_code=303)


@router.post("/societa/{societa_id}/elimina")
async def elimina_societa(societa_id: int, db: Session = Depends(get_db)):
    soc = db.get(Societa, societa_id)
    if soc:
        # I contatti restano (FK -> NULL); ordini e righe vengono rimossi (cascade).
        for c in list(soc.contatti):
            c.societa_id = None
        db.delete(soc)
        db.commit()
    return RedirectResponse(url="/societa", status_code=303)


@router.post("/societa/{societa_id}/contatti")
async def aggiungi_contatto_societa(request: Request, societa_id: int, db: Session = Depends(get_db)):
    """Crea una persona dentro la società, oppure collega un contatto esistente."""
    soc = db.get(Societa, societa_id)
    if not soc:
        return RedirectResponse(url="/societa")
    form = await request.form()
    esistente = form.get("contatto_id")
    if esistente and esistente.isdigit():
        c = db.get(Contatto, int(esistente))
        if c:
            c.societa_id = soc.id
    else:
        def g(k): return (form.get(k) or "").strip() or None
        c = Contatto(
            nome=g("nome"), cognome=g("cognome"), ruolo=g("ruolo"),
            telefono=g("telefono"), email=g("email"),
            ragione_sociale=soc.ragione_sociale or soc.insegna, sede=soc.citta,
            societa_id=soc.id, is_primario=(form.get("is_primario") == "on"),
        )
        db.add(c)
    db.commit()
    return RedirectResponse(url=f"/societa/{societa_id}?msg=Contatto+collegato", status_code=303)


# ---------- Timeline omnicanale ----------

def _timeline(soc: Societa) -> list[dict]:
    """Aggrega in ordine cronologico decrescente gli eventi di tutti i contatti della
    società (messaggi, chiamate, ticket) + gli ordini della società."""
    eventi = []
    for c in soc.contatti:
        for m in c.messaggi:
            in_ = m.direzione == DirezioneMessaggio.IN
            eventi.append({
                "ts": m.timestamp, "tipo": "messaggio",
                "icona": "bi-whatsapp", "colore": "success" if in_ else "secondary",
                "titolo": ("Messaggio da " if in_ else "Risposta a ") + c.nome_completo,
                "testo": m.testo, "contatto": c,
            })
        for ch in c.chiamate:
            eventi.append({
                "ts": ch.iniziata_at, "tipo": "chiamata",
                "icona": "bi-telephone", "colore": "info",
                "titolo": f"Telefonata · {c.nome_completo}",
                "testo": ch.riassunto or (ch.trascrizione or "")[:200], "contatto": c,
                "link": f"/contatti/{c.id}/chiamate",
            })
        for t in c.ticket:
            eventi.append({
                "ts": t.created_at, "tipo": "ticket",
                "icona": "bi-ticket-detailed", "colore": "warning",
                "titolo": f"Ticket: {t.titolo}",
                "testo": t.descrizione or "", "contatto": c,
                "link": "/ticket",
            })
    for o in soc.ordini:
        chi = o.agente.nome_completo if o.agente else (o.contatto.nome_completo if o.contatto else "—")
        eventi.append({
            "ts": o.data, "tipo": "ordine",
            "icona": "bi-bag-check", "colore": "primary",
            "titolo": f"Ordine #{o.id} · {o.stato.value} ({o.canale.value})",
            "testo": f"{o.n_articoli} articoli · € {o.totale:.2f} · da {chi}",
            "link": f"/ordini/{o.id}",
        })
    eventi.sort(key=lambda e: e["ts"] or 0, reverse=True)
    return eventi


# ---------- Agenti ----------

@router.get("/agenti", response_class=HTMLResponse)
async def lista_agenti(request: Request, db: Session = Depends(get_db)):
    agenti = db.query(Agente).order_by(Agente.cognome, Agente.nome).all()
    return templates.TemplateResponse("agenti.html", {
        "request": request, "azienda": _azienda(db), "agenti": agenti,
    })


@router.post("/agenti")
async def crea_agente(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    def g(k): return (form.get(k) or "").strip() or None
    prov = (form.get("percentuale_provvigione") or "").replace(",", ".").strip()
    ag = Agente(
        nome=g("nome"), cognome=g("cognome"), telefono=g("telefono"), email=g("email"),
        zona=g("zona"), percentuale_provvigione=float(prov) if prov else None, note=g("note"),
    )
    db.add(ag)
    db.commit()
    return RedirectResponse(url=f"/agenti/{ag.id}", status_code=303)


@router.get("/agenti/{agente_id}", response_class=HTMLResponse)
async def scheda_agente(request: Request, agente_id: int, msg: str = "", db: Session = Depends(get_db)):
    ag = db.get(Agente, agente_id)
    if not ag:
        return RedirectResponse(url="/agenti")
    ordini = db.query(Ordine).filter(Ordine.agente_id == ag.id).order_by(Ordine.data.desc()).all()
    fatturato = sum(o.totale for o in ordini if o.stato in (StatoOrdine.CONFERMATO, StatoOrdine.EVASO))
    provvigione = fatturato * (ag.percentuale_provvigione or 0) / 100
    return templates.TemplateResponse("agente.html", {
        "request": request, "azienda": _azienda(db), "ag": ag,
        "ordini": ordini, "fatturato": fatturato, "provvigione": provvigione,
    })


@router.post("/agenti/{agente_id}")
async def aggiorna_agente(request: Request, agente_id: int, db: Session = Depends(get_db)):
    ag = db.get(Agente, agente_id)
    if not ag:
        return RedirectResponse(url="/agenti")
    form = await request.form()
    def g(k): return (form.get(k) or "").strip() or None
    prov = (form.get("percentuale_provvigione") or "").replace(",", ".").strip()
    ag.nome = g("nome"); ag.cognome = g("cognome"); ag.telefono = g("telefono")
    ag.email = g("email"); ag.zona = g("zona"); ag.note = g("note")
    ag.percentuale_provvigione = float(prov) if prov else None
    db.commit()
    return RedirectResponse(url=f"/agenti/{agente_id}?msg=Agente+aggiornato", status_code=303)


@router.post("/agenti/{agente_id}/elimina")
async def elimina_agente(agente_id: int, db: Session = Depends(get_db)):
    ag = db.get(Agente, agente_id)
    if ag:
        for soc in list(ag.societa):
            soc.agente_referente_id = None
        for o in list(ag.ordini):
            o.agente_id = None
        db.delete(ag)
        db.commit()
    return RedirectResponse(url="/agenti", status_code=303)


# ---------- Ordini ----------

@router.get("/ordini", response_class=HTMLResponse)
async def lista_ordini(request: Request, stato: str = "", canale: str = "", db: Session = Depends(get_db)):
    query = db.query(Ordine)
    if stato in (e.value for e in StatoOrdine):
        query = query.filter(Ordine.stato == StatoOrdine(stato))
    if canale in (e.value for e in CanaleOrdine):
        query = query.filter(Ordine.canale == CanaleOrdine(canale))
    ordini = query.order_by(Ordine.data.desc()).all()
    return templates.TemplateResponse("ordini.html", {
        "request": request, "azienda": _azienda(db), "ordini": ordini,
        "stato": stato, "canale": canale,
        "stati": list(StatoOrdine), "canali": list(CanaleOrdine),
    })


@router.post("/ordini")
async def crea_ordine_manuale(request: Request, db: Session = Depends(get_db)):
    """Crea un ordine da una scheda società. Le righe arrivano da un textarea:
    una riga per prodotto, formato 'descrizione | quantità | unità | prezzo'."""
    form = await request.form()
    societa_id = form.get("societa_id")
    if not (societa_id and societa_id.isdigit()):
        return RedirectResponse(url="/societa", status_code=303)
    societa_id = int(societa_id)
    righe = _parse_righe(form.get("righe") or "")
    contatto_id = int(form["contatto_id"]) if (form.get("contatto_id") or "").isdigit() else None
    agente_id = int(form["agente_id"]) if (form.get("agente_id") or "").isdigit() else None
    default_origine = OrigineOrdine.AGENTE if agente_id and not contatto_id else OrigineOrdine.CLIENTE
    origine = _enum_or(form.get("origine"), OrigineOrdine, default_origine)
    descrizione_agente = (form.get("descrizione_agente") or "").strip()

    # Se l'ordine lo fa l'agente: agente e relativa descrizione sono obbligatori.
    if origine == OrigineOrdine.AGENTE and (not agente_id or not descrizione_agente):
        return RedirectResponse(
            url=f"/societa/{societa_id}?err=Per+un+ordine+inserito+dall'agente+servono+agente+e+descrizione",
            status_code=303)

    ordine = crm.crea_ordine(
        db, societa_id=societa_id, righe=righe, contatto_id=contatto_id, agente_id=agente_id,
        origine=origine, canale=CanaleOrdine.MANUALE,
        stato=_enum_or(form.get("stato"), StatoOrdine, StatoOrdine.CONFERMATO),
        note=form.get("note"), descrizione_agente=descrizione_agente,
    )
    if ordine:
        return RedirectResponse(url=f"/ordini/{ordine.id}", status_code=303)
    return RedirectResponse(url=f"/societa/{societa_id}?err=Errore+creazione+ordine", status_code=303)


def _parse_righe(testo: str) -> list[dict]:
    righe = []
    for line in (testo or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parti = [p.strip() for p in line.split("|")]
        righe.append({
            "descrizione": parti[0],
            "quantita": parti[1] if len(parti) > 1 else 1,
            "unita": parti[2] if len(parti) > 2 else "",
            "prezzo_unitario": parti[3] if len(parti) > 3 else "",
        })
    return righe


@router.get("/ordini/{ordine_id}", response_class=HTMLResponse)
async def dettaglio_ordine(request: Request, ordine_id: int, db: Session = Depends(get_db)):
    o = db.get(Ordine, ordine_id)
    if not o:
        return RedirectResponse(url="/ordini")
    return templates.TemplateResponse("ordine.html", {
        "request": request, "azienda": _azienda(db), "o": o,
        "stati": list(StatoOrdine),
    })


@router.post("/ordini/{ordine_id}/stato")
async def cambia_stato_ordine(ordine_id: int, stato: str = Form(...), db: Session = Depends(get_db)):
    o = db.get(Ordine, ordine_id)
    if o:
        o.stato = _enum_or(stato, StatoOrdine, o.stato)
        db.commit()
        if o.stato in (StatoOrdine.CONFERMATO, StatoOrdine.EVASO):
            crm.aggiorna_stato_relazione(db, o.societa)
    return RedirectResponse(url=f"/ordini/{ordine_id}", status_code=303)


@router.post("/ordini/{ordine_id}/elimina")
async def elimina_ordine(ordine_id: int, db: Session = Depends(get_db)):
    o = db.get(Ordine, ordine_id)
    societa_id = o.societa_id if o else None
    if o:
        db.delete(o)
        db.commit()
    return RedirectResponse(url=f"/societa/{societa_id}" if societa_id else "/ordini", status_code=303)
