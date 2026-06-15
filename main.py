import os
import logging
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from database import init_db, SessionLocal, Studio, Condominio, Inquilino, Ticket, StatoTicket
from routers import webhook, dashboard, voice

# ---------- Logging ----------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup e shutdown dell'applicazione."""
    logger.info("Avvio applicazione...")
    init_db()
    seed_data()
    yield
    logger.info("Applicazione chiusa")


# ---------- FastAPI App ----------

app = FastAPI(
    title="Centralino AI - Amministrazione Condomini",
    version="1.0.0",
    lifespan=lifespan,
)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Routers
app.include_router(webhook.router)
app.include_router(dashboard.router)
app.include_router(voice.router)


# ---------- Seed Data ----------

def seed_data():
    """Popola il DB con dati demo se vuoto."""
    db = SessionLocal()
    try:
        # Profilo dello studio di amministrazione (usato per il branding navbar).
        if not db.query(Studio).first():
            db.add(Studio(
                nome=os.getenv("STUDIO_NOME", "Studio Amministrazione Rossi"),
                nome_dottore="Geom. Marco Rossi",
                telefono=os.getenv("STUDIO_TELEFONO", "+39 02 1234567"),
                indirizzo="Via Roma 1, Milano",
            ))
            db.commit()

        if db.query(Condominio).first():
            logger.info("DB già popolato, skip seed")
            return

        logger.info("Popolamento dati demo condomìni...")

        # Condominio demo con qualche inquilino.
        condominio = Condominio(
            nome="Condominio Via Verdi 8",
            indirizzo="Via Verdi 8",
            citta="Milano",
            cap="20121",
            codice_fiscale="97123456789",
            iban="IT60X0542811101000000123456",
            note="Palazzina di 8 unità, ascensore, riscaldamento centralizzato.",
        )
        db.add(condominio)
        db.flush()

        inquilini_demo = [
            ("Mario", "Rossi", "Scala A - Int. 1", 120.5, "+393331234567", "mario.rossi@email.it"),
            ("Giulia", "Bianchi", "Scala A - Int. 2", 95.0, "+393339876543", "g.bianchi@email.it"),
            ("Luca", "Verdi", "Scala B - Int. 3", 110.0, "+393335551234", None),
            ("Anna", "Neri", "Scala B - Int. 4", 88.5, None, "anna.neri@email.it"),
            ("Andrea", "Barral", "Scala A - Int. 99", 100.0, "+393755116724", "sommojames@gmail.com"),
        ]
        andrea = None
        for nome, cognome, unita, mill, tel, email in inquilini_demo:
            inq = Inquilino(
                condominio_id=condominio.id,
                nome=nome, cognome=cognome, unita=unita,
                millesimi=mill, telefono=tel, email=email,
            )
            db.add(inq)
            if cognome == "Barral":
                andrea = inq
        db.flush()

        # Un ticket demo già aperto (da Andrea Barral) per mostrare la dashboard.
        if andrea is not None:
            db.add(Ticket(
                condominio_id=condominio.id,
                inquilino_id=andrea.id,
                canale="whatsapp",
                titolo="Saldo conto condominiale 2025 non chiaro",
                descrizione="Il condomino contesta il saldo di fine esercizio e chiede una verifica.",
                storia=(
                    "Condomino: Buongiorno, nel bilancio 2025 risulta un mio saldo di 100,61 € ma non capisco se è a mio debito o credito.\n"
                    "Assistente: Un attimo, controllo nei documenti…\n"
                    "Condomino: Non sono convinto della risposta, vorrei parlare con l'amministratore."
                ),
                stato=StatoTicket.APERTO,
            ))

        # Un secondo condominio vuoto, per mostrare la lista.
        db.add(Condominio(
            nome="Condominio Piazza Dante 3",
            indirizzo="Piazza Dante 3",
            citta="Milano",
            cap="20122",
        ))

        db.commit()
        logger.info("Seed data completato: 2 condomìni, %d inquilini", len(inquilini_demo))

    except Exception as e:
        logger.error("Errore seed data: %s", e)
        db.rollback()
    finally:
        db.close()


# ---------- Entry point ----------

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "9999"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
