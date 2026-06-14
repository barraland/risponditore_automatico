import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import (
    get_db, Appuntamento, Paziente, Servizio, Studio,
    StatoAppuntamento, StatoRichiamo,
)
from services.reminder import genera_richiamo
from services import calendar_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ---------- Schemas ----------

class ServizioCreate(BaseModel):
    nome: str
    durata_minuti: int = 30
    prezzo: float = 0.0
    intervallo_richiamo_giorni: int | None = None


class AppuntamentoCreate(BaseModel):
    paziente_id: int
    servizio_id: int
    data_ora: str  # formato: YYYY-MM-DDTHH:MM
    note: str = ""


class CambiaStatoRequest(BaseModel):
    stato: str


# ---------- Servizi ----------

@router.get("/servizi")
def lista_servizi(db: Session = Depends(get_db)):
    return [
        {
            "id": s.id,
            "nome": s.nome,
            "durata_minuti": s.durata_minuti,
            "prezzo": s.prezzo,
            "intervallo_richiamo_giorni": s.intervallo_richiamo_giorni,
        }
        for s in db.query(Servizio).all()
    ]


@router.post("/servizi")
def crea_servizio(data: ServizioCreate, db: Session = Depends(get_db)):
    servizio = Servizio(**data.model_dump())
    db.add(servizio)
    db.commit()
    db.refresh(servizio)
    return {"id": servizio.id, "nome": servizio.nome}


@router.put("/servizi/{servizio_id}")
def aggiorna_servizio(servizio_id: int, data: ServizioCreate,
                      db: Session = Depends(get_db)):
    servizio = db.query(Servizio).get(servizio_id)
    if not servizio:
        raise HTTPException(status_code=404, detail="Servizio non trovato")
    for key, value in data.model_dump().items():
        setattr(servizio, key, value)
    db.commit()
    return {"id": servizio.id, "nome": servizio.nome}


@router.delete("/servizi/{servizio_id}")
def elimina_servizio(servizio_id: int, db: Session = Depends(get_db)):
    servizio = db.query(Servizio).get(servizio_id)
    if not servizio:
        raise HTTPException(status_code=404, detail="Servizio non trovato")
    db.delete(servizio)
    db.commit()
    return {"ok": True}


# ---------- Pazienti ----------

