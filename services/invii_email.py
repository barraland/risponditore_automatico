"""Storico dei documenti inviati via email a un inquilino.

Serve a non rioffrire un documento già spedito e a citarlo ("lo trova nel
documento X che le ho già inviato"). Usato sia dall'agente WhatsApp sia dalla voce.
"""

import logging

from sqlalchemy.orm import Session

from database import InvioDocumentoEmail

logger = logging.getLogger(__name__)


def registra_invio(db: Session, inquilino_id: int, documento_id: int, email: str | None = None) -> None:
    """Registra che un documento è stato inviato via email a un inquilino."""
    try:
        db.add(InvioDocumentoEmail(inquilino_id=inquilino_id, documento_id=documento_id, email=email))
        db.commit()
    except Exception as e:
        logger.error("Registrazione invio documento fallita: %s", e)
        db.rollback()


def gia_inviato(db: Session, inquilino_id: int, documento_id: int) -> bool:
    """True se quel documento è già stato inviato via email a quell'inquilino."""
    if not inquilino_id or not documento_id:
        return False
    return (
        db.query(InvioDocumentoEmail)
        .filter(
            InvioDocumentoEmail.inquilino_id == inquilino_id,
            InvioDocumentoEmail.documento_id == documento_id,
        )
        .first()
        is not None
    )
