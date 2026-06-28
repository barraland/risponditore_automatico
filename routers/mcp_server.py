"""Server MCP per agenti vocali esterni (es. ElevenLabs Conversational AI).

Espone gli stessi strumenti dell'assistente Realtime, ma in versione STATELESS:
non c'è la sessione WebSocket in memoria, quindi il chiamante è identificato dal
suo numero di telefono (`telefono`), che l'agente esterno passa come parametro
(da ElevenLabs: la dynamic variable `system__caller_id`).

Riusa i service esistenti (crm, retriever, documenti, ticket, email) e la stessa
logica di find-or-create del contatto del canale WhatsApp.

Montato su /mcp dall'app principale; transport Streamable HTTP (stateless).
"""

import functools
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
from services import promemoria
from services import inoltri
from services import telefonia
from services import inoltro_assistito
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


def _riassumi_esito(res) -> str:
    """Riassunto conciso del risultato di un tool per il log (taglia i campi lunghi)."""
    if not isinstance(res, dict):
        return str(res)[:200]
    if res.get("errore"):
        return f"ERRORE: {res['errore']}"
    if res.get("email_mancante"):
        return "email mancante (da chiedere)"
    coppie = {k: v for k, v in res.items()
              if k not in ("ordini", "contenuto", "righe") and v not in (None, "", [])}
    s = ", ".join(f"{k}={v}" for k, v in coppie.items())
    return (s[:300] + "…") if len(s) > 300 else (s or "ok")


