"""Gestione ticket / segnalazioni.

L'assistente (voce o WhatsApp) apre un ticket quando non riesce a rispondere
(dato mancante) o quando il condomino si lamenta / insiste. I ticket aperti
sono visibili in dashboard, in vista trasversale su tutti i condomìni.
"""

import logging

from sqlalchemy.orm import Session

from database import Ticket, StatoTicket

logger = logging.getLogger(__name__)


def formatta_storia(turni) -> str:
    """turni: lista di {'ruolo': ..., 'testo': ...} oppure di MessaggioChat-like.
    Ritorna un testo leggibile o stringa vuota."""
    righe = []
    for t in (turni or []):
        if isinstance(t, dict):
            ruolo = t.get("ruolo", "")
            testo = (t.get("testo") or "").strip()
        else:
            ruolo, testo = "", str(t).strip()
        if testo:
            righe.append(f"{ruolo}: {testo}" if ruolo else testo)
    return "\n".join(righe)


def apri_ticket(db: Session, condominio_id, inquilino_id, titolo: str,
                descrizione: str = "", storia: str = "", canale: str = "") -> Ticket | None:
    """Crea un ticket aperto. Non solleva."""
    try:
        t = Ticket(
            condominio_id=condominio_id,
            inquilino_id=inquilino_id,
            canale=canale or None,
            titolo=(titolo or "Segnalazione").strip()[:300],
            descrizione=(descrizione or "").strip() or None,
            storia=(storia or "").strip() or None,
            stato=StatoTicket.APERTO,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        logger.info("Ticket #%s aperto (cond=%s, inq=%s, canale=%s)", t.id, condominio_id, inquilino_id, canale)
        return t
    except Exception as e:
        logger.error("Apertura ticket fallita: %s", e)
        db.rollback()
        return None


def chiudi_ticket(db: Session, ticket_id: int) -> None:
    """Segna un ticket come chiuso. Non solleva."""
    try:
        t = db.get(Ticket, ticket_id)
        if t:
            t.stato = StatoTicket.CHIUSO
            db.commit()
    except Exception as e:
        logger.error("Chiusura ticket %s fallita: %s", ticket_id, e)
        db.rollback()
