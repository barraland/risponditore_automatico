"""Agente WhatsApp: fa da ponte tra i messaggi del condomino e l'agente
risponditore (services/agente.py).

Per ogni messaggio in arrivo:
1. Identifica il condomino dal numero di telefono (anagrafica inquilini).
   - Se non registrato → risponde che non può servire il numero.
2. Determina il condominio (per ora: 1 condominio per utente = quello dell'inquilino).
3. Salva la storia della conversazione (ultimi N messaggi, N configurabile).
4. Un LLM "interfaccia" legge la storia recente e riformula l'ultimo messaggio in
   UNA domanda autosufficiente da girare all'agente risponditore (oppure riconosce
   un saluto/chiacchiera e risponde direttamente, senza interrogare i documenti).
5. Chiama l'agente risponditore e restituisce la risposta.

È sincrono e indipendente dal trasporto: il webhook si limita a chiamarlo e a
inviare la stringa restituita via WhatsApp.
"""

import json
import logging
import os
import re

from openai import OpenAI
from sqlalchemy.orm import Session

from database import Inquilino, Condominio, Documento, MessaggioChat, DirezioneMessaggio, StatoDocumento
from services import agente
from services import email as email_service
from services import invii_email
from services import ticket as ticket_service
from services.contesto import contesto_temporale

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("WHATSAPP_AGENT_MODEL", "gpt-5-mini")
EFFORT = os.getenv("WHATSAPP_AGENT_EFFORT", "low")
STORIA_MAX = int(os.getenv("WHATSAPP_STORIA_MAX", "10"))

