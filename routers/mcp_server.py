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
)
from services import crm
from services import retriever
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


def _log_tool(nome: str, **kv):
    """Log conciso di una chiamata tool MCP: nome + parametri d'ingresso."""
    parti = " ".join(f"{k}={v}" for k, v in kv.items() if v not in (None, "", []))
    logger.info("🔧 MCP tool %s | %s", nome, parti or "—")


# ---------- Tools ----------

@mcp.tool()
def consulta_documenti(domanda: str) -> dict:
    """Consulta i documenti caricati (listini, schede prodotto, condizioni di consegna, FAQ)
    per rispondere a una domanda su prezzi, condizioni o dettagli. Ritorna una risposta
    sintetica basata sui documenti, da riferire al chiamante."""
    _log_tool("consulta_documenti", domanda=domanda)
    db = SessionLocal()
    try:
        esito = retriever.rispondi(db, domanda)
        return {"risposta": esito.get("risposta", ""),
                "fonti": [f.get("documento") for f in (esito.get("fonti") or [])]}
    finally:
        db.close()


@mcp.tool()
def salva_contatto(telefono: str, nome: str = "", cognome: str = "", ragione_sociale: str = "",
                   ruolo: str = "", email: str = "", sede: str = "", stato: str = "") -> dict:
    """Salva o aggiorna in anagrafica i dati del chiamante (identificato da `telefono`).
    Passa solo i campi che hai appreso; quelli omessi restano invariati. Se emerge la
    ragione sociale, crea/aggancia automaticamente la società del cliente."""
    _log_tool("salva_contatto", telefono=telefono, nome=nome, email=email, ragione_sociale=ragione_sociale)
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
        return {"salvato": True, "contatto_id": c.id,
                "societa": societa.nome if societa else None}
    finally:
        db.close()


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
