"""Invio email con allegati via SMTP Gmail.

Utility di trasporto riusabile (come services/whatsapp.py): l'agente WhatsApp la
chiama per inviare un documento del condominio al condomino. La useranno anche
l'agente mail e quello voce quando serviranno.

Config nel .env:
  GMAIL_FROM=tuo@gmail.com
  GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"   # app password Google (non la password normale)
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)

GMAIL_FROM = os.getenv("GMAIL_FROM", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")


def _subtype(percorso: str) -> tuple[str, str]:
    ext = os.path.splitext(percorso)[1].lower()
    if ext == ".pdf":
        return "application", "pdf"
    if ext in (".doc", ".docx"):
        return "application", "vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext in (".txt", ".md"):
        return "text", "plain"
    return "application", "octet-stream"


def invia_email(destinatario: str, oggetto: str, corpo: str, allegati: list[str] | None = None) -> bool:
    """Invia un'email. Ritorna True se inviata, False altrimenti (non solleva)."""
    if not GMAIL_FROM or not GMAIL_APP_PASSWORD:
        logger.warning("Gmail non configurato: GMAIL_FROM o GMAIL_APP_PASSWORD mancante")
        return False

    msg = EmailMessage()
    msg["From"] = GMAIL_FROM
    msg["To"] = destinatario
    msg["Subject"] = oggetto
    msg.set_content(corpo)

    for percorso in (allegati or []):
        try:
            with open(percorso, "rb") as f:
                data = f.read()
            maintype, subtype = _subtype(percorso)
            msg.add_attachment(data, maintype=maintype, subtype=subtype,
                               filename=os.path.basename(percorso))
        except OSError as e:
            logger.error("Allegato non leggibile %s: %s", percorso, e)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as server:
            # Le app password Google si possono incollare con o senza spazi.
            server.login(GMAIL_FROM, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.send_message(msg)
        logger.info("Email inviata a %s (oggetto: %s)", destinatario, oggetto)
        return True
    except Exception as e:
        logger.error("Invio email a %s fallito: %s", destinatario, e)
        return False
