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

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from database import (
    SessionLocal, Contatto,
    Documento, StatoDocumento, TestoCategoria,
)
from services import entita as entita_service
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


def _aid(tenant="") -> int | None:
    """azienda_id (tenant). Se l'LLM lo passa (dynamic variable {{tenant}}) lo usa; ALTRIMENTI lo
    determina il BACKEND dal registro chiamate vive (il tenant è deciso all'init dal numero
    chiamato). None → i service usano il default (deployment single-tenant)."""
    t = str(tenant or "").strip()
    if t:
        try:
            return int(t)
        except ValueError:
            pass
    return telefonia.tenant_attivo()


def _contatto(db, telefono: str, tenant="") -> Contatto:
    """Identifica (o crea) il contatto dal numero NEL TENANT, come fa il canale WhatsApp."""
    return whatsapp_agent.trova_o_crea_contatto(db, telefono or "sconosciuto", azienda_id=_aid(tenant))


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
def cerca(domanda: str, tenant: str = "") -> dict:
    """Cerca nella base di conoscenza aziendale gli ELEMENTI utili a rispondere. Decide DA SÉ se la
    fonte è un DOCUMENTO (PDF: condizioni di vendita, FAQ, descrizioni) o una TABELLA (CSV/Excel:
    prezzi, disponibilità, formati, anagrafiche). Usalo per qualsiasi domanda su prodotti, prezzi,
    condizioni, schede, FAQ, dati.
    IMPORTANTE: `risposta` NON è una risposta già pronta: sono gli ESTRATTI pertinenti (con la fonte)
    per i documenti, o le RIGHE esatte per le tabelle. Leggili e formula TU la risposta al cliente, in
    modo sintetico e naturale, usando solo queste informazioni. Ritorna anche `fonte`
    (tabella/documenti/nessuna) e `fonti`: se una fonte ha `documento_id` con `inviabile`=true e il
    cliente vuole riceverla, mandala con invia_documento usando quel documento_id."""
    _log_tool("cerca")
    db = SessionLocal()
    try:
        from services import retriever
        # sintetizza=False: niente 2ª LLM nel retriever. Ritorna i chunk grezzi; l'elaborazione la
        # fa l'agente vocale nel proprio turno (che avviene comunque) -> un round-trip LLM in meno.
        esito = retriever.cerca(db, domanda, azienda_id=_aid(tenant), sintetizza=False)
        # Per le tabelle ritorniamo le righe ESATTE del CSV (non una parafrasi): leggile come sono.
        return {"risposta": esito.get("risposta", ""), "fonte": esito.get("fonte"),
                "fonti": esito.get("fonti", []), "righe": esito.get("righe", [])[:15]}
    finally:
        db.close()


@mcp.tool()
@_loggato
def invia_documento(email: str, documento_id: int, testo: str = "", tenant: str = "") -> dict:
    """Invia al cliente, via EMAIL, UNO specifico documento come allegato. `email`=indirizzo del
    destinatario (se non lo conosci, chiedilo al cliente PRIMA, scandito e confermato); `documento_id`
    =l'id del documento da inviare (lo trovi nelle fonti di cerca_documenti); `testo`=corpo del
    messaggio (opzionale). Invia SOLO i documenti permessi: se non è inviabile te lo segnalo."""
    _log_tool("invia_documento", telefono=email)
    db = SessionLocal()
    try:
        return documenti_service.invia_documento_email(db, email, documento_id, testo,
                                                        nome_azienda=profilo.nome_azienda(db, _aid(tenant)))
    finally:
        db.close()


# ---------- [DISMESSI] vecchi tool di ricerca per CATEGORIA -------------------------------------
# Sostituiti da cerca_documenti (ricerca semantica). Tenuti commentati per sicurezza, non si sa mai.
# @mcp.tool()
# @_loggato
# def leggi_listini_prezzi() -> dict:
#     """Restituisce per intero i LISTINI e i PREZZI caricati."""
#     _log_tool("leggi_listini_prezzi")
#     return _leggi_categoria("listino")
#
# @mcp.tool()
# @_loggato
# def leggi_condizioni_vendita() -> dict:
#     """Restituisce per intero le CONDIZIONI DI VENDITA e i contratti."""
#     _log_tool("leggi_condizioni_vendita")
#     return _leggi_categoria("contratti")
#
# @mcp.tool()
# @_loggato
# def leggi_schede_prodotto() -> dict:
#     """Restituisce per intero le SCHEDE PRODOTTO/SERVIZIO."""
#     _log_tool("leggi_schede_prodotto")
#     return _leggi_categoria("schede_prodotto")
#
# @mcp.tool()
# @_loggato
# def leggi_faq() -> dict:
#     """Restituisce per intero le FAQ e il materiale informativo generale."""
#     _log_tool("leggi_faq")
#     return _leggi_categoria("faq")
#
# @mcp.tool()
# @_loggato
# def leggi_altri_documenti() -> dict:
#     """Restituisce per intero i documenti della categoria «altro»."""
#     _log_tool("leggi_altri_documenti")
#     return _leggi_categoria("altro")


