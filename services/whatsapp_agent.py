"""Agente WhatsApp per la lead capture.

Per ogni messaggio in arrivo:
1. Identifica il contatto dal numero di telefono; se è un numero nuovo, crea un
   nuovo lead (prospect) — il numero sconosciuto NON viene più rifiutato.
2. Salva la storia della conversazione (ultimi N messaggi, N configurabile).
3. Una sola chiamata LLM, con il profilo aziendale (cosa offriamo, come qualificare,
   come assegnare la priorità) nel system prompt, produce in output strutturato:
   - la risposta da inviare al lead;
   - i campi anagrafici raccolti da salvare sul contatto;
   - se/quando aprire (o aggiornare) il ticket di follow-up con titolo e priorità.
4. Applica gli aggiornamenti (anagrafica + ticket) e restituisce la risposta.

È sincrono e indipendente dal trasporto: il webhook lo chiama e invia la stringa
restituita via WhatsApp.
"""

import json
import logging
import os
import re

from openai import OpenAI
from sqlalchemy.orm import Session

from datetime import datetime

from database import (
    Contatto, ContattoStato, MessaggioChat, DirezioneMessaggio, Ticket, StatoTicket,
    ChiamataVoce,
)
from database import CanaleOrdine, OrigineOrdine, StatoOrdine, Ordine
from services import ticket as ticket_service
from services import istruzioni
from services import profilo
from services import prompt_moduli
from services import promemoria
from services import agente_tools
from services import tool_meta
from services import retriever
from services import crm
from services import email as email_service
from services import documenti as documenti_service
from services import entita as entita_service
from services.contesto import contesto_temporale

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("WHATSAPP_AGENT_MODEL", "gpt-5-mini")
EFFORT = os.getenv("WHATSAPP_AGENT_EFFORT", "low")
STORIA_MAX = int(os.getenv("WHATSAPP_STORIA_MAX", "10"))

MSG_SERVIZIO_NON_DISPONIBILE = "Servizio momentaneamente non disponibile. Riprovi più tardi."


# ---------- Identificazione / creazione contatto ----------

def _chiave_tel(tel: str) -> str:
    """Normalizza un numero al 'core' nazionale (ultime 10 cifre) per il confronto."""
    d = re.sub(r"\D", "", tel or "")
    if d.startswith("00"):
        d = d[2:]
    return d[-10:] if len(d) >= 10 else d


def _tenant_id(db: Session, azienda_id: int | None) -> int | None:
    if azienda_id:
        return azienda_id
    from services import tenant as tenant_service
    az = tenant_service.default(db)
    return az.id if az else None


def trova_contatto(db: Session, telefono: str, azienda_id: int | None = None) -> Contatto | None:
    """Trova il contatto (NEL TENANT) il cui numero corrisponde (match sulle ultime cifre).
    Lo scoping per tenant è essenziale: lo stesso numero può esistere per aziende diverse."""
    chiave = _chiave_tel(telefono)
    if not chiave:
        return None
    aid = _tenant_id(db, azienda_id)
    for c in db.query(Contatto).filter(Contatto.telefono.isnot(None),
                                       Contatto.azienda_id == aid).all():
        if _chiave_tel(c.telefono) == chiave:
            return c
    return None


def trova_o_crea_contatto(db: Session, telefono: str, azienda_id: int | None = None) -> Contatto:
    """Ritorna il contatto del numero nel tenant, creandone uno nuovo (prospect) se sconosciuto."""
    aid = _tenant_id(db, azienda_id)
    c = trova_contatto(db, telefono, azienda_id=aid)
    if c:
        return c
    c = Contatto(telefono=telefono, stato=ContattoStato.PROSPECT, azienda_id=aid)
    db.add(c)
    db.commit()
    db.refresh(c)
    logger.info("Nuovo lead creato dal numero %s (id %s, tenant %s)", telefono, c.id, aid)
    return c


# ---------- Storia conversazione ----------

def _log(db: Session, contatto_id: int, direzione: DirezioneMessaggio, testo: str, traccia=None):
    db.add(MessaggioChat(
        contatto_id=contatto_id, direzione=direzione, testo=testo,
        traccia=json.dumps(traccia, ensure_ascii=False) if traccia else None,
    ))
    db.commit()


def _storia_recente(db: Session, contatto_id: int) -> list[MessaggioChat]:
    """Ultimi STORIA_MAX messaggi (in ordine cronologico)."""
    msgs = (
        db.query(MessaggioChat)
        .filter(MessaggioChat.contatto_id == contatto_id)
        .order_by(MessaggioChat.timestamp.desc())
        .limit(STORIA_MAX)
        .all()
    )
    return list(reversed(msgs))


