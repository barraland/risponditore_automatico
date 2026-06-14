import os
import logging

import httpx

logger = logging.getLogger(__name__)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
GRAPH_API_URL = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"


async def invia_messaggio(telefono: str, testo: str) -> dict | None:
    """Invia un messaggio di testo via WhatsApp Cloud API.

    Args:
        telefono: numero destinatario in formato E.164 (es. +393331234567)
        testo: testo del messaggio
    Returns:
        dict con la risposta dell'API o None in caso di errore
    """
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning("WhatsApp non configurato: WHATSAPP_TOKEN o PHONE_NUMBER_ID mancante")
        return None

    # Rimuovi il '+' iniziale se presente (Meta vuole solo cifre)
    numero = telefono.lstrip("+")

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"body": testo},
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(GRAPH_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            logger.info("Messaggio WhatsApp inviato a %s: %s", telefono, data)
            return data
    except httpx.HTTPStatusError as e:
        logger.error("Errore API WhatsApp %s: %s", e.response.status_code, e.response.text)
        return None
    except Exception as e:
        logger.error("Errore invio WhatsApp: %s", e)
        return None
