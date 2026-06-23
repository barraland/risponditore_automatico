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

from database import (
    Contatto, ContattoStato, MessaggioChat, DirezioneMessaggio, Ticket, StatoTicket,
)
from database import CanaleOrdine, OrigineOrdine, StatoOrdine, Ordine
from services import ticket as ticket_service
from services import istruzioni
from services import profilo
from services import retriever
from services import crm
from services import email as email_service
from services import documenti as documenti_service
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


def trova_contatto(db: Session, telefono: str) -> Contatto | None:
    """Trova il contatto il cui numero corrisponde (match sulle ultime cifre)."""
    chiave = _chiave_tel(telefono)
    if not chiave:
        return None
    for c in db.query(Contatto).filter(Contatto.telefono.isnot(None)).all():
        if _chiave_tel(c.telefono) == chiave:
            return c
    return None


def trova_o_crea_contatto(db: Session, telefono: str) -> Contatto:
    """Ritorna il contatto del numero, creandone uno nuovo (prospect) se sconosciuto."""
    c = trova_contatto(db, telefono)
    if c:
        return c
    c = Contatto(telefono=telefono, stato=ContattoStato.PROSPECT)
    db.add(c)
    db.commit()
    db.refresh(c)
    logger.info("Nuovo lead creato dal numero %s (id %s)", telefono, c.id)
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


def _ticket_aperto(db: Session, contatto_id: int) -> Ticket | None:
    """Eventuale ticket già aperto per il contatto (per non crearne duplicati)."""
    return (
        db.query(Ticket)
        .filter(Ticket.contatto_id == contatto_id, Ticket.stato == StatoTicket.APERTO)
        .order_by(Ticket.created_at.desc())
        .first()
    )


# ---------- LLM lead-capture ----------