def _storia_testo(storia: list[MessaggioChat]) -> str:
    return "\n".join(
        ("Contatto" if m.direzione == DirezioneMessaggio.IN else "Assistente") + f": {m.testo}"
        for m in storia
    )


def _tempo_da_ultimo(db: Session, contatto_id: int) -> str:
    """Tempo trascorso dal MESSAGGIO PRECEDENTE del thread (quello prima di quello appena ricevuto),
    in forma leggibile. «primo messaggio in assoluto» se non ce n'è uno prima. È un DATO grezzo per il
    prompt: la regola sul saluto la scrive l'amministratore in dashboard, non è cablata nel codice."""
    from datetime import datetime
    ultimi = (db.query(MessaggioChat)
              .filter(MessaggioChat.contatto_id == contatto_id)
              .order_by(MessaggioChat.timestamp.desc()).limit(2).all())
    if len(ultimi) < 2 or not ultimi[1].timestamp:
        return "primo messaggio in assoluto"
    sec = max(0, int((datetime.utcnow() - ultimi[1].timestamp).total_seconds()))
    if sec < 60:
        return "meno di un minuto"
    if sec < 3600:
        m = sec // 60
        return f"{m} minuto" if m == 1 else f"{m} minuti"
    if sec < 86400:
        h = sec // 3600
        return f"{h} ora" if h == 1 else f"{h} ore"
    g = sec // 86400
    return f"{g} giorno" if g == 1 else f"{g} giorni"


def _ticket_aperto(db: Session, contatto_id: int) -> Ticket | None:
    """Eventuale ticket già aperto per il contatto (per non crearne duplicati)."""
    return (
        db.query(Ticket)
        .filter(Ticket.contatto_id == contatto_id, Ticket.stato == StatoTicket.APERTO)
        .order_by(Ticket.created_at.desc())
        .first()
    )


def _registra_log_conversazione(db: Session, contatto: Contatto, storia_testo: str) -> None:
    """Log conversazione WhatsApp nel registro interazioni (chiamate_voce, canale='whatsapp').
    Upsert: UNA riga per contatto/giorno, con trascrizione aggiornata e link al ticket aperto.
    Non solleva: è un log secondario, non deve rompere la risposta al cliente."""
    try:
        tk = _ticket_aperto(db, contatto.id)
        riassunto = (tk.descrizione or tk.titolo).strip() if tk and (tk.descrizione or tk.titolo) else None
        ora = datetime.utcnow()
        esistente = (db.query(ChiamataVoce)
                     .filter(ChiamataVoce.contatto_id == contatto.id, ChiamataVoce.canale == "whatsapp")
                     .order_by(ChiamataVoce.iniziata_at.desc()).first())
        if esistente and esistente.iniziata_at and esistente.iniziata_at.date() == ora.date():
            esistente.trascrizione = storia_testo or esistente.trascrizione
            if riassunto:
                esistente.riassunto = riassunto
            if tk:
                esistente.ticket_id = tk.id
            esistente.telefono = contatto.telefono or esistente.telefono
        else:
            db.add(ChiamataVoce(
                azienda_id=contatto.azienda_id, contatto_id=contatto.id, telefono=contatto.telefono,
                canale="whatsapp", iniziata_at=ora, trascrizione=storia_testo or None,
                riassunto=riassunto, ticket_id=(tk.id if tk else None),
            ))
        db.commit()
    except Exception as e:
        logger.error("Log conversazione WhatsApp fallito (contatto %s): %s", contatto.id, e)
        db.rollback()


# ---------- LLM lead-capture ----------

SYSTEM = """Sei l'assistente WhatsApp di un'azienda: rispondi a clienti e potenziali clienti
(prodotti, prezzi, ordini, tempistiche) e intanto qualifichi il lead per il team commerciale.

- Scrivi in italiano, tono cordiale e professionale, messaggi BREVI adatti a una chat: vai al punto,
  niente muri di testo, elenchi puntati corti quando elenchi prodotti o prezzi. Puoi mandare link.
- Hai a disposizione degli STRUMENTI (cercare nei documenti, salvare/aggiornare il contatto e il
  locale, registrare ordini, aprire ticket, inviare email/documenti, controllare il calendario e
  prenotare meeting): USALI quando servono. Agisci e poi rispondi; non annunciare che stai per usare
  uno strumento.
- Non inventare mai dati: registra SOLO ciò che il cliente ha scritto. Se un'informazione non è nei
  documenti, dillo con onestà e rassicura che un collega ricontatterà il lead.
- Le regole di dettaglio (come qualificare il lead, gestire ordini, note, meeting, ticket) sono nelle
  sezioni seguenti."""