MSG_NON_REGISTRATO = (
    "Salve! Questo numero non risulta registrato tra i condòmini, quindi non posso fornire "
    "informazioni. Se pensa sia un errore, contatti il suo amministratore di condominio."
)
MSG_SALUTO = (
    "Salve! Sono l'assistente del suo condominio. Può chiedermi informazioni su rate e scadenze, "
    "spese e riparti, consumi, verbali, regolamento e altri documenti. Come posso aiutarla?"
)
MSG_ATTESA = "Un attimo, controllo nei documenti del condominio…"
MSG_CHIEDI_EMAIL = "Volentieri! A quale indirizzo email glielo invio?"
MSG_EMAIL_NON_VALIDA = "Non ho riconosciuto un indirizzo email valido. Può riscriverlo per favore?"
MSG_RIFIUTO = "Va bene, non lo invio. Se le serve altro, mi scriva pure."
MSG_INVIO_FALLITO = "Mi dispiace, l'invio della mail non è riuscito. Riprovi più tardi o contatti l'amministratore."
MSG_DOC_NON_DISPONIBILE = "Mi dispiace, quel documento non è più disponibile."
MSG_TICKET_RECLAMO = (
    "Mi dispiace per il disagio. Ho aperto una segnalazione per l'amministratore, "
    "che la ricontatterà al più presto. Posso aiutarla con altro?"
)
MSG_TICKET_NO_RISPOSTA = (
    "\n\nNon ho trovato il dato nei documenti: ho aperto una segnalazione per "
    "l'amministratore, che la ricontatterà."
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _estrai_email(testo: str) -> str | None:
    m = _EMAIL_RE.search(testo or "")
    return m.group(0) if m else None


def _invia_documento(inquilino: Inquilino, doc: Documento) -> bool:
    """Invia il documento del condominio all'email dell'inquilino."""
    return email_service.invia_email(
        destinatario=inquilino.email,
        oggetto=f"Documento del condominio: {doc.nome_file}",
        corpo=(
            f"Gentile {inquilino.nome},\n\n"
            f"in allegato il documento richiesto: {doc.nome_file}.\n\n"
            f"Cordiali saluti,\nL'assistente del condominio"
        ),
        allegati=[doc.percorso],
    )


# ---------- Identificazione numero ----------

def _chiave_tel(tel: str) -> str:
    """Normalizza un numero al 'core' nazionale (ultime 10 cifre) per il confronto.

    Gestisce prefissi internazionali e formattazioni varie (+39, 0039, spazi, ecc.).
    """
    d = re.sub(r"\D", "", tel or "")
    if d.startswith("00"):
        d = d[2:]
    return d[-10:] if len(d) >= 10 else d


def trova_inquilino(db: Session, telefono: str) -> Inquilino | None:
    """Trova l'inquilino il cui numero corrisponde (match sulle ultime cifre)."""
    chiave = _chiave_tel(telefono)
    if not chiave:
        return None
    for inq in db.query(Inquilino).filter(Inquilino.telefono.isnot(None)).all():
        if _chiave_tel(inq.telefono) == chiave:
            return inq
    return None


# ---------- Storia conversazione ----------

def _log(db: Session, inquilino_id: int, direzione: DirezioneMessaggio, testo: str, traccia=None):
    db.add(MessaggioChat(
        inquilino_id=inquilino_id, direzione=direzione, testo=testo,
        traccia=json.dumps(traccia, ensure_ascii=False) if traccia else None,
    ))
    db.commit()


def _storia_recente(db: Session, inquilino_id: int) -> list[MessaggioChat]:
    """Ultimi STORIA_MAX messaggi (in ordine cronologico)."""
    msgs = (
        db.query(MessaggioChat)
        .filter(MessaggioChat.inquilino_id == inquilino_id)
        .order_by(MessaggioChat.timestamp.desc())
        .limit(STORIA_MAX)
        .all()
    )
    return list(reversed(msgs))


# ---------- Riformulazione (LLM interfaccia) ----------

RIFORMULA_SYSTEM = """Sei l'interfaccia WhatsApp di un assistente condominiale. Ricevi i dati del
condomino che scrive e lo storico recente della conversazione. Devi capire cosa vuole con l'ultimo
messaggio.

IMPORTANTE — identità: conosci nome, cognome e unità di chi scrive. Quando l'utente usa la prima
persona ("quanto HO consumato", "qual è la MIA rata", "il MIO appartamento"), riscrivi la domanda
ESPLICITANDO il suo nome e cognome (e l'unità se utile), così che il sistema documentale possa
cercarlo per nome. Esempio: "quanto ho consumato nel 2025?" → "Quanto ha consumato Mario Rossi
(unità Scala A - Int. 1) nel 2025?".

Classifica l'ultimo messaggio dell'utente:
- tipo = "domanda": è una richiesta di informazioni a cui si può rispondere consultando i documenti
  del condominio (rate, spese, riparti, consumi, verbali, regolamento, scadenze, fornitori, ecc.).
  In questo caso riformula in UNA domanda autosufficiente e completa, risolvendo sia i riferimenti al
  contesto precedente (es. "e per l'anno scorso?") sia la prima persona (vedi sopra).
- tipo = "saluto": è solo un saluto, un ringraziamento o convenevoli, senza una richiesta concreta.
- tipo = "reclamo": il condomino si LAMENTA, contesta, è insoddisfatto/non convinto, insiste, o
  segnala un problema/guasto da risolvere (non una semplice domanda informativa).
- tipo = "altro": non è una domanda interpretabile (messaggio vuoto, non pertinente, incomprensibile).

Per "domanda" compila il campo "domanda". Per "reclamo" metti in "domanda" una breve sintesi del
problema (una frase). Per "saluto"/"altro" lascia "domanda" vuota."""

RIFORMULA_SCHEMA = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": ["domanda", "saluto", "reclamo", "altro"]},
        "domanda": {"type": "string"},
    },
    "required": ["tipo", "domanda"],
    "additionalProperties": False,
}


def _storia_testo(storia: list[MessaggioChat]) -> str:
    """Trascrizione leggibile della conversazione (per il ticket)."""
    return "\n".join(
        ("Condomino" if m.direzione == DirezioneMessaggio.IN else "Assistente") + f": {m.testo}"
        for m in storia
    )