def _applica_contatto(telefono: str, nome: str, cognome: str, ruolo: str, email: str,
                      titolo: str = "", note: str = "", tenant: str = "") -> dict:
    """Crea/aggiorna la PERSONA (contatto) identificata da `telefono`, scrivendo solo i campi non vuoti."""
    db = SessionLocal()
    try:
        c = _contatto(db, telefono, tenant)
        campi = {"titolo": titolo, "nome": nome, "cognome": cognome, "ruolo": ruolo}
        cambiato = False
        for k, v in campi.items():
            v = (v or "").strip()
            if v and getattr(c, k) != v:
                setattr(c, k, v)
                cambiato = True
        # Email → recapito (un contatto può averne più d'una). aggiungi() aggiorna anche la cache
        # contatti.email; se la tabella recapito non esiste ancora, fallback sulla colonna.
        em = (email or "").strip()
        if em:
            from services import recapiti
            from database import TipoRecapito
            if recapiti.aggiungi(db, c, TipoRecapito.EMAIL, em) is None and c.email != em:
                c.email = em
            cambiato = True
        # Note: testo libero che si ACCUMULA (non sovrascrive). Aggiunge solo se non già presente.
        n = (note or "").strip()
        if n and n not in (c.note or ""):
            c.note = ((c.note + "\n") if (c.note or "").strip() else "") + n
            cambiato = True
        if cambiato:
            db.commit()
        return {"ok": True, "contatto_id": c.id, "aggiornato": cambiato}
    finally:
        db.close()


@mcp.tool()
@_loggato
def salva_contatto(telefono: str, nome: str = "", cognome: str = "", ruolo: str = "",
                   email: str = "", titolo: str = "", note: str = "", tenant: str = "") -> dict:
    """Registra o aggiorna in anagrafica la PERSONA identificata da `telefono`: nome, cognome, ruolo,
    email, appellativo `titolo` («Signore»/«Signora») e `note` di contesto libero. I campi omessi
    restano invariati; `note` si accumula (non sovrascrive). Imposta `titolo` solo se il genere è
    certo. NON inventare valori: passa solo ciò che è stato detto esplicitamente. Per i dati
    dell'ENTITÀ collegata (animale, società, deceduto, ...) usa `registra_entita`, non questo tool."""
    _log_tool("salva_contatto", telefono=telefono, nome=nome, email=email, ruolo=ruolo)
    return _applica_contatto(telefono, nome, cognome, ruolo, email, titolo, note, tenant)


@mcp.tool()
@_loggato
def invia_mail(telefono: str, testo: str, oggetto: str = "", categoria_allegato: str = "", tenant: str = "") -> dict:
    """Invia un'email al chiamante. `testo` = corpo del messaggio, OBBLIGATORIO: scrivilo tu, chiaro
    e completo (è quello che leggerà il cliente). `oggetto` opzionale. `categoria_allegato` opzionale:
    se vuoi ALLEGARE dei documenti indica la loro categoria — usa SOLO le categorie elencate in
    "DOCUMENTI DISPONIBILI" nel tuo contesto; lascia vuoto se non c'è nulla da allegare (la mail va
    comunque col solo testo). Se il cliente non ha un'email salvata lo segnala: chiedila, salvala con
    salva_contatto e riprova."""
    _log_tool("invia_mail", telefono=telefono, categoria_allegato=categoria_allegato)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono, tenant)
        return documenti_service.invia_mail_contatto(
            db, c, testo, oggetto, categoria_allegato, profilo.nome_azienda(db, _aid(tenant)))
    finally:
        db.close()