SCHEMA = {
    "type": "object",
    "properties": {
        "risposta": {"type": "string"},
        "consulta_documenti": {
            "type": "object",
            "properties": {
                "serve": {"type": "boolean"},
                "domanda": {"type": "string"},
            },
            "required": ["serve", "domanda"],
            "additionalProperties": False,
        },
        "anagrafica": {
            "type": "object",
            "properties": {
                "nome": {"type": "string"},
                "cognome": {"type": "string"},
                "ragione_sociale": {"type": "string"},
                "ruolo": {"type": "string"},
                "email": {"type": "string"},
                "telefono": {"type": "string"},
                "sede": {"type": "string"},
                "stato": {"type": "string", "enum": ["cliente", "prospect", ""]},
            },
            "required": ["nome", "cognome", "ragione_sociale", "ruolo", "email",
                         "telefono", "sede", "stato"],
            "additionalProperties": False,
        },
        "ticket": {
            "type": "object",
            "properties": {
                "apri": {"type": "boolean"},
                "titolo": {"type": "string"},
                "priorita": {"type": "string", "enum": ["alta", "media", "bassa", ""]},
                "descrizione": {"type": "string"},
            },
            "required": ["apri", "titolo", "priorita", "descrizione"],
            "additionalProperties": False,
        },
        "ordine": {
            "type": "object",
            "properties": {
                "registra": {"type": "boolean"},
                "conferma": {"type": "boolean"},        # true = ordine confermato; false = bozza
                "invia_email": {"type": "boolean"},     # true = invia riepilogo via email al cliente
                "righe": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "descrizione": {"type": "string"},
                            "quantita": {"type": ["number", "null"]},
                            "unita": {"type": "string"},
                            "prezzo_unitario": {"type": ["number", "null"]},
                        },
                        "required": ["descrizione", "quantita", "unita", "prezzo_unitario"],
                        "additionalProperties": False,
                    },
                },
                "note": {"type": "string"},
            },
            "required": ["registra", "conferma", "invia_email", "righe", "note"],
            "additionalProperties": False,
        },
        "documento": {
            "type": "object",
            "properties": {
                "invia": {"type": "boolean"},
                "categoria": {"type": "string",
                              "enum": ["listino", "schede_prodotto", "contratti", "faq", "altro", ""]},
            },
            "required": ["invia", "categoria"],
            "additionalProperties": False,
        },
    },
    "required": ["risposta", "consulta_documenti", "anagrafica", "ticket", "ordine", "documento"],
    "additionalProperties": False,
}

# Campi anagrafici testuali aggiornabili dall'LLM.
_CAMPI_TESTO = ["nome", "cognome", "ragione_sociale", "ruolo", "email", "telefono", "sede"]


def _scheda_contatto(c: Contatto) -> str:
    """Dati già noti del contatto, da mostrare all'LLM (per non richiederli)."""
    campi = [
        ("Nome", c.nome), ("Cognome", c.cognome), ("Ragione sociale", c.ragione_sociale),
        ("Ruolo", c.ruolo), ("Email", c.email), ("Telefono", c.telefono),
        ("Sede", c.sede), ("Stato", c.stato.value if c.stato else None),
        ("Note", c.note),
    ]
    noti = [f"{k}: {v}" for k, v in campi if v]
    return "\n".join(noti) if noti else "(nessun dato ancora raccolto)"


def _scheda_ordini(db: Session, contatto: Contatto, limite: int = 5) -> str:
    """Ultimi ordini del cliente (prodotti + quantità), per disambiguare prodotti e riordinare."""
    societa = crm.societa_di_contatto(db, contatto)
    q = db.query(Ordine)
    q = q.filter(Ordine.societa_id == societa.id) if societa else q.filter(Ordine.contatto_id == contatto.id)
    ordini = q.order_by(Ordine.data.desc()).limit(limite).all()
    if not ordini:
        return "(nessun ordine precedente)"
    righe = []
    for o in ordini:
        prods = "; ".join(
            f"{r.quantita or ''}{(' ' + r.unita) if r.unita else ''} {r.descrizione}".strip()
            for r in o.righe
        ) or "—"
        d = o.data.strftime("%d/%m/%Y") if o.data else ""
        righe.append(f"#{o.id} del {d} ({o.stato.value}): {prods}")
    return "\n".join(righe)