def riformula(client: OpenAI, inquilino: Inquilino, storia: list[MessaggioChat], trace=None) -> dict:
    transcript = "\n".join(
        ("Condomino" if m.direzione == DirezioneMessaggio.IN else "Assistente") + f": {m.testo}"
        for m in storia
    )
    mittente = (
        f"MITTENTE: {inquilino.nome} {inquilino.cognome or ''}".strip()
        + (f" — unità: {inquilino.unita}" if inquilino.unita else "")
    )
    user = f"{mittente}\n\nSTORICO CONVERSAZIONE:\n{transcript}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": f"{RIFORMULA_SYSTEM}\n\n{contesto_temporale()}"},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "riformulazione", "strict": True, "schema": RIFORMULA_SCHEMA},
        },
        reasoning_effort=EFFORT,
        max_completion_tokens=1000,
    )
    raw = resp.choices[0].message.content or "{}"
    if trace is not None:
        trace.append({"fase": "Riformulazione domanda", "modello": MODEL, "input": user[:6000], "output": raw[:6000]})
    return json.loads(raw)


# ---------- Classificazione conferma (per l'offerta del documento) ----------

CONFERMA_SCHEMA = {
    "type": "object",
    "properties": {"intento": {"type": "string", "enum": ["conferma", "rifiuto", "nuova_domanda"]}},
    "required": ["intento"],
    "additionalProperties": False,
}


def classifica_conferma(client: OpenAI, testo: str, doc_nome: str, trace=None) -> str:
    """L'assistente ha appena offerto un documento via email: classifica la risposta."""
    system = (
        f"L'assistente ha appena chiesto al condomino se vuole ricevere via email il documento "
        f"«{doc_nome}». Classifica l'ultimo messaggio del condomino:\n"
        "- conferma: vuole riceverlo (sì, ok, mandamela, certo, va bene...).\n"
        "- rifiuto: non lo vuole (no, lascia stare, non serve...).\n"
        "- nuova_domanda: non sta rispondendo all'offerta ma fa un'altra richiesta/domanda."
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": testo}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "conferma", "strict": True, "schema": CONFERMA_SCHEMA}},
        reasoning_effort=EFFORT,
        max_completion_tokens=500,
    )
    raw = resp.choices[0].message.content or "{}"
    if trace is not None:
        trace.append({"fase": "Classificazione conferma", "modello": MODEL, "input": testo[:2000], "output": raw})
    try:
        return json.loads(raw).get("intento", "nuova_domanda")
    except (ValueError, TypeError):
        return "nuova_domanda"


def _gestisci_offerta(db: Session, client: OpenAI, inquilino: Inquilino, testo: str, traccia: list):
    """Gestisce un'offerta di documento in sospeso.

    Ritorna un dict azione="diretto" se l'offerta viene risolta (invio/rifiuto/email),
    oppure None se il messaggio è una nuova domanda (l'offerta decade, si prosegue).
    """
    doc = db.get(Documento, inquilino.offerta_doc_id)

    def _chiudi():
        inquilino.offerta_doc_id = None
        inquilino.offerta_attende_email = False
        db.commit()

    if not doc:
        _chiudi()
        _log(db, inquilino.id, DirezioneMessaggio.OUT, MSG_DOC_NON_DISPONIBILE, traccia=traccia)
        return {"azione": "diretto", "testo": MSG_DOC_NON_DISPONIBILE}

    # Fase: stiamo aspettando che il condomino indichi l'email.
    if inquilino.offerta_attende_email:
        email = _estrai_email(testo)
        if not email:
            _log(db, inquilino.id, DirezioneMessaggio.OUT, MSG_EMAIL_NON_VALIDA, traccia=traccia)
            return {"azione": "diretto", "testo": MSG_EMAIL_NON_VALIDA}  # stato invariato: richiede di nuovo
        inquilino.email = email  # salva in anagrafica
        ok = _invia_documento(inquilino, doc)
        if ok:
            invii_email.registra_invio(db, inquilino.id, doc.id, email)
        _chiudi()
        msg = (f"Ok, ho inviato «{doc.nome_file}» a {email}: dovrebbe riceverlo tra qualche secondo."
               if ok else MSG_INVIO_FALLITO)
        _log(db, inquilino.id, DirezioneMessaggio.OUT, msg, traccia=traccia)
        return {"azione": "diretto", "testo": msg}

    # Fase: aspettiamo sì/no all'offerta.
    intento = classifica_conferma(client, testo, doc.nome_file, trace=traccia)
    if intento == "conferma":
        if inquilino.email:
            ok = _invia_documento(inquilino, doc)
            if ok:
                invii_email.registra_invio(db, inquilino.id, doc.id, inquilino.email)
            _chiudi()
            msg = (f"Ok, ho inviato «{doc.nome_file}» a {inquilino.email}: dovrebbe riceverlo tra qualche secondo."
                   if ok else MSG_INVIO_FALLITO)
            _log(db, inquilino.id, DirezioneMessaggio.OUT, msg, traccia=traccia)
            return {"azione": "diretto", "testo": msg}
        # Nessuna email in anagrafica: chiediamola.
        inquilino.offerta_attende_email = True
        db.commit()
        _log(db, inquilino.id, DirezioneMessaggio.OUT, MSG_CHIEDI_EMAIL, traccia=traccia)
        return {"azione": "diretto", "testo": MSG_CHIEDI_EMAIL}

    if intento == "rifiuto":
        _chiudi()
        _log(db, inquilino.id, DirezioneMessaggio.OUT, MSG_RIFIUTO, traccia=traccia)
        return {"azione": "diretto", "testo": MSG_RIFIUTO}

    # nuova_domanda: l'offerta decade, si prosegue col flusso normale.
    _chiudi()
    return None


