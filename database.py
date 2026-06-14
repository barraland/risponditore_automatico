import enum
import os
from datetime import datetime, date

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Date,
    ForeignKey, Enum, Text, Boolean,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# Path del DB configurabile via env (utile in Docker per puntare a un volume persistente).
# Default invariato: file dentista.db nella working directory.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dentista.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------- Enums ----------

class StatoAppuntamento(str, enum.Enum):
    PRENOTATO = "PRENOTATO"
    CONFERMATO = "CONFERMATO"
    COMPLETATO = "COMPLETATO"
    CANCELLATO = "CANCELLATO"
    NO_SHOW = "NO_SHOW"


class StatoRichiamo(str, enum.Enum):
    DA_INVIARE = "DA_INVIARE"
    INVIATO = "INVIATO"
    PRENOTATO = "PRENOTATO"
    IGNORATO = "IGNORATO"


class DirezioneMessaggio(str, enum.Enum):
    IN = "IN"
    OUT = "OUT"


class StatoTicket(str, enum.Enum):
    APERTO = "aperto"
    CHIUSO = "chiuso"


# ---------- Models ----------

class Studio(Base):
    __tablename__ = "studi"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    telefono = Column(String(30))
    indirizzo = Column(String(300))
    orario_apertura = Column(String(5), default="09:00")
    orario_chiusura = Column(String(5), default="18:00")
    giorni_lavorativi = Column(String(50), default="lun,mar,mer,gio,ven")
    nome_dottore = Column(String(200), nullable=True)
    durata_slot_default = Column(Integer, default=30)
    max_giorni_prenotazione = Column(Integer, default=14)


class Servizio(Base):
    __tablename__ = "servizi"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(150), nullable=False)
    durata_minuti = Column(Integer, nullable=False, default=30)
    prezzo = Column(Float, default=0.0)
    intervallo_richiamo_giorni = Column(Integer, nullable=True)
    max_giorni_prenotazione = Column(Integer, nullable=True)
    descrizione_triage = Column(Text, nullable=True)

    appuntamenti = relationship("Appuntamento", back_populates="servizio")
    richiami = relationship("Richiamo", back_populates="servizio")


class Paziente(Base):
    __tablename__ = "pazienti"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False)
    cognome = Column(String(100), nullable=False)
    telefono = Column(String(20), unique=True, nullable=False)
    whatsapp_id = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    appuntamenti = relationship("Appuntamento", back_populates="paziente")
    richiami = relationship("Richiamo", back_populates="paziente")
    messaggi = relationship("MessaggioLog", back_populates="paziente")