def _applica_anagrafica(db: Session, contatto: Contatto, dati: dict) -> None:
    """Aggiorna i soli campi non vuoti restituiti dall'LLM."""
    cambiato = False
    for campo in _CAMPI_TESTO:
        val = (dati.get(campo) or "").strip()
        if val and getattr(contatto, campo) != val:
            setattr(contatto, campo, val)
            cambiato = True
    stato = (dati.get("stato") or "").strip()
    if stato in (ContattoStato.CLIENTE.value, ContattoStato.PROSPECT.value):
        nuovo = ContattoStato(stato)
        if contatto.stato != nuovo:
            contatto.stato = nuovo
            cambiato = True
    if cambiato:
        db.commit()


def _applica_ticket(db: Session, contatto: Contatto, dati: dict, storia_testo: str) -> None:
    """Apre il ticket di follow-up, o aggiorna quello già aperto per il contatto."""
    if not dati.get("apri"):
        return
    titolo = (dati.get("titolo") or "Lead da ricontattare").strip()[:300]
    priorita = ticket_service.normalizza_priorita(dati.get("priorita"))
    descrizione = (dati.get("descrizione") or "").strip() or None

    esistente = _ticket_aperto(db, contatto.id)
    if esistente:
        esistente.titolo = titolo
        if priorita:
            esistente.priorita = priorita
        esistente.descrizione = descrizione
        esistente.storia = storia_testo or esistente.storia
        db.commit()
        logger.info("Ticket #%s aggiornato (contatto %s)", esistente.id, contatto.id)
    else:
        ticket_service.apri_ticket(
            db, contatto_id=contatto.id, titolo=titolo, priorita=priorita,
            descrizione=descrizione, storia=storia_testo, canale="whatsapp",
        )


def _collega_societa(db: Session, contatto: Contatto, traccia: list) -> None:
    """Registrazione prospect: se dalla conversazione emerge la società (ragione sociale)
    e il contatto non è ancora collegato, crea/aggancia la Società corrispondente
    (riusa quella esistente se già presente: niente duplicati)."""
    if contatto.societa_id:
        return
    societa = crm.societa_di_contatto(db, contatto)   # crea/collega solo se c'è una ragione sociale
    if societa:
        traccia.append({
            "fase": "Società del prospect", "modello": "—",
            "input": f"Ragione sociale rilevata: {contatto.ragione_sociale or '—'}",
            "output": f"Contatto collegato alla società «{societa.nome}» (id {societa.id}, "
                      f"{societa.stato_relazione.value})",
        })


def _applica_ordine(db: Session, contatto: Contatto, dati: dict, traccia: list) -> None:
    """Se il messaggio contiene un ordine, crea una BOZZA d'ordine sulla società del contatto."""
    if not dati.get("registra"):
        return
    righe = [r for r in (dati.get("righe") or []) if (r.get("descrizione") or "").strip()]
    if not righe:
        return
    # Aggancia (o crea al volo) la società del contatto; fallback sul nome della persona.
    societa = crm.societa_di_contatto(db, contatto)
    if not societa:
        societa = crm.trova_o_crea_societa(db, insegna=contatto.nome_completo)
        contatto.societa_id = societa.id
        contatto.is_primario = True
        db.commit()
    confermato = bool(dati.get("conferma"))
    ordine, creato = crm.registra_ordine_conversazione(
        db, societa_id=societa.id, righe=righe, contatto_id=contatto.id,
        origine=OrigineOrdine.CLIENTE, canale=CanaleOrdine.WHATSAPP,
        note=(dati.get("note") or "").strip() or None,
        stato=StatoOrdine.CONFERMATO if confermato else StatoOrdine.BOZZA,
    )
    if ordine:
        traccia.append({
            "fase": "Ordine registrato" if creato else "Ordine aggiornato",
            "modello": "—",
            "input": f"Società: {societa.nome}",
            "output": f"Ordine #{ordine.id} ({ordine.stato.value}): {ordine.n_articoli} righe, "
                      f"€ {ordine.totale:.2f}" + ("" if creato else " (esistente aggiornato, nessun duplicato)"),
        })
        if dati.get("invia_email"):
            _invia_email_ordine(db, contatto, ordine, traccia)


