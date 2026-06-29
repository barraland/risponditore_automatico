"""Gestione ticket / segnalazioni.

Il risponditore (voce o WhatsApp) apre un ticket per ogni lead gestito: una scheda di
follow-up con titolo riassuntivo, priorità (alta/media/bassa) e trascrizione della
conversazione. I ticket aperti sono visibili in dashboard.
"""

import logging
import threading

from sqlalchemy.orm import Session

from database import Ticket, StatoTicket, PrioritaTicket, Amministratore, Contatto, SessionLocal
from services import email as email_service

logger = logging.getLogger(__name__)

_CAMPO_PRIORITA = {"alta": "inoltra_alta", "media": "inoltra_media", "bassa": "inoltra_bassa"}


def _inoltra_ticket_admin(ticket_id: int) -> None:
    """Invia il ticket via email agli amministratori che hanno il flag attivo per la sua priorità.
    Gira in un thread separato (apre la propria sessione) per non rallentare la chiamata."""
    db = SessionLocal()
    try:
        t = db.get(Ticket, ticket_id)
        if not t or not t.priorita:
            return
        campo = _CAMPO_PRIORITA.get(t.priorita.value)
        if not campo:
            return
        admins = db.query(Amministratore).filter(getattr(Amministratore, campo).is_(True)).all()
        dest = [(a.email or "").strip() for a in admins if (a.email or "").strip()]
        if not dest:
            return
        c = db.get(Contatto, t.contatto_id) if t.contatto_id else None
        cliente = (c.nome_completo if c else "Contatto sconosciuto")
        tel = (c.telefono if c else "") or ""
        oggetto = f"[Ticket #{t.id}] {t.priorita.value.upper()} — {t.titolo}"
        corpo = (
            f"Nuovo ticket (priorità {t.priorita.value}).\n\n"
            f"Cliente: {cliente}" + (f" — {tel}" if tel else "") + "\n"
            f"Canale: {t.canale or '-'}\n"
            f"Titolo: {t.titolo}\n"
            f"Descrizione: {t.descrizione or '-'}\n"
        )
        if t.storia:
            corpo += f"\nConversazione:\n{t.storia}\n"
        for em in dest:
            email_service.invia_email(destinatario=em, oggetto=oggetto, corpo=corpo)
        logger.info("📧 Ticket #%s inoltrato a %d admin (priorità %s)", t.id, len(dest), t.priorita.value)
    except Exception as e:
        logger.error("Inoltro ticket via email fallito (#%s): %s", ticket_id, e)
    finally:
        db.close()


def inoltra_ticket_async(ticket_id: int) -> None:
    threading.Thread(target=_inoltra_ticket_admin, args=(ticket_id,), daemon=True).start()


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
        inoltra_ticket_async(t.id)  # email agli admin con flag attivo per questa priorità (non blocca)
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