# ---------- Orchestrazione (lato WhatsApp; l'agente risponditore resta puro) ----------
#
# Il turno è in due fasi così il chiamante (webhook) può inviare un messaggio
# interlocutorio ("un attimo, controllo…") PRIMA di avviare la ricerca documentale,
# che è la parte lenta. La logica del messaggio di attesa vive qui (lato WhatsApp):
# l'agente risponditore (services/agente.py) non ne sa nulla e resterà riusabile
# tale e quale anche dagli agenti mail e voce.

def interpreta(db: Session, telefono: str, testo: str) -> dict:
    """Prima fase del turno: identifica il mittente, registra il messaggio, riformula
    e decide cosa fare. Ritorna un dict con "azione":

      - "diretto": rispondi subito con "testo" (numero non registrato, saluto,
        servizio non disponibile); l'eventuale OUT è già registrato qui.
      - "cerca": serve interrogare i documenti. Restituisce "messaggio_attesa" (da
        inviare PRIMA della ricerca), più "domanda", "condominio_id", "inquilino_id"
        e "traccia" da passare a completa().

    Sincrono e self-contained. Non solleva.
    """
    inquilino = trova_inquilino(db, telefono)
    if not inquilino:
        logger.info("Numero non registrato: %s", telefono)
        return {"azione": "diretto", "testo": MSG_NON_REGISTRATO}

    _log(db, inquilino.id, DirezioneMessaggio.IN, testo)

    # Traccia del turno: parte con la diagnostica di identificazione (a quale condominio
    # è associato il mittente e quanti documenti sono pronti).
    cond = db.get(Condominio, inquilino.condominio_id)
    n_ready = sum(1 for d in (cond.documenti if cond else []) if d.stato == StatoDocumento.READY)
    traccia = [{
        "fase": "Identificazione mittente",
        "modello": "—",
        "input": f"Numero in arrivo: {telefono}",
        "output": (
            f"Inquilino: {inquilino.nome} {inquilino.cognome or ''} (id {inquilino.id})\n"
            f"Condominio associato: id {inquilino.condominio_id} «{cond.nome if cond else '?'}»\n"
            f"Documenti pronti (ready) nel condominio: {n_ready}"
        ),
    }]

    if not OPENAI_API_KEY:
        risposta = "Servizio momentaneamente non disponibile. Riprovi più tardi."
        _log(db, inquilino.id, DirezioneMessaggio.OUT, risposta, traccia=traccia)
        return {"azione": "diretto", "testo": risposta}

    client = OpenAI(api_key=OPENAI_API_KEY)

    # C'è un'offerta di documento in sospeso? Gestiscila prima del flusso normale.
    if inquilino.offerta_doc_id is not None:
        esito_offerta = _gestisci_offerta(db, client, inquilino, testo, traccia)
        if esito_offerta is not None:
            return esito_offerta
        # else: era una nuova domanda, l'offerta è decaduta → prosegui sotto.

    storia = _storia_recente(db, inquilino.id)

    try:
        rif = riformula(client, inquilino, storia, trace=traccia)
    except Exception as e:
        logger.error("Riformulazione fallita: %s", e)
        rif = {"tipo": "domanda", "domanda": testo}  # fallback: usa il testo grezzo

    tipo = rif.get("tipo", "domanda")
    domanda = (rif.get("domanda") or "").strip()

    # Reclamo / lamentela / segnalazione: apri un ticket per l'amministratore.
    if tipo == "reclamo":
        sintesi = domanda or testo
        ticket_service.apri_ticket(
            db, condominio_id=inquilino.condominio_id, inquilino_id=inquilino.id,
            titolo=("Reclamo: " + sintesi)[:120],
            descrizione=f"Messaggio del condomino: «{testo}»",
            storia=_storia_testo(storia),   # include già il messaggio appena ricevuto
            canale="whatsapp",
        )
        _log(db, inquilino.id, DirezioneMessaggio.OUT, MSG_TICKET_RECLAMO, traccia=traccia)
        return {"azione": "diretto", "testo": MSG_TICKET_RECLAMO}

    # Saluto / chiacchiera: risposta diretta, niente ricerca documentale.
    if tipo != "domanda" or not domanda:
        _log(db, inquilino.id, DirezioneMessaggio.OUT, MSG_SALUTO, traccia=traccia)
        return {"azione": "diretto", "testo": MSG_SALUTO}

    logger.info("Inquilino %s (cond %s) -> domanda: %s", inquilino.id, inquilino.condominio_id, domanda)
    return {
        "azione": "cerca",
        "messaggio_attesa": MSG_ATTESA,
        "domanda": domanda,
        "condominio_id": inquilino.condominio_id,
        "inquilino_id": inquilino.id,
        "traccia": traccia,
    }