def _applica_documento(db: Session, contatto: Contatto, dati: dict, traccia: list) -> None:
    """Invia al cliente, via email, i documenti della categoria richiesta dall'agente."""
    if not dati.get("invia"):
        return
    categoria = (dati.get("categoria") or "").strip()
    if not categoria:
        return
    res = documenti_service.invia_documenti_email(db, contatto, categoria, profilo.nome_azienda(db))
    if res.get("inviato"):
        esito = f"Inviati a {res['email']}: {', '.join(res['documenti'])}"
    else:
        esito = res.get("messaggio") or res.get("errore") or "Non inviato."
    traccia.append({"fase": "Invio documenti via email", "modello": "—",
                    "input": f"Categoria: {categoria}", "output": esito})


def _invia_email_ordine(db: Session, contatto: Contatto, ordine, traccia: list) -> None:
    """Invia al cliente il riepilogo dell'ordine via email (se ha un indirizzo)."""
    email = (contatto.email or "").strip()
    if not email:
        traccia.append({"fase": "Email riepilogo ordine", "modello": "—",
                        "input": f"Ordine #{ordine.id}",
                        "output": "Non inviata: il contatto non ha un'email registrata."})
        return
    oggetto = f"Riepilogo ordine #{ordine.id} - {profilo.nome_azienda(db)}"
    corpo = (f"Gentile {contatto.nome or contatto.nome_completo},\n\n"
             f"come da accordi, le confermiamo il suo ordine:\n\n{crm.riepilogo_ordine(ordine)}\n\n"
             f"Cordiali saluti,\n{profilo.nome_azienda(db)}")
    inviata = email_service.invia_email(destinatario=email, oggetto=oggetto, corpo=corpo)
    traccia.append({"fase": "Email riepilogo ordine", "modello": "—",
                    "input": f"A: {email}",
                    "output": f"Riepilogo ordine #{ordine.id} inviato." if inviata
                              else "Invio email non riuscito (verifica configurazione Gmail)."})


def _chiama_llm(client: OpenAI, system: str, user: str) -> str:
    """Una chiamata lead-capture con output strutturato. Ritorna il JSON grezzo (stringa)."""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "lead_capture", "strict": True, "schema": SCHEMA},
        },
        reasoning_effort=EFFORT,
        max_completion_tokens=2000,
    )
    return resp.choices[0].message.content or "{}"


