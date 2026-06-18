import os
import logging
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from database import (
    init_db, SessionLocal, Azienda, Contatto, ContattoStato,
    Ticket, StatoTicket, PrioritaTicket,
    Agente, Societa, Ordine, RigaOrdine,
    TipoAttivita, StatoRelazione, CanaleOrdine, StatoOrdine, OrigineOrdine,
    MessaggioChat, DirezioneMessaggio, ChiamataVoce,
)
from routers import webhook, dashboard, voice, horeca

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
    title="Risponditore AI - Lead Capture",
    version="2.0.0",
    lifespan=lifespan,
)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Routers
app.include_router(webhook.router)
app.include_router(dashboard.router)
app.include_router(voice.router)
app.include_router(horeca.router)


# ---------- Seed Data ----------

def seed_data():
    """Popola il DB con uno scenario HORECA demo se vuoto."""
    db = SessionLocal()
    try:
        # Profilo aziendale singleton: qui l'azienda è un DISTRIBUTORE food&beverage per l'HORECA.
        if not db.query(Azienda).first():
            db.add(Azienda(
                nome=os.getenv("AZIENDA_NOME", "Distribuzione HORECA S.r.l."),
                telefono=os.getenv("AZIENDA_TELEFONO", "+39 02 1234567"),
                descrizione_servizi=(
                    "Esempio (da personalizzare in Impostazioni): distribuiamo alimentari e bevande a "
                    "ristoranti, pizzerie, bar e hotel in Lombardia e Piemonte. Consegna in 24-48h, "
                    "ordine minimo 150€. Gestiamo ordini via WhatsApp, telefono e tramite la rete di agenti. "
                    "Catalogo: farine, olio, conserve, latticini, vino, caffè, bevande."
                ),
                criteri_priorita=(
                    "ALTA: cliente storico, o ordine urgente entro 24h, o nuovo locale con volumi alti. "
                    "MEDIA: ordine ordinario di un cliente attivo. BASSA: solo richiesta listino o info."
                ),
                info_qualificazione=None,
            ))
            db.commit()

        if db.query(Societa).first():
            logger.info("DB già popolato, skip seed")
            return

        logger.info("Popolamento scenario HORECA demo...")
        now = datetime.utcnow()

        # --- Agenti ---
        giulia = Agente(nome="Giulia", cognome="Verdi", telefono="+393401112233",
                        email="g.verdi@distribuzione.it", zona="Lombardia", percentuale_provvigione=5.0)
        marco = Agente(nome="Marco", cognome="Bianchi", telefono="+393404445566",
                       email="m.bianchi@distribuzione.it", zona="Piemonte", percentuale_provvigione=4.0)
        db.add_all([giulia, marco])
        db.flush()

        # --- Locali + referenti ---
        gino = Societa(insegna="Trattoria da Gino", ragione_sociale="Gino Rossi S.n.c.",
                      tipo=TipoAttivita.RISTORANTE, citta="Milano", indirizzo="Via Brera 12",
                      piva="IT01234567890", stato_relazione=StatoRelazione.CLIENTE,
                      agente_referente_id=giulia.id)
        napoli = Societa(insegna="Pizzeria Napoli", ragione_sociale="Esposito S.r.l.",
                        tipo=TipoAttivita.PIZZERIA, citta="Milano", indirizzo="Corso Buenos Aires 88",
                        stato_relazione=StatoRelazione.CLIENTE, agente_referente_id=giulia.id)
        hotel = Societa(insegna="Hotel Belvedere", ragione_sociale="Belvedere Hotels S.p.A.",
                       tipo=TipoAttivita.HOTEL, citta="Torino", indirizzo="Corso Vittorio 200",
                       stato_relazione=StatoRelazione.PROSPECT, agente_referente_id=marco.id)
        bar = Societa(insegna="Bar Centrale", tipo=TipoAttivita.BAR, citta="Torino",
                     stato_relazione=StatoRelazione.PROSPECT, agente_referente_id=marco.id)
        db.add_all([gino, napoli, hotel, bar])
        db.flush()

        c_gino = Contatto(nome="Gino", cognome="Rossi", ruolo="Titolare", telefono="+393331234567",
                          email="gino@trattoriadagino.it", ragione_sociale=gino.ragione_sociale,
                          sede="Milano", stato=ContattoStato.CLIENTE, societa_id=gino.id, is_primario=True)
        c_luca = Contatto(nome="Luca", cognome="Ferri", ruolo="Chef", telefono="+393339998877",
                          ragione_sociale=gino.ragione_sociale, sede="Milano",
                          stato=ContattoStato.CLIENTE, societa_id=gino.id)
        c_antonio = Contatto(nome="Antonio", cognome="Esposito", ruolo="Titolare", telefono="+393332223344",
                             ragione_sociale=napoli.ragione_sociale, sede="Milano",
                             stato=ContattoStato.CLIENTE, societa_id=napoli.id, is_primario=True)
        c_sara = Contatto(nome="Sara", cognome="Conti", ruolo="F&B Manager", telefono="+393755116724",
                          email="sommojames@gmail.com", ragione_sociale=hotel.ragione_sociale,
                          sede="Torino", stato=ContattoStato.PROSPECT, societa_id=hotel.id, is_primario=True)
        db.add_all([c_gino, c_luca, c_antonio, c_sara])
        db.flush()

        # --- Ordini ---
        o1 = Ordine(societa_id=gino.id, contatto_id=c_gino.id, origine=OrigineOrdine.CLIENTE,
                    canale=CanaleOrdine.WHATSAPP, stato=StatoOrdine.EVASO,
                    data=now - timedelta(days=12), agente_id=giulia.id)
        o2 = Ordine(societa_id=gino.id, agente_id=giulia.id, origine=OrigineOrdine.AGENTE,
                    canale=CanaleOrdine.AGENTE, stato=StatoOrdine.CONFERMATO, data=now - timedelta(days=2))
        o3 = Ordine(societa_id=napoli.id, contatto_id=c_antonio.id, origine=OrigineOrdine.CLIENTE,
                    canale=CanaleOrdine.VOCE, stato=StatoOrdine.CONFERMATO, data=now - timedelta(days=1),
                    agente_id=giulia.id)
        db.add_all([o1, o2, o3])
        db.flush()
        db.add_all([
            RigaOrdine(ordine_id=o1.id, descrizione="Farina 00 tipo pizzeria", quantita=10, unita="sacchi 25kg", prezzo_unitario=18.5),
            RigaOrdine(ordine_id=o1.id, descrizione="Olio EVO", quantita=6, unita="latte 5L", prezzo_unitario=42.0),
            RigaOrdine(ordine_id=o1.id, descrizione="Pomodori pelati", quantita=4, unita="cartoni", prezzo_unitario=22.0),
            RigaOrdine(ordine_id=o2.id, descrizione="Vino rosso della casa", quantita=24, unita="bottiglie", prezzo_unitario=4.8),
            RigaOrdine(ordine_id=o2.id, descrizione="Caffè in grani", quantita=8, unita="kg", prezzo_unitario=16.0),
            RigaOrdine(ordine_id=o3.id, descrizione="Mozzarella fior di latte", quantita=20, unita="kg", prezzo_unitario=7.5),
            RigaOrdine(ordine_id=o3.id, descrizione="Farina 00 tipo pizzeria", quantita=15, unita="sacchi 25kg", prezzo_unitario=18.5),
        ])

        # --- Conversazioni / chiamate / ticket per la timeline ---
        db.add_all([
            MessaggioChat(contatto_id=c_gino.id, direzione=DirezioneMessaggio.IN,
                          testo="Ciao, mi servirebbe il riordino solito di farina e olio per giovedì.",
                          timestamp=now - timedelta(days=12, minutes=5)),
            MessaggioChat(contatto_id=c_gino.id, direzione=DirezioneMessaggio.OUT,
                          testo="Certo Gino! Confermo 10 sacchi di farina e 6 latte di olio EVO, consegna giovedì. Ti registro l'ordine.",
                          timestamp=now - timedelta(days=12, minutes=4)),
        ])
        db.add(ChiamataVoce(
            contatto_id=c_antonio.id, telefono=c_antonio.telefono, iniziata_at=now - timedelta(days=1, hours=2),
            durata_sec=145, riassunto="Antonio (Pizzeria Napoli) ordina mozzarella e farina per il weekend. Ordine confermato.",
            trascrizione="Cliente: Buongiorno, vorrei ordinare mozzarella e farina.\nAssistente: Volentieri, quanti kg di mozzarella?\nCliente: Venti kg, e quindici sacchi di farina.",
        ))
        db.add(Ticket(
            contatto_id=c_sara.id, canale="whatsapp",
            titolo="Richiesta listino e prima fornitura Hotel Belvedere",
            priorita=PrioritaTicket.ALTA,
            descrizione="Nuovo prospect a Torino, chiede listino completo e disponibilità consegna settimanale.",
            storia="Contatto: Salve, siamo l'Hotel Belvedere, vorremmo valutarvi come fornitore.\nAssistente: Con piacere! Le invio il listino e la passo all'agente di zona.",
            stato=StatoTicket.APERTO,
        ))

        db.commit()
        logger.info("Seed HORECA completato: 2 agenti, 4 locali, 4 contatti, 3 ordini")

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
