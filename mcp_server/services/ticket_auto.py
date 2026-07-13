"""Ticket automatico a fine conversazione (POST-PROCESSING).

La decisione se aprire un ticket — con che titolo/priorità/descrizione — NON la prende più
l'agente DURANTE la chiamata (che muore col canale se il cliente riaggancia): la prende
questo modulo DOPO, sulla trascrizione completa. È logica di back-office (come il riassunto
della chiamata), non un prompt che pilota l'agente col cliente.

Dedup: se esiste già un ticket APERTO per lo stesso contatto lo AGGIORNA, non lo duplica.
"""

import json
import logging
import os

from openai import OpenAI
from sqlalchemy.orm import Session

from database import Ticket, StatoTicket
from services import ticket as ticket_service

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("TICKET_AUTO_MODEL", os.getenv("VOICE_LOG_MODEL", "gpt-5-mini"))

SYSTEM = (
    "Sei l'assistente di back-office di un'azienda. Ricevi la TRASCRIZIONE di una conversazione "
    "(telefono o chat) fra un lead/cliente e l'assistente. Decidi se aprire un TICKET di follow-up "
    "per il team commerciale. Rispondi SOLO con JSON valido: "
    '{"apri": true|false, "titolo": "...", "priorita": "alta|media|bassa", "descrizione": "..."}. '
    "Metti apri=true se il lead ha bisogno di qualcosa, ha chiesto info/preventivo/listino, ha un "
    "reclamo o va comunque ricontattato — ANCHE se «ci pensa» o non ordina. Metti apri=false SOLO se "
    "non c'è stato dialogo utile (silenzio, numero sbagliato, riaggancio immediato). "
    "PRIORITÀ: alta = cliente storico, oppure urgenza entro 24h, oppure nuovo locale ad alto volume; "
    "media = ordine/richiesta ordinaria di un cliente attivo; bassa = solo listino o informazioni. "
    "titolo: max ~10 parole. descrizione: sintesi della richiesta e dei dati raccolti."
)


def _decidi(trascrizione: str) -> dict:
    """Chiede all'LLM la decisione strutturata. Ritorna {} (= non aprire) in caso di errore."""
    if not OPENAI_API_KEY or not trascrizione.strip():
        return {}
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": trascrizione}],
            reasoning_effort="low",
            max_completion_tokens=500,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        logger.error("Decisione ticket automatica fallita: %s", e)
        return {}


def _ticket_aperto(db: Session, contatto_id: int) -> Ticket | None:
    return (db.query(Ticket)
            .filter(Ticket.contatto_id == contatto_id, Ticket.stato == StatoTicket.APERTO)
            .order_by(Ticket.created_at.desc()).first())


def genera_da_trascrizione(db: Session, contatto_id: int, trascrizione: str,
                           canale: str = "voce", azienda_id: int | None = None) -> Ticket | None:
    """Crea o aggiorna il ticket di follow-up a partire dalla trascrizione. Ritorna il Ticket
    (nuovo o aggiornato) oppure None se non serve. Non solleva."""
    try:
        trascrizione = (trascrizione or "").strip()
        if not contatto_id or not trascrizione:
            return None
        dec = _decidi(trascrizione)
        esistente = _ticket_aperto(db, contatto_id)

        if not dec.get("apri"):
            # Nessun ticket nuovo; se ne esiste uno aperto, arricchiscine la storia con il transcript.
            if esistente:
                esistente.storia = trascrizione
                db.commit()
            return esistente

        titolo = (dec.get("titolo") or "Lead da ricontattare").strip()[:300]
        priorita = ticket_service.normalizza_priorita(dec.get("priorita"))
        descrizione = (dec.get("descrizione") or "").strip() or None

        if esistente:
            esistente.titolo = titolo
            if priorita:
                esistente.priorita = priorita
            esistente.descrizione = descrizione or esistente.descrizione
            esistente.storia = trascrizione or esistente.storia
            db.commit()
            logger.info("Ticket #%s aggiornato dal post-processing (contatto %s)", esistente.id, contatto_id)
            return esistente

        return ticket_service.apri_ticket(
            db, contatto_id=contatto_id, titolo=titolo, priorita=priorita,
            descrizione=descrizione or "", storia=trascrizione, canale=canale, azienda_id=azienda_id)
    except Exception as e:
        logger.error("Ticket automatico fallito (contatto %s): %s", contatto_id, e)
        db.rollback()
        return None