def gestisci(db: Session, telefono: str, testo: str) -> dict:
    """Gestisce un messaggio WhatsApp end-to-end. Ritorna {"risposta": str}. Non solleva."""
    contatto = trova_o_crea_contatto(db, telefono)
    _log(db, contatto.id, DirezioneMessaggio.IN, testo)

    if not OPENAI_API_KEY:
        _log(db, contatto.id, DirezioneMessaggio.OUT, MSG_SERVIZIO_NON_DISPONIBILE)
        return {"risposta": MSG_SERVIZIO_NON_DISPONIBILE}

    storia = _storia_recente(db, contatto.id)
    storia_testo = _storia_testo(storia)

    # Se scrive un AMMINISTRATORE (numero in amministratori o inoltro flaggato admin), usa il prompt
    # admin (voce_admin + moduli 'admin') e il toolset admin (promemoria). Altrimenti flusso cliente.
    admin = promemoria.is_admin(telefono, db)
    from routers import elevenlabs  # _riassunto / sostituzioni (import pigro)

    if admin:
        # Prompt dai moduli del PUBBLICO "admin", canale whatsapp (+ regole). Tutto in dashboard.
        system = (prompt_moduli.componi(db, None, audience="admin", canale="whatsapp")
                  + istruzioni.blocco_regole(db))
        for _k, _v in {"telefono_chiamante": telefono or "", "tenant": "",
                       "azienda": profilo.nome_azienda(db)}.items():
            system = system.replace("{{" + _k + "}}", _v or "")
        user = ("Stai parlando con l'AMMINISTRATORE (canale WhatsApp).\n\n"
                f"STORICO CONVERSAZIONE (ultimo messaggio in fondo):\n{storia_testo}")
        tools_set = tool_meta.applica_chat(agente_tools.SCHEMI_ADMIN)
    else:
        # Prompt WhatsApp MODULARE (moduli 'whatsapp') + conoscenza tenant + regole + catalogo + promemoria.
        system = (
            SYSTEM
            + f"\n\n{contesto_temporale()}"
            + profilo.blocco_prompt(db)
            + prompt_moduli.componi(db, None, audience="cliente", canale="whatsapp")
            + istruzioni.blocco_regole(db)
            + documenti_service.catalogo_prompt(db)
            + documenti_service.testo_sempre_presente(db)
            + profilo.contatto_campi_prompt(db)
            + entita_service.blocco_prompt(db, None)
            + promemoria.blocco_prompt(db, contatto.id)
        )
        _soc = crm.societa_di_contatto(db, contatto)
        _ultimo = (db.query(Ordine).filter(Ordine.contatto_id == contatto.id)
                   .order_by(Ordine.data.desc()).first())
        _known = bool(contatto.nome or contatto.cognome or contatto.ragione_sociale)
        _riass = (elevenlabs._riassunto(contatto, _soc, _ultimo) if _known
                  else "Contatto non riconosciuto: è un nuovo contatto da registrare.")
        _dv = {
            "cliente_conosciuto": "sì" if _known else "no",
            "riassunto_cliente": _riass,
            "telefono_chiamante": (contatto.telefono or telefono or ""),
            "tenant": "",  # WhatsApp single-tenant; il tool riceve il tenant dal codice
            "nome": contatto.nome or "", "cognome": contatto.cognome or "",
            "titolo": contatto.titolo or "", "nome_cliente": (contatto.nome_completo if _known else ""),
            "societa": (_soc.nome if _soc else (contatto.ragione_sociale or "")),
            "ruolo": contatto.ruolo or "", "email_cliente": contatto.email or "",
            "azienda": profilo.nome_azienda(db), "saluto": "", "configurazione": "",
        }
        for _k, _v in _dv.items():
            system = system.replace("{{" + _k + "}}", _v or "")
        ticket_esistente = _ticket_aperto(db, contatto.id)
        user = (
            f"TEMPO DAL MESSAGGIO PRECEDENTE DEL CLIENTE: {_tempo_da_ultimo(db, contatto.id)}\n\n"
            f"DATI GIÀ NOTI DEL CONTATTO:\n{_scheda_contatto(contatto)}\n\n"
            f"{entita_service.contesto_contatto(db, contatto.id)}\n\n"
            f"ULTIMI ORDINI DEL CLIENTE (per disambiguare prodotti e riordinare):\n{_scheda_ordini(db, contatto)}\n\n"
            f"TICKET DI FOLLOW-UP GIÀ APERTO: {'sì (aggiornalo, non duplicarlo)' if ticket_esistente else 'no'}\n\n"
            f"STORICO CONVERSAZIONE (ultimo messaggio in fondo):\n{storia_testo}"
        )
        tools_set = tool_meta.applica_chat(agente_tools.SCHEMI)

    traccia = [{
        "fase": "Identificazione contatto", "modello": "—",
        "input": f"Numero in arrivo: {telefono}",
        "output": f"Contatto id {contatto.id} — {contatto.nome_completo} ({contatto.stato.value})",
    }]

    # Loop di function-calling: gli stessi tool MCP della voce. telefono/tenant iniettati dal codice.
    tenant = ""  # WhatsApp single-tenant per ora (tenant da phone_number_id = step futuro)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    risposta = ""
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        for _giro in range(6):
            resp = client.chat.completions.create(
                model=MODEL, messages=messages, tools=tools_set,
                reasoning_effort=EFFORT, max_completion_tokens=2000,
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                risposta = (msg.content or "").strip()
                break
            # registra la richiesta di tool e poi esegui ogni tool, accodando i risultati
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                               for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                risultato = agente_tools.esegui(tc.function.name, args, telefono, tenant)
                traccia.append({"fase": f"tool: {tc.function.name}", "modello": MODEL,
                                "input": json.dumps(args, ensure_ascii=False)[:2000],
                                "output": json.dumps(risultato, ensure_ascii=False, default=str)[:2000]})
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(risultato, ensure_ascii=False, default=str)})
    except Exception as e:
        logger.error("WhatsApp agent (tool loop) fallito: %s", e)
        risposta = "Mi scusi, ho avuto un problema tecnico. Può ripetere?"
        _log(db, contatto.id, DirezioneMessaggio.OUT, risposta, traccia=traccia)
        return {"risposta": risposta}

    risposta = risposta or "Come posso aiutarla?"
    _log(db, contatto.id, DirezioneMessaggio.OUT, risposta, traccia=traccia)
    if not admin:   # il traffico admin non va nel registro conversazioni cliente
        _registra_log_conversazione(db, contatto, _storia_testo(_storia_recente(db, contatto.id)))
    return {"risposta": risposta}
