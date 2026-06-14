import logging
from datetime import datetime, timedelta, date

from sqlalchemy.orm import Session

from database import (
    SessionLocal, Appuntamento, Richiamo, Paziente, Servizio, Studio,
    StatoAppuntamento, StatoRichiamo,
)
from services.whatsapp import invia_messaggio

logger = logging.getLogger(__name__)


def genera_richiamo(db: Session, appuntamento: Appuntamento):
    """Genera un richiamo se il servizio lo prevede (dopo COMPLETATO)."""
    servizio = db.query(Servizio).get(appuntamento.servizio_id)
    if not servizio or not servizio.intervallo_richiamo_giorni:
        return

    data_prevista = appuntamento.data_ora.date() + timedelta(
        days=servizio.intervallo_richiamo_giorni
    )

    richiamo = Richiamo(
        paziente_id=appuntamento.paziente_id,
        servizio_id=appuntamento.servizio_id,
        data_prevista=data_prevista,
        stato=StatoRichiamo.DA_INVIARE,
    )
    db.add(richiamo)
    db.commit()
    logger.info(
        "Richiamo creato per paziente #%d, servizio %s, data prevista %s",
        appuntamento.paziente_id, servizio.nome, data_prevista,
    )


async def controlla_e_invia_reminder():
    """Controlla appuntamenti e richiami, invia reminder dove necessario.

    Da chiamare periodicamente (es. ogni ora).
    """
    db = SessionLocal()
    try:
        studio = db.query(Studio).first()
        nome_studio = studio.nome if studio else "Salone"

        await _invia_reminder_appuntamenti(db, nome_studio)
        await _invia_richiami(db, nome_studio)

    except Exception as e:
        logger.error("Errore nel job reminder: %s", e)
    finally:
        db.close()


async def _invia_reminder_appuntamenti(db: Session, nome_studio: str):
    """Invia reminder 24h prima degli appuntamenti."""
    adesso = datetime.now()
    tra_24h = adesso + timedelta(hours=24)
    tra_25h = adesso + timedelta(hours=25)

    appuntamenti = (
        db.query(Appuntamento)
        .filter(
            Appuntamento.stato.in_([
                StatoAppuntamento.PRENOTATO,
                StatoAppuntamento.CONFERMATO,
            ]),
            Appuntamento.data_ora >= tra_24h,
            Appuntamento.data_ora < tra_25h,
        )
        .all()
    )

    for app in appuntamenti:
        paziente = db.query(Paziente).get(app.paziente_id)
        servizio = db.query(Servizio).get(app.servizio_id)

        if not paziente or not servizio:
            continue

        ora_str = app.data_ora.strftime("%H:%M")
        data_str = app.data_ora.strftime("%d/%m/%Y")

        messaggio = (
            f"Ciao {paziente.nome}, ti ricordiamo l'appuntamento per "
            f"{servizio.nome} domani {data_str} alle {ora_str} presso {nome_studio}. "
            f"Per spostare o cancellare, rispondi a questo messaggio."
        )

        await invia_messaggio(paziente.telefono, messaggio)
        logger.info("Reminder inviato a %s per appuntamento #%d", paziente.telefono, app.id)


async def _invia_richiami(db: Session, nome_studio: str):
    """Invia messaggi di richiamo per i pazienti che ne hanno bisogno."""
    oggi = date.today()

    richiami = (
        db.query(Richiamo)
        .filter(
            Richiamo.data_prevista <= oggi,
            Richiamo.stato == StatoRichiamo.DA_INVIARE,
        )
        .all()
    )

    for richiamo in richiami:
        paziente = db.query(Paziente).get(richiamo.paziente_id)
        servizio = db.query(Servizio).get(richiamo.servizio_id)

        if not paziente or not servizio:
            continue

        mesi = servizio.intervallo_richiamo_giorni // 30 if servizio.intervallo_richiamo_giorni else 6

        messaggio = (
            f"Ciao {paziente.nome}, sono passati circa {mesi} mesi dal "
            f"tuo ultimo {servizio.nome}. Ti consigliamo di prenotare un nuovo "
            f"appuntamento. Vuoi che ti proponiamo una data?"
        )

        await invia_messaggio(paziente.telefono, messaggio)

        richiamo.stato = StatoRichiamo.INVIATO
        richiamo.messaggio_inviato_at = datetime.now()
        db.commit()

        logger.info("Richiamo inviato a %s per %s", paziente.telefono, servizio.nome)
