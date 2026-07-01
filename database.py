import enum
import os
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    ForeignKey, Enum, Text, Boolean,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# Path del DB configurabile via env (utile in Docker per puntare a un volume persistente).
# .strip() difensivo: uno spazio accidentale nel .env/secret romperebbe il parsing dell'URL.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/assistente.db").strip()

# check_same_thread è un connect_arg SOLO di SQLite: con Postgres va omesso.
_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
# Su Postgres/Supabase: pool_pre_ping controlla che la connessione sia viva prima di usarla
# (il pooler chiude le connessioni idle -> altrimenti query intermittenti falliscono);
# pool_recycle ricicla quelle più vecchie di 5 min.
_engine_kwargs = {} if _is_sqlite else {"pool_pre_ping": True, "pool_recycle": 300}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------- Enums ----------

class DirezioneMessaggio(str, enum.Enum):
    IN = "IN"
    OUT = "OUT"


class StatoTicket(str, enum.Enum):
    APERTO = "aperto"
    CHIUSO = "chiuso"


class PrioritaTicket(str, enum.Enum):
    ALTA = "alta"
    MEDIA = "media"
    BASSA = "bassa"


class ContattoStato(str, enum.Enum):
    CLIENTE = "cliente"
    PROSPECT = "prospect"


class StatoRelazione(str, enum.Enum):
    """Stato commerciale della SOCIETÀ (non della persona): è la società a essere
    prospect finché non arriva il primo ordine, poi diventa cliente."""
    PROSPECT = "prospect"
    CLIENTE = "cliente"
    INATTIVO = "inattivo"


class TipoAttivita(str, enum.Enum):
    RISTORANTE = "ristorante"
    PIZZERIA = "pizzeria"
    BAR = "bar"
    HOTEL = "hotel"
    GASTRONOMIA = "gastronomia"
    ALTRO = "altro"


class CanaleOrdine(str, enum.Enum):
    WHATSAPP = "whatsapp"
    VOCE = "voce"
    EMAIL = "email"
    AGENTE = "agente"
    MANUALE = "manuale"


class StatoOrdine(str, enum.Enum):
    BOZZA = "bozza"             # estratto dalla conversazione, da confermare
    CONFERMATO = "confermato"
    EVASO = "evaso"
    ANNULLATO = "annullato"


class OrigineOrdine(str, enum.Enum):
    CLIENTE = "cliente"        # inserito da un contatto del locale
    AGENTE = "agente"          # inserito da un agente di commercio


# ---------- Modelli ----------

class Azienda(Base):
    """Profilo dell'azienda che usa il risponditore + configurazione comportamentale
    (in linguaggio naturale) usata per rispondere, qualificare i lead e dare priorità.

    Singleton: esiste una sola riga. Modificabile dalla pagina Impostazioni.
    """
    __tablename__ = "azienda"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    telefono = Column(String(30))
    indirizzo = Column(String(300))

    # Testo libero: cosa offre l'azienda (prodotti/servizi, cosa fa e non fa, dove, come,
    # orari, tempi di consegna...). Il risponditore lo usa per rispondere ai lead.
    descrizione_servizi = Column(Text, nullable=True)
    # Testo libero: cosa caratterizza un lead a priorità alta / media / bassa.
    criteri_priorita = Column(Text, nullable=True)
    # Testo libero: info minime da raccogliere per qualificare il lead.
    info_qualificazione = Column(Text, nullable=True)
    # Istruzioni libere dell'amministratore, iniettate nel system prompt di tutti gli LLM
    # (voce, retriever) e in ElevenLabs via la dynamic var {{configurazione}}. È il prompt VOCALE.
    istruzioni_admin = Column(Text, nullable=True)
    # Prompt dell'agente WhatsApp (testo). Se vuoto, ricade sul prompt vocale (istruzioni_admin).
    prompt_whatsapp = Column(Text, nullable=True)
    # Regole commerciali e promozioni (prezzi, sconti, omaggi). Iniettate ovunque come le
    # istruzioni: l'assistente le applica sia rispondendo sui prezzi sia registrando ordini.
    regole_commerciali = Column(Text, nullable=True)
    # Formule del primo saluto vocale (ElevenLabs {{saluto}}). Segnaposto: {nome} {cognome} {azienda}.
    saluto = Column(Text, nullable=True)                 # chiamante riconosciuto (usa {nome}/{cognome})
    saluto_sconosciuto = Column(Text, nullable=True)     # chiamante non riconosciuto (no nome)
    # Numeri abilitati come amministratore (possono lasciare promemoria via voce). Editabile da dashboard.
    admin_telefoni = Column(Text, nullable=True)         # separati da virgola/spazio/a-capo