def _loggato(fn):
    """Logga l'esito (✅/⚠️/❌) di un tool MCP. Va messo SOTTO @mcp.tool() per non rompere lo schema."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            res = fn(*args, **kwargs)
            problema = isinstance(res, dict) and (
                res.get("errore") or res.get("ok") is False
                or res.get("email_mancante") or res.get("trovato") is False)
            logger.info("   %s %s → %s", "⚠️" if problema else "✅", fn.__name__, _riassumi_esito(res))
            return res
        except Exception as e:
            logger.exception("   ❌ %s → eccezione: %s", fn.__name__, e)
            raise
    return wrapper


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
@_loggato
def leggi_listini_prezzi() -> dict:
    """Restituisce per intero i LISTINI e i PREZZI caricati. Usalo quando il cliente chiede
    quanto costa un prodotto, sconti di listino, formati/confezioni e relativi prezzi."""
    _log_tool("leggi_listini_prezzi")
    return _leggi_categoria("listino")


@mcp.tool()
@_loggato
def leggi_condizioni_vendita() -> dict:
    """Restituisce per intero le CONDIZIONI DI VENDITA e i contratti: tempi e modalità di
    consegna, ordine minimo, modalità di pagamento, termini contrattuali."""
    _log_tool("leggi_condizioni_vendita")
    return _leggi_categoria("contratti")


@mcp.tool()
@_loggato
def leggi_schede_prodotto() -> dict:
    """Restituisce per intero le SCHEDE PRODOTTO/SERVIZIO: caratteristiche, formati, dettagli
    tecnici, ingredienti/specifiche dei prodotti."""
    _log_tool("leggi_schede_prodotto")
    return _leggi_categoria("schede_prodotto")


@mcp.tool()
@_loggato
def leggi_faq() -> dict:
    """Restituisce per intero le FAQ e il materiale informativo generale (domande frequenti,
    informazioni sull'azienda e sul servizio)."""
    _log_tool("leggi_faq")
    return _leggi_categoria("faq")


@mcp.tool()
@_loggato
def leggi_altri_documenti() -> dict:
    """Restituisce per intero i documenti della categoria «altro» (non classificati nelle
    categorie precedenti)."""
    _log_tool("leggi_altri_documenti")
    return _leggi_categoria("altro")


def _applica_contatto(telefono: str, nome: str, cognome: str, ragione_sociale: str,
                      ruolo: str, email: str, sede: str, stato: str, titolo: str = "") -> dict:
    """Crea/aggiorna il contatto identificato da `telefono`, scrivendo solo i campi non vuoti.
    Logica condivisa da salva_contatto (prima registrazione) e aggiorna_contatto (info nuove)."""
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        campi = {"titolo": titolo, "nome": nome, "cognome": cognome, "ragione_sociale": ragione_sociale,
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
@_loggato
def salva_contatto(telefono: str, nome: str = "", cognome: str = "", ragione_sociale: str = "",
                   ruolo: str = "", email: str = "", sede: str = "", stato: str = "", titolo: str = "") -> dict:
    """Registra in anagrafica il chiamante (identificato da `telefono`): usalo SUBITO alla prima
    registrazione di un prospect non ancora in rubrica, appena hai almeno il nome.
    Passa SOLO i campi che il cliente ha detto esplicitamente; quelli omessi restano invariati.
    NON inventare né dedurre valori: se un dato (es. città/sede, email, ruolo) non è stato
    dichiarato, ometti del tutto il campo. `titolo` = "Signore" o "Signora": impostalo SOLO se sei
    certo del genere (dal modo in cui si presenta), altrimenti OMETTILO — meglio nessun titolo che
    sbagliarlo. Tutti i campi tranne `telefono` sono opzionali. Se emerge la ragione sociale,
    crea/aggancia automaticamente la società del cliente."""
    _log_tool("salva_contatto", telefono=telefono, nome=nome, email=email, ragione_sociale=ragione_sociale)
    return _applica_contatto(telefono, nome, cognome, ragione_sociale, ruolo, email, sede, stato, titolo)


@mcp.tool()
@_loggato
def aggiorna_contatto(telefono: str, nome: str = "", cognome: str = "", ragione_sociale: str = "",
                      ruolo: str = "", email: str = "", sede: str = "", stato: str = "", titolo: str = "") -> dict:
    """Aggiorna i dati ANAGRAFICI DELLA PERSONA (contatto, identificato da `telefono`) quando emergono
    informazioni nuove: email, ruolo, cognome, oppure `titolo` ("Signore"/"Signora") se diventa chiaro
    il genere. Per i dati del LOCALE/azienda (città, indirizzo, P.IVA) usa invece aggiorna_locale.
    Passa SOLO i campi nuovi appena appresi; gli altri restano invariati. NON inventare valori."""
    _log_tool("aggiorna_contatto", telefono=telefono, email=email, ruolo=ruolo, titolo=titolo)
    return _applica_contatto(telefono, nome, cognome, ragione_sociale, ruolo, email, sede, stato, titolo)


@mcp.tool()
@_loggato
def aggiorna_locale(telefono: str, citta: str = "", indirizzo: str = "",
                    ragione_sociale: str = "", piva: str = "", insegna: str = "") -> dict:
    """Aggiorna l'anagrafica del LOCALE/azienda del chiamante (il ristorante/bar/hotel a cui è
    associato il contatto identificato da `telefono`): città, indirizzo, ragione sociale, P.IVA,
    insegna. Usalo quando in conversazione emerge un dato del locale prima mancante (es. la città).
    Passa SOLO i campi nuovi; gli altri restano invariati. NON inventare valori."""
    _log_tool("aggiorna_locale", telefono=telefono, citta=citta, indirizzo=indirizzo)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        societa = crm.societa_di_contatto(db, c)
        if not societa:
            insegna_nuova = (insegna or ragione_sociale or c.ragione_sociale or "").strip()
            if not insegna_nuova:
                return {"ok": False, "errore": "Nessun locale associato e nessun nome per crearlo."}
            societa = crm.trova_o_crea_societa(db, insegna=insegna_nuova)
            if not c.societa_id:
                c.societa_id = societa.id
        campi = {"citta": citta, "indirizzo": indirizzo, "ragione_sociale": ragione_sociale,
                 "piva": piva, "insegna": insegna}
        cambiato = False
        for k, v in campi.items():
            v = (v or "").strip()
            if v and getattr(societa, k) != v:
                setattr(societa, k, v)
                cambiato = True
        if cambiato or not c.societa_id:
            db.commit()
        return {"ok": True, "locale_id": societa.id, "locale": societa.insegna, "aggiornato": cambiato}
    finally:
        db.close()


@mcp.tool()
@_loggato
def registra_ordine(telefono: str, righe: list[RigaOrdineInput], note: str = "",
                    conferma: bool = False) -> dict:
    """Registra un ordine del chiamante. `conferma`=true lo salva come CONFERMATO, altrimenti
    come bozza (segui le indicazioni dell'amministratore su quando confermare). Se per la stessa
    trattativa esiste già una bozza, la aggiorna invece di duplicarla.
    `note`: testo libero su questo ordine (es. orario di consegna preferito, richieste particolari,
    note su sconti applicati). Per modificare SOLO le note di un ordine già creato usa aggiorna_ordine."""
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
@_loggato
def aggiorna_ordine(telefono: str, note: str, ordine_id: int = 0) -> dict:
    """Aggiorna le NOTE libere di un ordine GIÀ registrato del chiamante (es. orario di consegna
    preferito, richieste particolari, note sugli sconti applicati). Se `ordine_id` è 0/omesso,
    aggiorna l'ULTIMO ordine del cliente (quello appena creato). Le note fornite SOSTITUISCONO le
    precedenti: per aggiungere, includi anche il testo già presente. Non tocca le righe."""
    _log_tool("aggiorna_ordine", telefono=telefono, ordine_id=ordine_id)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        ordine = None
        if ordine_id:
            o = db.get(Ordine, int(ordine_id))
            if o and (o.contatto_id == c.id or (c.societa_id and o.societa_id == c.societa_id)):
                ordine = o
        if ordine is None:
            ordine = (db.query(Ordine).filter(Ordine.contatto_id == c.id)
                      .order_by(Ordine.data.desc()).first())
        if ordine is None:
            return {"ok": False, "errore": "Nessun ordine da aggiornare per questo cliente."}
        ordine.note = (note or "").strip() or None
        db.commit()
        return {"ok": True, "ordine_id": ordine.id, "note": ordine.note}
    finally:
        db.close()


@mcp.tool()
@_loggato
def storico_ordini(telefono: str, giorni: int = 0, limite: int = 10) -> dict:
    """Restituisce gli ordini RECENTI del cliente (la sua società), con prodotti e quantità di
    ciascuno. Usalo per: (a) capire cosa ordina di solito e DISAMBIGUARE un prodotto generico
    (es. "la Peroni" → quale formato ha già ordinato; se ne ha ordinati più formati, chiedigli
    quale); (b) RIORDINARE un ordine passato ("riordina l'ultimo con le birre" → cerchi l'ordine
    giusto qui e poi lo registri con registra_ordine). `giorni`: finestra temporale (7 = ultima
    settimana, 30 = ultimo mese; 0 = tutti). `limite`: max ordini da restituire (default 10)."""
    _log_tool("storico_ordini", telefono=telefono, giorni=giorni)
    from datetime import datetime, timedelta
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        societa = crm.societa_di_contatto(db, c)
        q = db.query(Ordine)
        q = q.filter(Ordine.societa_id == societa.id) if societa else q.filter(Ordine.contatto_id == c.id)
        if giorni and giorni > 0:
            q = q.filter(Ordine.data >= datetime.utcnow() - timedelta(days=giorni))
        ordini = q.order_by(Ordine.data.desc()).limit(max(1, min(int(limite or 10), 30))).all()
        out = [{
            "ordine_id": o.id,
            "data": o.data.strftime("%d/%m/%Y") if o.data else "",
            "stato": o.stato.value,
            "totale": o.totale,
            "righe": [{"descrizione": r.descrizione, "quantita": r.quantita,
                       "unita": r.unita, "prezzo_unitario": r.prezzo_unitario} for r in o.righe],
        } for o in ordini]
        return {"n": len(out), "ordini": out}
    finally:
        db.close()


@mcp.tool()
@_loggato
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
@_loggato
def invia_mail(telefono: str, testo: str, oggetto: str = "", categoria_allegato: str = "") -> dict:
    """Invia un'email al chiamante. `testo` = corpo del messaggio, OBBLIGATORIO: scrivilo tu, chiaro
    e completo (è quello che leggerà il cliente). `oggetto` opzionale. `categoria_allegato` opzionale:
    se vuoi ALLEGARE dei documenti indica la loro categoria — usa SOLO le categorie elencate in
    "DOCUMENTI DISPONIBILI" nel tuo contesto; lascia vuoto se non c'è nulla da allegare (la mail va
    comunque col solo testo). Se il cliente non ha un'email salvata lo segnala: chiedila, salvala con
    aggiorna_contatto e riprova."""
    _log_tool("invia_mail", telefono=telefono, categoria_allegato=categoria_allegato)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono)
        return documenti_service.invia_mail_contatto(
            db, c, testo, oggetto, categoria_allegato, profilo.nome_azienda(db))
    finally:
        db.close()


@mcp.tool()
@_loggato
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


@mcp.tool()
@_loggato
def lascia_promemoria(telefono: str, nome_cliente: str, testo: str, societa: str = "",
                      giorni_validita: int = 0) -> dict:
    """[SOLO AMMINISTRATORE] Registra un promemoria per un CLIENTE: quando quel cliente chiamerà,
    l'assistente ne terrà conto (es. comunicargli un'offerta). `telefono` = il TUO numero
    (amministratore). `nome_cliente` = nome e/o cognome del destinatario; `societa` aiuta a
    distinguerlo. `testo` = il messaggio/avviso. `giorni_validita` = validità in giorni (0 = senza
    scadenza). Se più clienti corrispondono, ti elenco i candidati per farti scegliere."""
    _log_tool("lascia_promemoria", telefono=telefono, nome_cliente=nome_cliente, societa=societa)
    if not promemoria.is_admin(telefono):
        return {"ok": False, "errore": "Funzione riservata all'amministratore."}
    db = SessionLocal()
    try:
        cand = promemoria.trova_target(db, nome_cliente, societa)
        if not cand:
            return {"ok": False, "errore": f"Nessun cliente trovato per «{nome_cliente}»."}
        if len(cand) > 1:
            return {"ok": False, "ambiguo": True,
                    "candidati": [{"contatto_id": c.id, "nome": c.nome_completo,
                                   "societa": (c.societa.nome if c.societa else (c.ragione_sociale or ""))}
                                  for c in cand],
                    "messaggio": "Più clienti corrispondono: chiedi all'amministratore quale (nome/società) e riprova."}
        c = cand[0]
        p = promemoria.crea(db, c.id, testo, giorni_validita)
        if not p:
            return {"ok": False, "errore": "Testo del promemoria mancante."}
        return {"ok": True, "promemoria_id": p.id, "cliente": c.nome_completo,
                "scade_il": p.scade_il.strftime("%d/%m/%Y") if p.scade_il else None}
    finally:
        db.close()


@mcp.tool()
@_loggato
def inoltra_chiamata(telefono: str, motivo: str, nome_destinatario: str = "", ruolo: str = "") -> dict:
    """INOLTRA la chiamata a una persona della rubrica inoltri (es. responsabile spedizioni).
    `telefono` = numero del chiamante; `motivo` = cosa vuole il cliente; indica il destinatario per
    `nome_destinatario` e/o `ruolo`. Inoltra SOLO se la richiesta rientra nelle regole di inoltro che
    vedi nel contesto. Lo strumento esegue direttamente il trasferimento: chiama il destinatario, gli
    annuncia chi e perché e chiede conferma a voce; se accetta unisce le chiamate, altrimenti il
    cliente viene avvisato. Se più persone corrispondono, te le elenco per scegliere: chiedi al
    cliente quale prima di riprovare. Prima di chiamare questo strumento, di' al cliente di restare
    in linea."""
    _log_tool("inoltra_chiamata", telefono=telefono, nome_destinatario=nome_destinatario, ruolo=ruolo)
    db = SessionLocal()
    try:
        cand = inoltri.trova(db, nome_destinatario, ruolo)
        if not cand:
            return {"ok": False, "errore": "Nessun destinatario di inoltro trovato per questa richiesta."}
        if len(cand) > 1:
            return {"ok": False, "ambiguo": True,
                    "candidati": [{"nome": i.nome_completo, "ruolo": i.ruolo, "telefono": i.telefono} for i in cand],
                    "messaggio": "Più destinatari possibili: scegli quale (per nome o ruolo) e riprova."}
        i = cand[0]
        c = whatsapp_agent.trova_contatto(db, telefono) if telefono else None
        chiamante = c.nome_completo if c else "il chiamante"
        riepilogo = (f"Le passo {chiamante}. Motivo: {(motivo or '').strip() or 'richiesta del cliente'}.")

        # Avvia il trasferimento reale sulla chiamata Twilio in corso (vale per ElevenLabs e Realtime).
        ch = telefonia.dati_chiamata(telefono)
        ok, errore = telefonia.avvia_inoltro(ch.get("call_sid"), i.telefono, riepilogo,
                                             ch.get("host"), ch.get("numero_twilio", ""))
        if ok:
            return {"ok": True, "inoltro_avviato": True, "destinatario": i.nome_completo,
                    "messaggio": ("Sto passando la chiamata adesso: di' al cliente di restare in linea che lo "
                                  "metti in contatto, poi non aggiungere altro.")}
        return {"ok": False, "errore": errore,
                "messaggio": "Non riesco a passare la chiamata ora: di' al cliente che lo farete ricontattare."}
    finally:
        db.close()


# ---------- Inoltro ASSISTITO (un secondo agente chiama il destinatario) ----------

def _qualifica_chiamante(c) -> str:
    """Descrizione di chi è in linea da annunciare al destinatario: nome + società + città."""
    if not c:
        return "un cliente"
    parti = [c.nome_completo]
    soc = getattr(c, "societa", None)
    nome_soc = ((getattr(soc, "insegna", None) or getattr(soc, "ragione_sociale", None)) if soc
                else getattr(c, "ragione_sociale", None))
    if nome_soc:
        parti.append(f"di {nome_soc}")
    citta = getattr(soc, "citta", None) if soc else None
    if citta:
        parti.append(f"({citta})")
    return " ".join(p for p in parti if p)


@mcp.tool()
@_loggato
def chiama_persona(telefono: str, motivo: str, nome_destinatario: str = "", ruolo: str = "",
                   chi_chiama: str = "", frase_apertura: str = "") -> dict:
    """[inoltro assistito] Avvia una chiamata in USCITA: un nostro assistente chiama la persona della
    rubrica inoltri (es. responsabile spedizioni) e le chiede se può ricevere ORA la chiamata.
    `telefono`=numero del chiamante; `motivo`=il problema/richiesta del cliente, descritto bene;
    `chi_chiama`=chi è in linea, qualificato (nome e cognome, società/locale e città se li sai);
    `frase_apertura`=la FRASE PARLATA, naturale e già pronta, che il nostro assistente dirà per
    prima al destinatario: deve qualificare chi è in linea (nome + società + città) e il motivo in
    modo discorsivo, e finire offrendo di passarlo o no. Es: "Ciao, ho in linea Andrea Barral del
    chiosco di Piazza Piemonte a Milano: ha un cliente che chiede una dilazione di pagamento e
    vorrebbe parlartene. Te lo passo, o gli dico che ora sei occupato?". Destinatario per
    `nome_destinatario` e/o `ruolo`. Usa SOLO se la richiesta rientra nelle regole di inoltro.
    DOPO: di' al cliente di restare in linea, poi usa `attendi_esito`. Se più persone
    corrispondono, te le elenco: chiedi quale."""
    _log_tool("chiama_persona", telefono=telefono, nome_destinatario=nome_destinatario, ruolo=ruolo)
    db = SessionLocal()
    try:
        cand = inoltri.trova(db, nome_destinatario, ruolo)
        if not cand:
            return {"ok": False, "errore": "Nessun destinatario di inoltro trovato per questa richiesta."}
        if len(cand) > 1:
            return {"ok": False, "ambiguo": True,
                    "candidati": [{"nome": x.nome_completo, "ruolo": x.ruolo, "telefono": x.telefono} for x in cand],
                    "messaggio": "Più destinatari possibili: chiedi al cliente quale e riprova."}
        i = cand[0]
        c = whatsapp_agent.trova_contatto(db, telefono) if telefono else None
        # Cliente NOTO: qualifica autorevole dai dati Supabase (nome + società + città).
        # Sconosciuto/prospect: usa ciò che Margherita ha raccolto in chi_chiama.
        chiamante = _qualifica_chiamante(c) if c else ((chi_chiama or "").strip() or "un cliente")
        ch = telefonia.dati_chiamata(telefono)
        ok, errore = inoltro_assistito.avvia(telefono, ch.get("call_sid"), ch.get("host"),
                                             i, chiamante, motivo, frase_apertura)
        if ok:
            return {"ok": True, "chiamata_avviata": True, "destinatario": i.nome_completo,
                    "messaggio": ("Sto chiamando %s. Di' al cliente di restare in linea un momento, "
                                  "poi usa attendi_esito." % i.nome_completo)}
        return {"ok": False, "errore": errore,
                "messaggio": "Non riesco a contattarlo ora: di' al cliente che lo farete ricontattare."}
    finally:
        db.close()


@mcp.tool()
@_loggato
def attendi_esito(telefono: str) -> dict:
    """[inoltro assistito] Dimmi com'è andata la chiamata al destinatario. `telefono`=numero del
    chiamante. Stati: `in_corso` (sto ancora provando: rassicura il cliente «ancora un istante» e
    richiamami tra poco), `accettato` (ha detto sì: sto unendo le chiamate, salutalo brevemente),
    `rifiutato`/`non_risponde` (riferisci al cliente con gentilezza e prosegui tu ad aiutarlo),
    `nessuno` (nessuna chiamata in corso)."""
    _log_tool("attendi_esito", telefono=telefono)
    return inoltro_assistito.attendi_esito(telefono)


@mcp.tool()
@_loggato
def unisci_chiamate(sessione: str = "", telefono: str = "") -> dict:
    """[AGENTE OUTBOUND] Il destinatario ha ACCETTATO di ricevere la chiamata: unisci le due
    chiamate. Passa `sessione` (il valore che hai ricevuto nel contesto). Dopo, saluta e chiudi."""
    _log_tool("unisci_chiamate", telefono=sessione or telefono)
    ok, errore = inoltro_assistito.accetta(sessione or telefono)
    if ok:
        return {"ok": True, "messaggio": "Chiamate unite. Saluta e termina."}
    return {"ok": False, "errore": errore}


@mcp.tool()
@_loggato
def rifiuta_inoltro(sessione: str = "", telefono: str = "", motivo: str = "") -> dict:
    """[AGENTE OUTBOUND] Il destinatario NON può ricevere la chiamata ora (ha rifiutato, oppure hai
    raggiunto una segreteria). Passa `sessione` dal contesto e un breve `motivo`. Se è una
    segreteria, puoi lasciare un messaggio prima di chiudere."""
    _log_tool("rifiuta_inoltro", telefono=sessione or telefono)
    ok, errore = inoltro_assistito.rifiuta(sessione or telefono, motivo)
    if ok:
        return {"ok": True, "messaggio": "Registrato. Saluta e termina la chiamata."}
    return {"ok": False, "errore": errore}


# Inizializza l'app Streamable HTTP (crea il session_manager, usato nel lifespan dell'app).
http_app = mcp.streamable_http_app()