@mcp.tool()
@_loggato
def apri_ticket(telefono: str, titolo: str, descrizione: str = "", priorita: str = "",
                trascrizione: str = "", canale: str = "voce", tenant: str = "") -> dict:
    """Apre (o aggiorna) un ticket di follow-up per il chiamante, per il team commerciale.
    Passa titolo, una descrizione della richiesta, la priorità (alta/media/bassa) e, se la hai,
    la trascrizione/sintesi della conversazione. `canale` lo imposta il codice (voce/whatsapp)."""
    _log_tool("apri_ticket", telefono=telefono, titolo=titolo, priorita=priorita)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono, tenant)
        # Su WhatsApp la STORIA del ticket è la conversazione REALE (dai messaggi), non il riassunto
        # del modello: più fedele per il collega che rilavora il lead.
        storia = (trascrizione or "").strip()
        if canale == "whatsapp":
            try:
                reale = whatsapp_agent._storia_testo(whatsapp_agent._storia_recente(db, c.id))
                if reale.strip():
                    storia = reale
            except Exception as e:
                logger.warning("Storia WhatsApp per ticket non recuperata: %s", e)
        esistente = whatsapp_agent._ticket_aperto(db, c.id)
        if esistente:
            esistente.titolo = (titolo or esistente.titolo).strip()[:300]
            p = ticket_service.normalizza_priorita(priorita)
            if p:
                esistente.priorita = p
            esistente.descrizione = (descrizione or "").strip() or esistente.descrizione
            if storia:
                esistente.storia = storia
            if canale:
                esistente.canale = canale
            db.commit()
            return {"aperto": True, "ticket_id": esistente.id, "aggiornato": True}
        t = ticket_service.apri_ticket(
            db, contatto_id=c.id, titolo=titolo or "Lead", priorita=priorita,
            descrizione=descrizione, storia=storia, canale=canale)
        return {"aperto": bool(t), "ticket_id": t.id if t else None}
    finally:
        db.close()


@mcp.tool()
@_loggato
def lascia_promemoria(nome_cliente: str, testo: str, societa: str = "",
                      giorni_validita: int = 0, telefono: str = "", tenant: str = "") -> dict:
    """[SOLO AMMINISTRATORE] Registra un promemoria per un CLIENTE: quando quel cliente chiamerà,
    l'assistente ne terrà conto (es. comunicargli un'offerta). `nome_cliente` = nome e/o cognome del
    destinatario; `societa` aiuta a distinguerlo. `testo` = il messaggio/avviso. `giorni_validita` =
    validità in giorni (0 = senza scadenza). NON serve passare telefono o tenant: l'identità
    dell'amministratore e il tenant li gestisce il backend. Se più clienti corrispondono, ti elenco i
    candidati per farti scegliere."""
    _log_tool("lascia_promemoria", telefono=telefono, nome_cliente=nome_cliente, societa=societa)
    db = SessionLocal()
    try:
        aid = _aid(tenant)
        # SECURITY LATO BACKEND: il flag admin è deciso all'arrivo della chiamata (caller-id
        # affidabile) e letto qui via il tenant. Fallback: verifica diretta del numero passato.
        admin_ok = telefonia.e_admin_tenant(aid) or promemoria.is_admin(telefono, db, azienda_id=aid)
        if not admin_ok:
            return {"ok": False, "errore": "Funzione riservata all'amministratore."}
        cand = promemoria.trova_target(db, nome_cliente, societa, azienda_id=_aid(tenant))
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
def registra_entita(telefono: str = "", valori: dict | None = None, entita_id: int = 0,
                    tenant: str = "") -> dict:
    """Registra (o aggiorna) l'ENTITÀ collegata al cliente — es. un animale, un deceduto, una società:
    il TIPO e i campi da raccogliere te li dico nel contesto. `valori` = dizionario {chiave: valore}
    dei campi (usa le chiavi indicate nel contesto). Passa `entita_id` SOLO se stai aggiornando una
    delle entità GIÀ NOTE elencate nel contesto (quel record specifico); OMETTILO per crearne una
    nuova. Non dedurre da solo che due con lo stesso nome siano la stessa: se hai dubbi, chiedi."""
    _log_tool("registra_entita", telefono=telefono, valori=valori, entita_id=entita_id)
    db = SessionLocal()
    try:
        c = _contatto(db, telefono, tenant)
        return entita_service.registra(db, _aid(tenant), c.id, valori or {},
                                       entita_id=(int(entita_id) or None))
    finally:
        db.close()


@mcp.tool()
@_loggato
def inoltra_chiamata(telefono: str, motivo: str, nome_destinatario: str = "", ruolo: str = "", tenant: str = "") -> dict:
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
        cand = inoltri.trova(db, nome_destinatario, ruolo, azienda_id=_aid(tenant))
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
                   chi_chiama: str = "", frase_apertura: str = "", tenant: str = "") -> dict:
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
        cand = inoltri.trova(db, nome_destinatario, ruolo, azienda_id=_aid(tenant))
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
def attendi_esito(telefono: str, tenant: str = "") -> dict:
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


def _calendario_persona(db, persona: str, tenant: str = "") -> tuple[str | None, str, str]:
    """Data una PERSONA della rubrica inoltri (per nome o ruolo), ritorna (calendar_id, regole_
    prenotazione, nome). (None, '', '') se non trovata: in tal caso si usa il calendario dell'azienda."""
    p = (persona or "").strip()
    if not p:
        return None, "", ""
    cand = inoltri.trova(db, p, "", azienda_id=_aid(tenant)) or inoltri.trova(db, "", p, azienda_id=_aid(tenant))
    if not cand:
        return None, "", ""
    it = cand[0]
    return (it.calendar_id or None), (it.regole_prenotazione or ""), it.nome_completo