class Appuntamento(Base):
    __tablename__ = "appuntamenti"

    id = Column(Integer, primary_key=True, index=True)
    paziente_id = Column(Integer, ForeignKey("pazienti.id"), nullable=False)
    servizio_id = Column(Integer, ForeignKey("servizi.id"), nullable=False)
    data_ora = Column(DateTime, nullable=False)
    durata_minuti = Column(Integer, nullable=False, default=30)
    stato = Column(Enum(StatoAppuntamento), default=StatoAppuntamento.PRENOTATO)
    google_event_id = Column(String(200), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    paziente = relationship("Paziente", back_populates="appuntamenti")
    servizio = relationship("Servizio", back_populates="appuntamenti")


class Richiamo(Base):
    __tablename__ = "richiami"

    id = Column(Integer, primary_key=True, index=True)
    paziente_id = Column(Integer, ForeignKey("pazienti.id"), nullable=False)
    servizio_id = Column(Integer, ForeignKey("servizi.id"), nullable=False)
    data_prevista = Column(Date, nullable=False)
    stato = Column(Enum(StatoRichiamo), default=StatoRichiamo.DA_INVIARE)
    messaggio_inviato_at = Column(DateTime, nullable=True)

    paziente = relationship("Paziente", back_populates="richiami")
    servizio = relationship("Servizio", back_populates="richiami")


class MessaggioLog(Base):
    __tablename__ = "messaggi_log"

    id = Column(Integer, primary_key=True, index=True)
    paziente_id = Column(Integer, ForeignKey("pazienti.id"), nullable=True)
    direzione = Column(Enum(DirezioneMessaggio), nullable=False)
    testo = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    whatsapp_message_id = Column(String(100), nullable=True)

    paziente = relationship("Paziente", back_populates="messaggi")


# ---------- Modelli Amministrazione Condomini ----------

class Condominio(Base):
    __tablename__ = "condomini"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    indirizzo = Column(String(300))
    citta = Column(String(120))
    cap = Column(String(10))
    codice_fiscale = Column(String(20))
    iban = Column(String(40))
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    inquilini = relationship(
        "Inquilino",
        back_populates="condominio",
        cascade="all, delete-orphan",
        order_by="Inquilino.cognome",
    )
    documenti = relationship(
        "Documento",
        back_populates="condominio",
        cascade="all, delete-orphan",
        order_by="Documento.caricato_at.desc()",
    )
    ticket = relationship(
        "Ticket",
        back_populates="condominio",
        cascade="all, delete-orphan",
        order_by="Ticket.created_at.desc()",
    )


class Inquilino(Base):
    __tablename__ = "inquilini"

    id = Column(Integer, primary_key=True, index=True)
    condominio_id = Column(Integer, ForeignKey("condomini.id"), nullable=False)
    nome = Column(String(100), nullable=False)
    cognome = Column(String(100))
    unita = Column(String(120))          # es. "Scala A - Interno 3"
    millesimi = Column(Float, nullable=True)
    telefono = Column(String(30))
    email = Column(String(150))
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Stato "offerta documento via email" (gestito dall'agente WhatsApp)
    offerta_doc_id = Column(Integer, nullable=True)               # documento offerto, in attesa di conferma
    offerta_attende_email = Column(Boolean, default=False)        # True = in attesa che il condomino indichi l'email

    condominio = relationship("Condominio", back_populates="inquilini")
    messaggi = relationship(
        "MessaggioChat",
        back_populates="inquilino",
        cascade="all, delete-orphan",
        order_by="MessaggioChat.timestamp",
    )
    chiamate = relationship(
        "ChiamataVoce",
        back_populates="inquilino",
        cascade="all, delete-orphan",
        order_by="ChiamataVoce.iniziata_at.desc()",
    )
    invii = relationship(
        "InvioDocumentoEmail",
        back_populates="inquilino",
        cascade="all, delete-orphan",
    )
    ticket = relationship(
        "Ticket",
        back_populates="inquilino",
        cascade="all, delete-orphan",
    )


class MessaggioChat(Base):
    """Storia conversazione WhatsApp di un inquilino (per il contesto)."""
    __tablename__ = "messaggi_chat"

    id = Column(Integer, primary_key=True, index=True)
    inquilino_id = Column(Integer, ForeignKey("inquilini.id"), nullable=False)
    direzione = Column(Enum(DirezioneMessaggio), nullable=False)   # IN = dal condomino, OUT = assistente
    testo = Column(Text, nullable=False)
    traccia = Column(Text, nullable=True)   # JSON: chiamate LLM (fase, input, output) del turno (sulle OUT)
    timestamp = Column(DateTime, default=datetime.utcnow)

    inquilino = relationship("Inquilino", back_populates="messaggi")


class ChiamataVoce(Base):
    """Log di una telefonata: trascrizione completa + riassunto."""
    __tablename__ = "chiamate_voce"

    id = Column(Integer, primary_key=True, index=True)
    inquilino_id = Column(Integer, ForeignKey("inquilini.id"), nullable=False)
    telefono = Column(String(30))
    iniziata_at = Column(DateTime, default=datetime.utcnow)
    durata_sec = Column(Integer, nullable=True)
    trascrizione = Column(Text, nullable=True)   # dialogo completo
    riassunto = Column(Text, nullable=True)       # riassunto generato dall'LLM

    inquilino = relationship("Inquilino", back_populates="chiamate")


class InvioDocumentoEmail(Base):
    """Storico dei documenti inviati via email a un inquilino (per non rioffrirli)."""
    __tablename__ = "invii_documento_email"

    id = Column(Integer, primary_key=True, index=True)
    inquilino_id = Column(Integer, ForeignKey("inquilini.id"), nullable=False)
    documento_id = Column(Integer, index=True, nullable=False)   # no FK: lo storico resta anche se il doc viene rimosso
    email = Column(String(150))
    inviato_at = Column(DateTime, default=datetime.utcnow)

    inquilino = relationship("Inquilino", back_populates="invii")


class Ticket(Base):
    """Segnalazione aperta dall'assistente (voce/WhatsApp) quando non sa rispondere
    o il condomino si lamenta. Visibile in dashboard finché è aperta."""
    __tablename__ = "ticket"

    id = Column(Integer, primary_key=True, index=True)
    condominio_id = Column(Integer, ForeignKey("condomini.id"), nullable=True)
    inquilino_id = Column(Integer, ForeignKey("inquilini.id"), nullable=True)
    canale = Column(String(20))                    # whatsapp | voce | dashboard
    titolo = Column(String(300), nullable=False)
    descrizione = Column(Text, nullable=True)
    storia = Column(Text, nullable=True)           # storia chat / trascrizione chiamata
    stato = Column(Enum(StatoTicket), default=StatoTicket.APERTO, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    condominio = relationship("Condominio", back_populates="ticket")
    inquilino = relationship("Inquilino", back_populates="ticket")
    risposte = relationship(
        "RispostaTicket",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="RispostaTicket.created_at",
    )


class RispostaTicket(Base):
    """Risposta dell'amministratore a un ticket (thread)."""
    __tablename__ = "risposte_ticket"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("ticket.id"), nullable=False)
    testo = Column(Text, nullable=False)
    inviata_email = Column(Boolean, default=False)   # inoltrata al condomino via email?
    created_at = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("Ticket", back_populates="risposte")


class StatoDocumento(str, enum.Enum):
    PROCESSING = "processing"     # ingestion in corso
    READY = "ready"               # indice generato e validato
    NEEDS_REVIEW = "needs_review" # indice non validabile, output grezzo conservato
    ERROR = "error"               # estrazione fallita (es. PDF scansionato)


class Documento(Base):
    __tablename__ = "documenti"

    id = Column(Integer, primary_key=True, index=True)
    condominio_id = Column(Integer, ForeignKey("condomini.id"), nullable=False)
    categoria = Column(String(40), nullable=False)   # chiave: verbali, bilanci, regolamento, ...
    anno = Column(Integer, nullable=True, index=True) # anno di riferimento (per la catalogazione)
    nome_file = Column(String(300), nullable=False)  # nome originale del file
    percorso = Column(String(500), nullable=False)   # path su disco (PDF originale, sempre conservato)
    n_pagine = Column(Integer, nullable=True)
    dimensione = Column(Integer, nullable=True)       # byte
    stato = Column(Enum(StatoDocumento), default=StatoDocumento.PROCESSING, nullable=False)
    errore = Column(Text, nullable=True)             # messaggio in caso di stato ERROR
    indice_raw = Column(Text, nullable=True)         # output grezzo del sezionatore (per ispezione / needs_review)
    caricato_at = Column(DateTime, default=datetime.utcnow)

    condominio = relationship("Condominio", back_populates="documenti")
    sezioni = relationship(
        "Sezione",
        back_populates="documento",
        cascade="all, delete-orphan",
        order_by="Sezione.ordine",
    )


class Sezione(Base):
    __tablename__ = "sezioni"

    id = Column(Integer, primary_key=True, index=True)
    documento_id = Column(Integer, ForeignKey("documenti.id"), nullable=False)
    ordine = Column(Integer, nullable=False)            # 0-based, ordine nel documento
    titolo = Column(String(400), nullable=False)
    summary = Column(Text, nullable=True)               # 2-3 frasi: cosa contiene, a quali domande risponde
    page_start = Column(Integer, nullable=False)        # 1-based inclusivo
    page_end = Column(Integer, nullable=False)          # 1-based inclusivo
    contiene_tabelle = Column(Boolean, default=False)
    content_md = Column(Text, nullable=True)            # testo integrale delle pagine del range, con marcatori

    documento = relationship("Documento", back_populates="sezioni")


# ---------- Helpers ----------

def get_db():
    """Dependency per FastAPI: fornisce una sessione DB."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Crea tutte le tabelle."""
    Base.metadata.create_all(bind=engine)
