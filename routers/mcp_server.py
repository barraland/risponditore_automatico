"""Server MCP per agenti vocali esterni (es. ElevenLabs Conversational AI).

Espone gli stessi strumenti dell'assistente Realtime, ma in versione STATELESS:
non c'è la sessione WebSocket in memoria, quindi il chiamante è identificato dal
suo numero di telefono (`telefono`), che l'agente esterno passa come parametro
(da ElevenLabs: la dynamic variable `system__caller_id`).

Riusa i service esistenti (crm, retriever, documenti, ticket, email) e la stessa
logica di find-or-create del contatto del canale WhatsApp.

Montato su /mcp dall'app principale; transport Streamable HTTP (stateless).
"""

import logging
import os

from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from database import (
    SessionLocal, Contatto, ContattoStato, Ordine,
    CanaleOrdine, OrigineOrdine, StatoOrdine,
    Documento, StatoDocumento, TestoCategoria,
)
from services import crm
from services import documenti as documenti_service
from services import ticket as ticket_service
from services import profilo
from services import email as email_service
from services import whatsapp_agent

logger = logging.getLogger(__name__)

# DNS-rebinding protection: FastMCP la attiva di default consentendo solo host localhost,
# il che rifiuta (421 "Invalid host header") le richieste arrivate via ngrok/ElevenLabs.
# Default qui: disattivata (l'app è dietro ngrok ed è raggiunta server-to-server; usa
# MCP_AUTH_TOKEN per l'autenticazione). Per bloccarla a domini specifici, imposta
# MCP_ALLOWED_HOSTS="dominio1,dominio2" nel .env.
_hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
if _hosts:
    _security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_hosts,
        allowed_origins=[f"https://{h}" for h in _hosts] + [f"http://{h}" for h in _hosts],
    )
else:
    _security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

# stateless_http=True: ogni richiesta è indipendente (nessuna sessione MCP persistente),
# perfetto per un agente telefonico esterno. streamable_http_path="/" così, montato su
# /mcp, l'endpoint finale è proprio /mcp.
mcp = FastMCP("risponditore-horeca", stateless_http=True, streamable_http_path="/",
              transport_security=_security)


def _contatto(db, telefono: str) -> Contatto:
    """Identifica (o crea) il contatto dal numero, come fa il canale WhatsApp."""
    return whatsapp_agent.trova_o_crea_contatto(db, telefono or "sconosciuto")


class RigaOrdineInput(BaseModel):
    descrizione: str = Field(description="Nome del prodotto.")
    quantita: float | None = Field(default=None, description="Quantità ordinata.")
    unita: str = Field(default="", description="Unità di misura (pz, kg, casse, bottiglie...).")
    prezzo_unitario: float | None = Field(default=None, description="Prezzo unitario se noto.")


def _log_tool(tool: str, **kv):
    """Log conciso di una chiamata tool MCP: nome del tool + parametri d'ingresso.

    NB: il primo parametro è `tool` (non `nome`) per non collidere con un eventuale
    parametro `nome=` loggato dai tool (es. salva_contatto passa nome=...).
    """
    parti = " ".join(f"{k}={v}" for k, v in kv.items() if v not in (None, "", []))
    logger.info("🔧 MCP tool %s | %s", tool, parti or "—")


# ---------- Tools ----------

def _leggi_categoria(categoria: str) -> dict:
    """Ritorna il testo integrale di TUTTI i documenti di una categoria + la nota libera
    dell'amministratore per quella categoria. Nessun LLM: lettura diretta dal DB (veloce).
    L'agente legge il contenuto e risponde da sé."""
    db = SessionLocal()
    try:
        blocchi = []
        nota = db.query(TestoCategoria).filter(TestoCategoria.categoria == categoria).first()
        if nota and nota.testo and nota.testo.strip():
            blocchi.append(f"NOTA DELL'AMMINISTRATORE:\n{nota.testo.strip()}")
        docs = (db.query(Documento)
                .filter(Documento.categoria == categoria,
                        Documento.stato.in_([StatoDocumento.READY, StatoDocumento.NEEDS_REVIEW]))
                .order_by(Documento.caricato_at.desc()).all())
        for d in docs:
            testo = "\n".join((s.content_md or "") for s in sorted(d.sezioni, key=lambda s: s.ordine)).strip()
            if testo:
                blocchi.append(f"=== {d.nome_file} ===\n{testo}")
        if not blocchi:
            return {"trovato": False, "contenuto": f"Nessun documento disponibile nella categoria «{categoria}»."}
        return {"trovato": True, "contenuto": "\n\n".join(blocchi)}
    finally:
        db.close()


@mcp.tool()
def leggi_listini_prezzi() -> dict:
    """Restituisce per intero i LISTINI e i PREZZI caricati. Usalo quando il cliente chiede
    quanto costa un prodotto, sconti di listino, formati/confezioni e relativi prezzi."""
    _log_tool("leggi_listini_prezzi")
    return _leggi_categoria("listino")