SYSTEM = """Sei l'assistente WhatsApp di un'azienda e ti occupi della LEAD CAPTURE: rispondi a
clienti e potenziali clienti che chiedono informazioni su prodotti, servizi, ordini, costi e
tempistiche, e nel frattempo raccogli i loro dati e qualifichi il lead per il team commerciale.

REGOLE:
- Rispondi in italiano, con tono cordiale e professionale, messaggi brevi adatti a WhatsApp.
- Per domande su prodotti/servizi/costi usa le informazioni della sezione "COSA OFFRIAMO".
- Se per rispondere ti servono dettagli che NON sono in "COSA OFFRIAMO" ma potrebbero trovarsi nei
  DOCUMENTI caricati (listini, schede prodotto, contratti, condizioni, FAQ, file Excel/CSV...), NON
  inventare: imposta consulta_documenti.serve=true e scrivi in consulta_documenti.domanda una domanda
  chiara e autosufficiente (con tutto il contesto utile) per l'agente che consulta i documenti. In
  quel caso metti pure una "risposta" interlocutoria breve (es. "Verifico subito."): ti verrà fornita
  la risposta tratta dai documenti e potrai completare. Quando NON serve, consulta_documenti.serve=false
  e consulta_documenti.domanda="".
- Se nemmeno i documenti hanno l'informazione, dillo con onestà senza inventare, e rassicura che un
  collega ricontatterà il lead.
- Raccogli con naturalezza (non come un interrogatorio) le informazioni indicate in
  "COME QUALIFICARE IL LEAD". Chiedi pochi dati per volta, integrandoli nella conversazione.
- Aggiorna l'anagrafica con i dati che emergono: compila SOLO i campi che hai effettivamente
  appreso in questa conversazione; lascia "" gli altri. Non inventare dati.
- Apri SEMPRE un ticket di follow-up per il lead (uno solo per conversazione): metti apri=true
  appena hai capito di cosa ha bisogno il lead, con un titolo riassuntivo, una descrizione
  sintetica della richiesta e la priorità (alta/media/bassa) secondo i criteri forniti. Se per il
  contatto risulta GIÀ un ticket aperto, tienilo aggiornato (apri=true: titolo/priorità/descrizione
  aggiornati), non temere i duplicati (il sistema aggiorna quello esistente).
- Se non hai ancora elementi sufficienti (es. solo un saluto), apri=false e prosegui la raccolta.
- ORDINI: se il cliente sta ordinando o riordinando prodotti (con quantità), imposta
  ordine.registra=true ed elenca in ordine.righe i prodotti (descrizione, quantità, unità di
  misura, e prezzo_unitario se lo conosci dai documenti/listino, altrimenti null). Imposta
  ordine.conferma=true per registrarlo come CONFERMATO oppure false per lasciarlo in bozza, secondo
  le indicazioni dell'amministratore su quando confermare. Se devi inviare al cliente il riepilogo
  via email imposta ordine.invia_email=true, ma SOLO se conosci la sua email (campo email
  dell'anagrafica); se non ce l'hai, chiedila nel messaggio e lascia invia_email=false per ora.
  Riepiloga nel messaggio cosa hai registrato. Se NON è un ordine: registra=false, conferma=false,
  invia_email=false, righe=[].
- INVIO DOCUMENTI: hai a disposizione l'invio via email dei documenti caricati (es. listino,
  condizioni/costi di consegna). Se devi inviarne uno, imposta documento.invia=true e documento.categoria
  (tra: listino, schede_prodotto, contratti, faq, altro), SOLO se conosci l'email del cliente; se non ce
  l'hai, chiedila nel messaggio e lascia invia=false per ora. Usalo secondo le indicazioni
  dell'amministratore. Se non serve: documento.invia=false e documento.categoria="".

Compila SEMPRE tutti i campi dell'output: usa "" per i valori non noti."""

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

    system = (
        SYSTEM
        + f"\n\n{contesto_temporale()}"
        + profilo.blocco_prompt(db)
        + istruzioni.blocco_prompt(db, canale="whatsapp")
    )
    ticket_esistente = _ticket_aperto(db, contatto.id)
    user = (
        f"DATI GIÀ NOTI DEL CONTATTO:\n{_scheda_contatto(contatto)}\n\n"
        f"ULTIMI ORDINI DEL CLIENTE (per disambiguare prodotti e riordinare):\n{_scheda_ordini(db, contatto)}\n\n"
        f"TICKET DI FOLLOW-UP GIÀ APERTO: {'sì (aggiornalo)' if ticket_esistente else 'no'}\n\n"
        f"STORICO CONVERSAZIONE (ultimo messaggio in fondo):\n{storia_testo}"
    )

    traccia = [{
        "fase": "Identificazione contatto", "modello": "—",
        "input": f"Numero in arrivo: {telefono}",
        "output": f"Contatto id {contatto.id} — {contatto.nome_completo} ({contatto.stato.value})",
    }]

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        raw = _chiama_llm(client, system, user)
        traccia.append({"fase": "Lead capture", "modello": MODEL,
                        "input": user[:6000], "output": raw[:6000]})
        out = json.loads(raw)

        # Se l'agente chiede di consultare i documenti, interroga il retriever e
        # rigenera la risposta avendo in contesto l'output dei documenti (un solo giro).
        cons = out.get("consulta_documenti") or {}
        domanda_doc = (cons.get("domanda") or "").strip()
        if cons.get("serve") and domanda_doc:
            esito = retriever.rispondi(db, domanda_doc)
            risposta_doc = esito.get("risposta", "")
            traccia.append({"fase": "Consultazione documenti (retriever)", "modello": retriever.MODEL,
                            "input": domanda_doc, "output": risposta_doc[:6000]})
            user2 = (
                f"{user}\n\n"
                f"HAI CHIESTO ALL'AGENTE DOCUMENTI: «{domanda_doc}»\n"
                f"RISPOSTA TRATTA DAI DOCUMENTI:\n{risposta_doc}\n\n"
                "Usa questa risposta per rispondere al lead. Non richiedere di nuovo i documenti "
                "(consulta_documenti.serve=false)."
            )
            raw = _chiama_llm(client, system, user2)
            traccia.append({"fase": "Lead capture (post-documenti)", "modello": MODEL,
                            "input": user2[:6000], "output": raw[:6000]})
            out = json.loads(raw)
    except Exception as e:
        logger.error("Lead capture LLM fallita: %s", e)
        risposta = "Mi scusi, ho avuto un problema tecnico. Può ripetere?"
        _log(db, contatto.id, DirezioneMessaggio.OUT, risposta, traccia=traccia)
        return {"risposta": risposta}

    _applica_anagrafica(db, contatto, out.get("anagrafica") or {})
    _collega_societa(db, contatto, traccia)
    # Storia aggiornata (include il messaggio appena ricevuto) per il ticket.
    _applica_ticket(db, contatto, out.get("ticket") or {}, _storia_testo(_storia_recente(db, contatto.id)))
    _applica_ordine(db, contatto, out.get("ordine") or {}, traccia)
    _applica_documento(db, contatto, out.get("documento") or {}, traccia)

    risposta = (out.get("risposta") or "").strip() or "Come posso aiutarla?"
    _log(db, contatto.id, DirezioneMessaggio.OUT, risposta, traccia=traccia)
    return {"risposta": risposta}