def completa(db: Session, inquilino_id: int, condominio_id: int, domanda: str, traccia: list) -> dict:
    """Seconda fase: invoca l'agente risponditore (puro), registra la risposta e,
    se la risposta è fondata su un documento, predispone l'offerta di invio via email.

    Ritorna {"risposta": str, "offerta": str|None}. Da chiamare DOPO il messaggio di
    attesa. Non solleva.
    """
    esito = agente.rispondi(db, condominio_id, domanda, trace=traccia)
    risposta = esito.get("risposta") or "Mi dispiace, non sono riuscito a elaborare la risposta."

    # Log accessi del risponditore: quali sezioni di quali documenti ha consultato.
    traccia.append({
        "fase": "Documenti consultati", "modello": "—",
        "input": f"Domanda: {domanda}", "output": agente.formatta_accessi(esito),
    })

    # Documento-fonte da cui è stata tratta la risposta?
    offerta = None
    fonti = [p for p in esito.get("passi", []) if p.get("trovato") and p.get("documento_id")]
    if fonti:
        primo = fonti[0]
        if invii_email.gia_inviato(db, inquilino_id, primo["documento_id"]):
            # Già inviato in precedenza: non rioffrire, ma citalo esplicitamente.
            risposta += (f"\n\nTrova questi dati nel documento «{primo['documento']}», "
                         f"che le ho già inviato via email.")
        else:
            inq = db.get(Inquilino, inquilino_id)
            inq.offerta_doc_id = primo["documento_id"]
            inq.offerta_attende_email = False
            db.commit()
            dest = f" a {inq.email}" if inq.email else ""
            offerta = f"Vuole ricevere anche «{primo['documento']}» via email{dest}? (sì/no)"
    else:
        # Nessuna fonte trovata: il dato non è nei documenti → apri una segnalazione.
        ticket_service.apri_ticket(
            db, condominio_id=condominio_id, inquilino_id=inquilino_id,
            titolo=("Domanda senza risposta: " + domanda)[:120],
            descrizione=f"L'assistente non ha trovato il dato nei documenti.\nDomanda: {domanda}",
            storia=_storia_testo(_storia_recente(db, inquilino_id)),
            canale="whatsapp",
        )
        risposta += MSG_TICKET_NO_RISPOSTA

    _log(db, inquilino_id, DirezioneMessaggio.OUT, risposta, traccia=traccia)
    if offerta:
        _log(db, inquilino_id, DirezioneMessaggio.OUT, offerta, traccia=None)

    return {"risposta": risposta, "offerta": offerta}