@mcp.tool()
def leggi_condizioni_vendita() -> dict:
    """Restituisce per intero le CONDIZIONI DI VENDITA e i contratti: tempi e modalità di
    consegna, ordine minimo, modalità di pagamento, termini contrattuali."""
    _log_tool("leggi_condizioni_vendita")
    return _leggi_categoria("contratti")


@mcp.tool()
def leggi_schede_prodotto() -> dict:
    """Restituisce per intero le SCHEDE PRODOTTO/SERVIZIO: caratteristiche, formati, dettagli
    tecnici, ingredienti/specifiche dei prodotti."""
    _log_tool("leggi_schede_prodotto")
    return _leggi_categoria("schede_prodotto")


@mcp.tool()
def leggi_faq() -> dict:
    """Restituisce per intero le FAQ e il materiale informativo generale (domande frequenti,
    informazioni sull'azienda e sul servizio)."""
    _log_tool("leggi_faq")
    return _leggi_categoria("faq")


@mcp.tool()
def leggi_altri_documenti() -> dict:
    """Restituisce per intero i documenti della categoria «altro» (non classificati nelle
    categorie precedenti)."""
    _log_tool("leggi_altri_documenti")
    return _leggi_categoria("altro")


def _applica_contatto(telefono: str, nome: str, cognome: str, ragione_sociale: str,
                      ruolo: str, email: str, sede: str, stato: str) -> dict:
    """Crea/aggiorna il contatto identificato da `telefono`, scrivendo solo i campi non vuoti.
    Logica condivisa da salva_contatto (prima registrazione) e aggiorna_contatto (info nuove)."""
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        campi = {"nome": nome, "cognome": cognome, "ragione_sociale": ragione_sociale,
                 "ruolo": ruolo, "email": email, "sede": sede}
        cambiato = False
        for k, v in campi.items():
            v = (v or "").strip()
            if v and getattr(c, k) != v:
                setattr(c, k, v)
                cambiato = True
        st = (stato or "").strip().lower()
        if st in (ContattoStato.CLIENTE.value, ContattoStato.PROSPECT.value):
            nuovo = ContattoStato(st)
            if c.stato != nuovo:
                c.stato = nuovo
                cambiato = True
        if cambiato:
            db.commit()
        societa = crm.societa_di_contatto(db, c)
        return {"ok": True, "contatto_id": c.id, "aggiornato": cambiato,
                "societa": societa.nome if societa else None}
    finally:
        db.close()


@mcp.tool()
def salva_contatto(telefono: str, nome: str = "", cognome: str = "", ragione_sociale: str = "",
                   ruolo: str = "", email: str = "", sede: str = "", stato: str = "") -> dict:
    """Registra in anagrafica il chiamante (identificato da `telefono`): usalo SUBITO alla prima
    registrazione di un prospect non ancora in rubrica, appena hai almeno il nome.
    Passa SOLO i campi che il cliente ha detto esplicitamente; quelli omessi restano invariati.
    NON inventare né dedurre valori: se un dato (es. città/sede, email, ruolo) non è stato
    dichiarato, ometti del tutto il campo. Tutti i campi tranne `telefono` sono opzionali.
    Se emerge la ragione sociale, crea/aggancia automaticamente la società del cliente."""
    _log_tool("salva_contatto", telefono=telefono, nome=nome, email=email, ragione_sociale=ragione_sociale)
    return _applica_contatto(telefono, nome, cognome, ragione_sociale, ruolo, email, sede, stato)


@mcp.tool()
def aggiorna_contatto(telefono: str, nome: str = "", cognome: str = "", ragione_sociale: str = "",
                      ruolo: str = "", email: str = "", sede: str = "", stato: str = "") -> dict:
    """Aggiorna i dati di un contatto GIÀ registrato (identificato da `telefono`) quando emergono
    informazioni nuove durante la conversazione — tipicamente la CITTÀ/sede se non era stata detta
    subito, oppure email, ruolo, ragione sociale. Passa SOLO i campi nuovi appena appresi; gli altri
    restano invariati. NON inventare valori. Identico a salva_contatto ma da usare per gli aggiornamenti
    incrementali in corso di chiamata."""
    _log_tool("aggiorna_contatto", telefono=telefono, sede=sede, email=email, ragione_sociale=ragione_sociale)
    return _applica_contatto(telefono, nome, cognome, ragione_sociale, ruolo, email, sede, stato)