@mcp.tool()
@_loggato
def controlla_disponibilita(giorno: str = "", durata_minuti: int = 30, dalle: int = 9,
                            alle: int = 18, persona: str = "", tenant: str = "") -> dict:
    """SLOT LIBERI (e occupati) su Google Calendar. Di DEFAULT (giorno vuoto) ritorna i PROSSIMI 7
    GIORNI (oggi incluso), un blocco per giorno con `slot_liberi` e `occupati`. Se ti serve un SOLO
    giorno, passa `giorno`='YYYY-MM-DD'. `durata_minuti` (default 30); `dalle`/`alle` = finestra oraria.
    `persona`: se il meeting è per una persona della rubrica (chi gestisce l'inoltro), passa il suo
    nome/ruolo → si usa il SUO calendario e, se presenti, le sue `regole_prenotazione`.
    Proponi al cliente SOLO orari in `slot_liberi` CHE rispettano anche le `regole_prenotazione`
    eventualmente restituite; poi prenota con prenota_meeting (stessa `persona`)."""
    _log_tool("controlla_disponibilita", titolo=giorno or "settimana", nome_cliente=persona)
    from services import google_calendar as gc
    db = SessionLocal()
    try:
        cal, regole, nome = _calendario_persona(db, persona, tenant)
        if (giorno or "").strip():
            res = gc.disponibilita(db, giorno, int(durata_minuti or 30),
                                   ora_inizio=int(dalle or 9), ora_fine=int(alle or 18), calendar_id=cal)
        else:
            res = gc.disponibilita_settimana(db, giorni=7, durata_min=int(durata_minuti or 30),
                                             ora_inizio=int(dalle or 9), ora_fine=int(alle or 18),
                                             calendar_id=cal)
        if isinstance(res, dict):
            if persona:
                res["persona"] = nome or persona
            if (regole or "").strip():
                res["regole_prenotazione"] = regole.strip()
        return res
    finally:
        db.close()


@mcp.tool()
@_loggato
def prenota_meeting(titolo: str, data_ora: str, durata_minuti: int = 30, invitati: str = "",
                    descrizione: str = "", online: bool = True, persona: str = "",
                    tenant: str = "") -> dict:
    """Prenota un meeting su Google Calendar e INVIA l'invito ai destinatari.
    `titolo` = oggetto; `data_ora` = inizio ISO locale (es. "2026-07-01T16:00:00"); `durata_minuti`
    (default 30); `invitati` = email separate da virgola; `online`=True crea una Google Meet.
    `persona`: se il meeting è per una persona della rubrica, passa il suo nome/ruolo → si prenota sul
    SUO calendario (lo stesso usato in controlla_disponibilita). Prima di prenotare, conferma col
    cliente data/ora e la sua email, e verifica che lo slot sia libero e rispetti le regole."""
    _log_tool("prenota_meeting", titolo=titolo, nome_cliente=persona)
    from datetime import datetime, timedelta
    from services import google_calendar as gc
    try:
        inizio = datetime.fromisoformat(data_ora.strip())
    except ValueError:
        return {"ok": False, "errore": "data_ora non valida: usa il formato ISO, es. 2026-07-01T16:00:00."}
    fine = inizio + timedelta(minutes=int(durata_minuti or 30))
    emails = [e.strip() for e in (invitati or "").replace(";", ",").split(",") if e.strip()]
    db = SessionLocal()
    try:
        cal, _regole, _nome = _calendario_persona(db, persona, tenant)
        return gc.crea_evento(db, titolo, inizio.isoformat(), fine.isoformat(), emails,
                              descrizione, bool(online), calendar_id=cal)
    finally:
        db.close()


# Override LIVE delle descrizioni (editabili da dashboard) su ciò che ElevenLabs riceve via MCP:
# wrappiamo il list_tools del tool manager per sostituire la description quando c'è un override.
# Guardato: se l'API interna cambiasse, si tiene il comportamento originale (descrizioni docstring).
def _inventario_tools():
    """Lista dei tool esposti dall'MCP: [{nome, descrizione, parametri}]. Usata da list_tools e dall'API."""
    return list(mcp._tool_manager.list_tools())


try:
    from services import tool_meta as _tm
    _orig_list_tools = mcp._tool_manager.list_tools

    def _list_tools_con_override():
        tools = _orig_list_tools()
        try:
            ov = _tm.overrides()
            for t in tools:
                nome = getattr(t, "name", None)
                if nome in ov:
                    t.description = ov[nome]
        except Exception:
            pass
        return tools

    mcp._tool_manager.list_tools = _list_tools_con_override
except Exception:
    pass


# Inizializza l'app Streamable HTTP (crea il session_manager, usato nel lifespan dell'app).
http_app = mcp.streamable_http_app()
