"""Gestione ticket / segnalazioni.

Il risponditore (voce o WhatsApp) apre un ticket per ogni lead gestito: una scheda di
follow-up con titolo riassuntivo, priorità (alta/media/bassa) e trascrizione della
conversazione. I ticket aperti sono visibili in dashboard.
"""

import logging

from sqlalchemy.orm import Session

from database import Ticket, StatoTicket, PrioritaTicket

logger = logging.getLogger(__name__)


def normalizza_priorita(valore) -> PrioritaTicket | None:
    """Converte una stringa ('alta'/'media'/'bassa', case-insensitive) in PrioritaTicket."""
    if isinstance(valore, PrioritaTicket):
        return valore
    if not valore:
        return None
    try:
        return PrioritaTicket(str(valore).strip().lower())
    except ValueError:
        return None


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


def apri_ticket(db: Session, contatto_id, titolo: str, priorita=None,
                descrizione: str = "", storia: str = "", canale: str = "") -> Ticket | None:
    """Crea un ticket aperto. Non solleva."""
    try:
        t = Ticket(
            contatto_id=contatto_id,
            canale=canale or None,
            titolo=(titolo or "Segnalazione").strip()[:300],
            priorita=normalizza_priorita(priorita),
            descrizione=(descrizione or "").strip() or None,
            storia=(storia or "").strip() or None,
            stato=StatoTicket.APERTO,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        logger.info("Ticket #%s aperto (contatto=%s, priorità=%s, canale=%s)",
                    t.id, contatto_id, t.priorita.value if t.priorita else "-", canale)
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