@router.get("/pazienti")
def lista_pazienti(q: str = "", db: Session = Depends(get_db)):
    query = db.query(Paziente)
    if q:
        filtro = f"%{q}%"
        query = query.filter(
            (Paziente.nome.ilike(filtro))
            | (Paziente.cognome.ilike(filtro))
            | (Paziente.telefono.ilike(filtro))
        )
    return [
        {
            "id": p.id,
            "nome": p.nome,
            "cognome": p.cognome,
            "telefono": p.telefono,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in query.order_by(Paziente.cognome).all()
    ]


@router.get("/pazienti/{paziente_id}")
def dettaglio_paziente(paziente_id: int, db: Session = Depends(get_db)):
    paziente = db.query(Paziente).get(paziente_id)
    if not paziente:
        raise HTTPException(status_code=404, detail="Paziente non trovato")

    appuntamenti = (
        db.query(Appuntamento)
        .filter(Appuntamento.paziente_id == paziente_id)
        .order_by(Appuntamento.data_ora.desc())
        .all()
    )

    return {
        "id": paziente.id,
        "nome": paziente.nome,
        "cognome": paziente.cognome,
        "telefono": paziente.telefono,
        "appuntamenti": [
            {
                "id": a.id,
                "servizio": db.query(Servizio).get(a.servizio_id).nome if db.query(Servizio).get(a.servizio_id) else "N/A",
                "data_ora": a.data_ora.isoformat(),
                "stato": a.stato.value,
                "note": a.note,
            }
            for a in appuntamenti
        ],
    }


# ---------- Appuntamenti ----------

@router.get("/appuntamenti")
def lista_appuntamenti(data_da: str = "", data_a: str = "",
                       db: Session = Depends(get_db)):
    query = db.query(Appuntamento)
    if data_da:
        query = query.filter(Appuntamento.data_ora >= datetime.fromisoformat(data_da))
    if data_a:
        query = query.filter(Appuntamento.data_ora <= datetime.fromisoformat(data_a))

    return [
        {
            "id": a.id,
            "paziente_id": a.paziente_id,
            "paziente_nome": f"{a.paziente.nome} {a.paziente.cognome}",
            "servizio": a.servizio.nome,
            "data_ora": a.data_ora.isoformat(),
            "durata_minuti": a.durata_minuti,
            "stato": a.stato.value,
            "note": a.note,
        }
        for a in query.order_by(Appuntamento.data_ora).all()
    ]


@router.post("/appuntamenti")
def crea_appuntamento(data: AppuntamentoCreate, db: Session = Depends(get_db)):
    paziente = db.query(Paziente).get(data.paziente_id)
    if not paziente:
        raise HTTPException(status_code=404, detail="Paziente non trovato")

    servizio = db.query(Servizio).get(data.servizio_id)
    if not servizio:
        raise HTTPException(status_code=404, detail="Servizio non trovato")

    data_ora = datetime.fromisoformat(data.data_ora)

    # Crea evento su Google Calendar
    titolo = f"{servizio.nome} - {paziente.nome} {paziente.cognome}"
    descrizione = f"Paziente: {paziente.nome} {paziente.cognome}\nTel: {paziente.telefono}"
    event_id = calendar_sync.crea_evento(titolo, data_ora, servizio.durata_minuti, descrizione)

    appuntamento = Appuntamento(
        paziente_id=data.paziente_id,
        servizio_id=data.servizio_id,
        data_ora=data_ora,
        durata_minuti=servizio.durata_minuti,
        stato=StatoAppuntamento.PRENOTATO,
        google_event_id=event_id,
        note=data.note,
    )
    db.add(appuntamento)
    db.commit()
    db.refresh(appuntamento)
    return {"id": appuntamento.id}


@router.post("/appuntamenti/{appuntamento_id}/stato")
def cambia_stato(appuntamento_id: int, data: CambiaStatoRequest,
                 db: Session = Depends(get_db)):
    appuntamento = db.query(Appuntamento).get(appuntamento_id)
    if not appuntamento:
        raise HTTPException(status_code=404, detail="Appuntamento non trovato")

    try:
        nuovo_stato = StatoAppuntamento(data.stato)
    except ValueError:
        raise HTTPException(status_code=400, detail="Stato non valido")

    vecchio_stato = appuntamento.stato
    appuntamento.stato = nuovo_stato
    db.commit()

    # Se passa a COMPLETATO, genera richiamo
    if nuovo_stato == StatoAppuntamento.COMPLETATO and vecchio_stato != StatoAppuntamento.COMPLETATO:
        genera_richiamo(db, appuntamento)

    # Se cancellato, cancella da Google Calendar
    if nuovo_stato == StatoAppuntamento.CANCELLATO and appuntamento.google_event_id:
        calendar_sync.cancella_evento(appuntamento.google_event_id)

    return {"id": appuntamento.id, "stato": nuovo_stato.value}


# ---------- Studio / Impostazioni ----------

@router.get("/studio")
def get_studio(db: Session = Depends(get_db)):
    studio = db.query(Studio).first()
    if not studio:
        raise HTTPException(status_code=404, detail="Studio non configurato")
    return {
        "id": studio.id,
        "nome": studio.nome,
        "nome_dottore": studio.nome_dottore,
        "telefono": studio.telefono,
        "indirizzo": studio.indirizzo,
        "orario_apertura": studio.orario_apertura,
        "orario_chiusura": studio.orario_chiusura,
        "giorni_lavorativi": studio.giorni_lavorativi,
        "durata_slot_default": studio.durata_slot_default,
    }


@router.post("/studio")
def aggiorna_studio(request: dict, db: Session = Depends(get_db)):
    studio = db.query(Studio).first()
    if not studio:
        raise HTTPException(status_code=404, detail="Studio non configurato")

    for key in ["nome", "nome_dottore", "telefono", "indirizzo", "orario_apertura",
                "orario_chiusura", "giorni_lavorativi", "durata_slot_default"]:
        if key in request:
            setattr(studio, key, request[key])

    db.commit()
    return {"ok": True}