class Contatto(Base):
    """Cliente o potenziale cliente (prospect). Entità centrale: la home ne mostra la lista.
    L'anagrafica viene compilata a mano o dal risponditore durante una chiamata/chat."""
    __tablename__ = "contatti"

    id = Column(Integer, primary_key=True, index=True)
    titolo = Column(String(20))                # appellativo: "Signore" / "Signora" (opzionale)
    nome = Column(String(100))
    cognome = Column(String(100))
    ragione_sociale = Column(String(200))      # ragione sociale della società
    ruolo = Column(String(150))                # ruolo nella società
    email = Column(String(150))
    telefono = Column(String(30))
    sede = Column(String(200))                 # sede / località
    stato = Column(Enum(ContattoStato), default=ContattoStato.PROSPECT, nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # HORECA: la persona appartiene a una Società (ristorante/bar/hotel...). Nullable per
    # i contatti non ancora associati o creati al volo dal risponditore.
    # Nome fisico colonna invariato ("locale_id") per non migrare i DB esistenti.
    societa_id = Column("locale_id", Integer, ForeignKey("locali.id"), nullable=True, index=True)
    is_primario = Column(Boolean, default=False)   # referente principale della società

    societa = relationship("Societa", back_populates="contatti")

    messaggi = relationship(
        "MessaggioChat",
        back_populates="contatto",
        cascade="all, delete-orphan",
        order_by="MessaggioChat.timestamp",
    )
    chiamate = relationship(
        "ChiamataVoce",
        back_populates="contatto",
        cascade="all, delete-orphan",
        order_by="ChiamataVoce.iniziata_at.desc()",
    )
    ticket = relationship(
        "Ticket",
        back_populates="contatto",
        cascade="all, delete-orphan",
        order_by="Ticket.created_at.desc()",
    )

    @property
    def nome_completo(self) -> str:
        n = f"{self.nome or ''} {self.cognome or ''}".strip()
        return n or (self.ragione_sociale or "Contatto senza nome")


class MessaggioChat(Base):
    """Storia conversazione WhatsApp di un contatto (per il contesto)."""
    __tablename__ = "messaggi_chat"

    id = Column(Integer, primary_key=True, index=True)
    contatto_id = Column(Integer, ForeignKey("contatti.id"), nullable=False)
    direzione = Column(Enum(DirezioneMessaggio), nullable=False)   # IN = dal contatto, OUT = assistente
    testo = Column(Text, nullable=False)
    traccia = Column(Text, nullable=True)   # JSON: chiamate LLM (fase, input, output) del turno (sulle OUT)
    timestamp = Column(DateTime, default=datetime.utcnow)

    contatto = relationship("Contatto", back_populates="messaggi")


class ChiamataVoce(Base):
    """Log di una telefonata: trascrizione completa + riassunto."""
    __tablename__ = "chiamate_voce"

    id = Column(Integer, primary_key=True, index=True)
    contatto_id = Column(Integer, ForeignKey("contatti.id"), nullable=False)
    telefono = Column(String(30))
    iniziata_at = Column(DateTime, default=datetime.utcnow)
    durata_sec = Column(Integer, nullable=True)
    trascrizione = Column(Text, nullable=True)   # dialogo completo
    riassunto = Column(Text, nullable=True)       # riassunto generato dall'LLM

    contatto = relationship("Contatto", back_populates="chiamate")


class Ticket(Base):
    """Segnalazione / scheda di follow-up aperta dall'assistente (voce/WhatsApp) o a mano.
    Per ogni lead gestito si apre un ticket con titolo riassuntivo, priorità e trascrizione."""
    __tablename__ = "ticket"

    id = Column(Integer, primary_key=True, index=True)
    contatto_id = Column(Integer, ForeignKey("contatti.id"), nullable=True)
    canale = Column(String(20))                    # whatsapp | voce | dashboard
    titolo = Column(String(300), nullable=False)
    priorita = Column(Enum(PrioritaTicket), nullable=True, index=True)
    descrizione = Column(Text, nullable=True)      # sintesi della richiesta
    storia = Column(Text, nullable=True)           # storia chat / trascrizione chiamata
    stato = Column(Enum(StatoTicket), default=StatoTicket.APERTO, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    contatto = relationship("Contatto", back_populates="ticket")
    risposte = relationship(
        "RispostaTicket",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="RispostaTicket.created_at",
    )


class RispostaTicket(Base):
    """Risposta dell'operatore a un ticket (thread)."""
    __tablename__ = "risposte_ticket"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("ticket.id"), nullable=False)
    testo = Column(Text, nullable=False)
    inviata_email = Column(Boolean, default=False)   # inoltrata al contatto via email?
    created_at = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("Ticket", back_populates="risposte")


# ---------- HORECA: agenti, locali, ordini ----------

class Agente(Base):
    """Agente di commercio: gestisce un portafoglio di Locali e può inserire ordini
    per loro conto. L'ordine può essere attribuito a un agente per la provvigione."""
    __tablename__ = "agenti"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100))
    cognome = Column(String(100))
    telefono = Column(String(30))
    email = Column(String(150))
    zona = Column(String(150))                          # area di competenza
    percentuale_provvigione = Column(Float, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    societa = relationship("Societa", back_populates="agente_referente")
    ordini = relationship("Ordine", back_populates="agente")

    @property
    def nome_completo(self) -> str:
        n = f"{self.nome or ''} {self.cognome or ''}".strip()
        return n or "Agente senza nome"


class Societa(Base):
    """Il cliente HORECA: la società/locale (ristorante, bar, hotel...) che ordina e consuma.
    Entità aggregante: ha più contatti (persone) e più ordini. Lo stato commerciale
    (prospect/cliente) vive QUI, non sulla singola persona.

    Tabella fisica "locali" (invariata) per non migrare i DB esistenti."""
    __tablename__ = "locali"

    id = Column(Integer, primary_key=True, index=True)
    insegna = Column(String(200), nullable=False)       # nome (es. "Trattoria da Gino")
    ragione_sociale = Column(String(200))               # ragione sociale / P.IVA holder
    tipo = Column(Enum(TipoAttivita), default=TipoAttivita.RISTORANTE, nullable=False)
    piva = Column(String(20))
    indirizzo = Column(String(300))
    citta = Column(String(120), index=True)
    stato_relazione = Column(Enum(StatoRelazione), default=StatoRelazione.PROSPECT, nullable=False, index=True)
    agente_referente_id = Column(Integer, ForeignKey("agenti.id"), nullable=True, index=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    agente_referente = relationship("Agente", back_populates="societa")
    contatti = relationship(
        "Contatto",
        back_populates="societa",
        order_by="Contatto.is_primario.desc()",
    )
    ordini = relationship(
        "Ordine",
        back_populates="societa",
        cascade="all, delete-orphan",
        order_by="Ordine.data.desc()",
    )

    @property
    def nome(self) -> str:
        return self.insegna or self.ragione_sociale or "Società senza nome"


class Ordine(Base):
    """Ordine di una Società. È ancorato alla SOCIETÀ; la persona (contatto) e l'agente
    sono il 'chi/come'. Così ordini della stessa società da agente o da cliente diretto
    convergono sulla stessa scheda."""
    __tablename__ = "ordini"

    id = Column(Integer, primary_key=True, index=True)
    societa_id = Column("locale_id", Integer, ForeignKey("locali.id"), nullable=False, index=True)
    contatto_id = Column(Integer, ForeignKey("contatti.id"), nullable=True)   # persona che ha ordinato
    agente_id = Column(Integer, ForeignKey("agenti.id"), nullable=True)       # agente (inserimento e/o attribuzione)
    origine = Column(Enum(OrigineOrdine), default=OrigineOrdine.CLIENTE, nullable=False)
    canale = Column(Enum(CanaleOrdine), default=CanaleOrdine.MANUALE, nullable=False)
    stato = Column(Enum(StatoOrdine), default=StatoOrdine.BOZZA, nullable=False, index=True)
    data = Column(DateTime, default=datetime.utcnow, index=True)
    note = Column(Text, nullable=True)
    # Contesto/motivazione fornita dall'agente quando ordina per conto del cliente.
    # Opzionale in generale, obbligatorio (lato GUI) quando origine = AGENTE.
    descrizione_agente = Column(Text, nullable=True)

    societa = relationship("Societa", back_populates="ordini")
    contatto = relationship("Contatto")
    agente = relationship("Agente", back_populates="ordini")
    righe = relationship(
        "RigaOrdine",
        back_populates="ordine",
        cascade="all, delete-orphan",
        order_by="RigaOrdine.id",
    )

    @property
    def totale(self) -> float:
        return round(sum((r.subtotale or 0) for r in self.righe), 2)

    @property
    def n_articoli(self) -> int:
        return len(self.righe)


class RigaOrdine(Base):
    """Riga di un ordine: prodotto/descrizione + quantità + prezzo."""
    __tablename__ = "righe_ordine"

    id = Column(Integer, primary_key=True, index=True)
    ordine_id = Column(Integer, ForeignKey("ordini.id"), nullable=False)
    descrizione = Column(String(400), nullable=False)   # nome prodotto / descrizione libera
    quantita = Column(Float, default=1)
    unita = Column(String(30))                          # pz, kg, casse, bottiglie...
    prezzo_unitario = Column(Float, nullable=True)

    ordine = relationship("Ordine", back_populates="righe")

    @property
    def subtotale(self) -> float | None:
        if self.prezzo_unitario is None:
            return None
        return round((self.quantita or 0) * self.prezzo_unitario, 2)


# ---------- Documenti (parcheggiati: base di conoscenza per riuso futuro) ----------

class StatoDocumento(str, enum.Enum):
    PROCESSING = "processing"     # ingestion in corso
    READY = "ready"               # indice generato e validato
    NEEDS_REVIEW = "needs_review" # indice non validabile, output grezzo conservato
    ERROR = "error"               # estrazione fallita (es. PDF scansionato)


class Documento(Base):
    __tablename__ = "documenti"

    id = Column(Integer, primary_key=True, index=True)
    azienda_id = Column(Integer, ForeignKey("azienda.id"), nullable=True)
    categoria = Column(String(40), nullable=False)   # listino, schede_prodotto, contratti, faq, altro
    anno = Column(Integer, nullable=True, index=True) # anno di riferimento (per la catalogazione)
    nome_file = Column(String(300), nullable=False)  # nome originale del file
    percorso = Column(String(500), nullable=False)   # path su disco (transitorio, usato per l'indicizzazione)
    storage_path = Column(String(500), nullable=True)  # path su Supabase Storage (copia durevole dell'originale)
    n_pagine = Column(Integer, nullable=True)
    dimensione = Column(Integer, nullable=True)       # byte
    stato = Column(Enum(StatoDocumento), default=StatoDocumento.PROCESSING, nullable=False)
    errore = Column(Text, nullable=True)             # messaggio in caso di stato ERROR
    indice_raw = Column(Text, nullable=True)         # output grezzo del sezionatore (per ispezione / needs_review)
    riassunto = Column(Text, nullable=True)          # summary generato da AI (metadato per il retriever)
    inviabile = Column(Boolean, default=True, nullable=False)  # se l'assistente può inviarlo al cliente come allegato
    caricato_at = Column(DateTime, default=datetime.utcnow)

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


class DocumentoChunk(Base):
    """Pezzo di documento (PDF) per la ricerca semantica. Ogni chunk ha il suo embedding
    (lista di float serializzata in JSON) e i metadati denormalizzati per filtrare/citare:
    categoria (= sezione della dashboard) e range pagine. Popolato all'ingestion, in parallelo
    al salvataggio dell'originale su Supabase Storage."""
    __tablename__ = "documento_chunk"

    id = Column(Integer, primary_key=True, index=True)
    documento_id = Column(Integer, ForeignKey("documenti.id", ondelete="CASCADE"), nullable=False, index=True)
    sezione_id = Column(Integer, ForeignKey("sezioni.id", ondelete="CASCADE"), nullable=True)
    ordine = Column(Integer, nullable=False, default=0)   # ordine del chunk nel documento
    categoria = Column(String(40), index=True)            # denormalizzato dal documento (per filtro)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    testo = Column(Text, nullable=False)                  # testo del chunk
    embedding = Column(Text, nullable=True)               # JSON: lista di float

    documento = relationship("Documento")


class DocumentoColonna(Base):
    """Una colonna di un file tabellare (CSV/Excel) con i suoi FACET, per guidare l'agente nelle
    interrogazioni strutturate. `esaustivo`=True quando i valori distinti sono ≤ soglia (lista
    completa, sicura per un filtro esatto); altrimenti `distinti` è solo un CAMPIONE."""
    __tablename__ = "documento_colonna"

    id = Column(Integer, primary_key=True, index=True)
    documento_id = Column(Integer, ForeignKey("documenti.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = Column(String(200), nullable=False)
    tipo = Column(String(20))                 # 'numero' | 'testo' | 'data'
    n_distinti = Column(Integer, default=0)
    esaustivo = Column(Boolean, default=True)  # i 'distinti' sono la lista COMPLETA (non un campione)
    distinti = Column(Text)                    # JSON: lista di valori distinti (o campione se non esaustivo)
    min_val = Column(String(120))              # per le colonne numeriche
    max_val = Column(String(120))

    documento = relationship("Documento")


class DocumentoRiga(Base):
    """Una riga di un file tabellare (CSV/Excel), dati come dizionario JSON colonna->valore."""
    __tablename__ = "documento_riga"

    id = Column(Integer, primary_key=True, index=True)
    documento_id = Column(Integer, ForeignKey("documenti.id", ondelete="CASCADE"), nullable=False, index=True)
    ordine = Column(Integer, nullable=False, default=0)
    dati = Column(Text, nullable=False)        # JSON: {colonna: valore}

    documento = relationship("Documento")


class TestoCategoria(Base):
    """Testo libero che l'amministratore associa a una categoria di documenti.

    Accompagna i documenti caricati (listini, FAQ, contratti…): note, precisazioni,
    risposte tipo. Una sola riga per categoria. Il risponditore (in futuro) lo
    consulterà insieme ai documenti indicizzati per rispondere all'utente.
    """
    __tablename__ = "testi_categoria"

    id = Column(Integer, primary_key=True, index=True)
    azienda_id = Column(Integer, ForeignKey("azienda.id"), nullable=True)
    categoria = Column(String(40), nullable=False, unique=True, index=True)
    testo = Column(Text, nullable=True)
    aggiornato_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Promemoria(Base):
    """Nota mirata che l'amministratore lascia per un CONTATTO specifico: quando quel cliente
    chiama (o scrive), il testo viene iniettato nel contesto dell'assistente, che ne tiene conto
    (es. comunicargli un'offerta). Può avere una scadenza. Si gestisce da dashboard o via voce."""
    __tablename__ = "promemoria"

    id = Column(Integer, primary_key=True, index=True)
    contatto_id = Column(Integer, ForeignKey("contatti.id"), nullable=False, index=True)
    testo = Column(Text, nullable=False)
    scade_il = Column(DateTime, nullable=True)            # nota valida fino a... (None = senza scadenza)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    contatto = relationship("Contatto")


class Amministratore(Base):
    """Numero abilitato come amministratore: chi chiama da qui può lasciare promemoria per i
    clienti via voce. Censiti dalla dashboard (come i contatti)."""
    __tablename__ = "amministratori"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(150))
    telefono = Column(String(30), nullable=False, index=True)
    email = Column(String(150))
    # Inoltro dei nuovi ticket via email a questo admin, per priorità:
    inoltra_alta = Column(Boolean, default=False, nullable=False)
    inoltra_media = Column(Boolean, default=False, nullable=False)
    inoltra_bassa = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class GoogleCalendar(Base):
    """Connessione OAuth 2.0 al Google Calendar (token per creare eventi). Una riga per la demo
    (single-tenant); i token sono segreti e stanno solo qui nel backend."""
    __tablename__ = "google_calendar"

    id = Column(Integer, primary_key=True, index=True)
    azienda_id = Column(Integer, ForeignKey("azienda.id"), nullable=True)
    email = Column(String(200))                      # account Google connesso (per mostrarlo in GUI)
    calendar_id = Column(String(200), default="primary")
    access_token = Column(Text)
    refresh_token = Column(Text)                      # long-lived: serve per rinnovare l'access token
    scad = Column(DateTime)                           # scadenza dell'access token
    connesso_at = Column(DateTime, default=datetime.utcnow)


class Inoltro(Base):
    """Persona a cui l'assistente può INOLTRARE la chiamata (es. responsabile spedizioni).
    `regole` descrive in linguaggio naturale quando inoltrare a questa persona. La rubrica e le
    regole vengono iniettate nel prompt; il trasferimento vero lo fa la telefonia (ElevenLabs)."""
    __tablename__ = "inoltri"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100))
    cognome = Column(String(100))
    ruolo = Column(String(150))
    email = Column(String(150))
    telefono = Column(String(30), nullable=False)
    regole = Column(Text)                 # quando inoltrare a questa persona (testo libero)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def nome_completo(self) -> str:
        return f"{self.nome or ''} {self.cognome or ''}".strip() or "(senza nome)"


# ---------- Helpers ----------

def get_db():
    """Dependency per FastAPI: fornisce una sessione DB."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Colonne aggiunte a tabelle preesistenti: create_all NON le aggiunge ai DB già creati,
# quindi le applichiamo a mano (ALTER TABLE) all'avvio. (table, colonna, DDL).
_MIGRAZIONI_COLONNE = [
    ("contatti", "locale_id", "INTEGER"),
    ("contatti", "is_primario", "BOOLEAN DEFAULT 0"),
    ("ordini", "descrizione_agente", "TEXT"),
]


def _migra_colonne(engine):
    """Aggiunge a SQLite le colonne mancanti su tabelle già esistenti (migrazione leggera)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    tabelle = set(insp.get_table_names())
    with engine.begin() as conn:
        for tabella, colonna, ddl in _MIGRAZIONI_COLONNE:
            if tabella not in tabelle:
                continue
            esistenti = {c["name"] for c in insp.get_columns(tabella)}
            if colonna not in esistenti:
                conn.execute(text(f"ALTER TABLE {tabella} ADD COLUMN {colonna} {ddl}"))


def init_db():
    """Crea tutte le tabelle e applica le migrazioni leggere di colonna."""
    # Assicura che la cartella del DB SQLite esista (es. ./data).
    if DATABASE_URL.startswith("sqlite:///"):
        path = DATABASE_URL.replace("sqlite:///", "", 1)
        cartella = os.path.dirname(path)
        if cartella:
            os.makedirs(cartella, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    # La micro-migrazione ALTER TABLE è scritta per il vecchio DB SQLite; su Postgres
    # le colonne nascono già da create_all, quindi la saltiamo.
    if DATABASE_URL.startswith("sqlite"):
        _migra_colonne(engine)