@mcp.tool()
def registra_ordine(telefono: str, righe: list[RigaOrdineInput], note: str = "",
                    conferma: bool = False) -> dict:
    """Registra un ordine del chiamante. `conferma`=true lo salva come CONFERMATO, altrimenti
    come bozza (segui le indicazioni dell'amministratore su quando confermare). Se per la stessa
    trattativa esiste già una bozza, la aggiorna invece di duplicarla."""
    _log_tool("registra_ordine", telefono=telefono, n_righe=len(righe), conferma=conferma)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        societa = crm.societa_di_contatto(db, c) or crm.trova_o_crea_societa(db, insegna=c.nome_completo)
        if not c.societa_id:
            c.societa_id = societa.id
            c.is_primario = True
            db.commit()
        ordine, creato = crm.registra_ordine_conversazione(
            db, societa_id=societa.id, righe=[r.model_dump() for r in righe], contatto_id=c.id,
            origine=OrigineOrdine.CLIENTE, canale=CanaleOrdine.VOCE,
            note=(note or "").strip() or None,
            stato=StatoOrdine.CONFERMATO if conferma else StatoOrdine.BOZZA,
        )
        if not ordine:
            return {"errore": "Registrazione ordine non riuscita."}
        return {"registrato": True, "aggiornato": not creato, "ordine_id": ordine.id,
                "stato": ordine.stato.value, "articoli": ordine.n_articoli, "totale": ordine.totale}
    finally:
        db.close()


@mcp.tool()
def invia_riepilogo_ordine(telefono: str, ordine_id: int = 0) -> dict:
    """Invia via email al chiamante il riepilogo di un ordine (ordine_id, oppure l'ultimo).
    Se il cliente non ha un'email salvata, lo segnala: chiedila e salvala con salva_contatto."""
    _log_tool("invia_riepilogo_ordine", telefono=telefono, ordine_id=ordine_id)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        ordine = db.get(Ordine, ordine_id) if ordine_id else None
        if not ordine:
            ordine = (db.query(Ordine).filter(Ordine.contatto_id == c.id)
                      .order_by(Ordine.data.desc()).first())
        if not ordine:
            return {"errore": "Nessun ordine da riepilogare."}
        email = (c.email or "").strip()
        if not email:
            return {"email_mancante": True,
                    "messaggio": "Chiedi l'email al cliente, salvala con salva_contatto e riprova."}
        oggetto = f"Riepilogo ordine #{ordine.id} - {profilo.nome_azienda(db)}"
        corpo = (f"Gentile {c.nome or c.nome_completo},\n\n"
                 f"come da accordi telefonici, le confermiamo il suo ordine:\n\n"
                 f"{crm.riepilogo_ordine(ordine)}\n\n"
                 f"Cordiali saluti,\n{profilo.nome_azienda(db)}")
        inviata = email_service.invia_email(destinatario=email, oggetto=oggetto, corpo=corpo)
        return ({"inviato": True, "email": email, "ordine_id": ordine.id} if inviata
                else {"errore": "Invio email non riuscito (verifica configurazione Gmail)."})
    finally:
        db.close()


@mcp.tool()
def invia_documento(telefono: str, categoria: str) -> dict:
    """Invia via email al chiamante i documenti caricati di una categoria
    (una tra: listino, schede_prodotto, contratti, faq, altro). Se manca l'email, lo segnala."""
    _log_tool("invia_documento", telefono=telefono, categoria=categoria)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        return documenti_service.invia_documenti_email(db, c, categoria, profilo.nome_azienda(db))
    finally:
        db.close()


@mcp.tool()
def apri_ticket(telefono: str, titolo: str, descrizione: str = "", priorita: str = "",
                trascrizione: str = "") -> dict:
    """Apre (o aggiorna) un ticket di follow-up per il chiamante, per il team commerciale.
    Passa titolo, una descrizione della richiesta, la priorità (alta/media/bassa) e, se la hai,
    la trascrizione/sintesi della conversazione."""
    _log_tool("apri_ticket", telefono=telefono, titolo=titolo, priorita=priorita)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        esistente = whatsapp_agent._ticket_aperto(db, c.id)
        if esistente:
            esistente.titolo = (titolo or esistente.titolo).strip()[:300]
            p = ticket_service.normalizza_priorita(priorita)
            if p:
                esistente.priorita = p
            esistente.descrizione = (descrizione or "").strip() or esistente.descrizione
            if (trascrizione or "").strip():
                esistente.storia = trascrizione.strip()
            db.commit()
            return {"aperto": True, "ticket_id": esistente.id, "aggiornato": True}
        t = ticket_service.apri_ticket(
            db, contatto_id=c.id, titolo=titolo or "Lead telefonico", priorita=priorita,
            descrizione=descrizione, storia=trascrizione, canale="voce")
        return {"aperto": bool(t), "ticket_id": t.id if t else None}
    finally:
        db.close()


# Inizializza l'app Streamable HTTP (crea il session_manager, usato nel lifespan dell'app).
http_app = mcp.streamable_http_app()
