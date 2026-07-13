import io
import os
import logging

import httpx

logger = logging.getLogger(__name__)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
GRAPH_BASE = "https://graph.facebook.com/v21.0"
GRAPH_API_URL = f"{GRAPH_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
WHISPER_MODEL = os.getenv("WHATSAPP_WHISPER_MODEL", "whisper-1")


def trascrivi_audio(media_id: str) -> str:
    """Scarica un messaggio vocale/audio WhatsApp (per media_id) e lo trascrive con Whisper.
    Ritorna il testo trascritto (o stringa vuota se non configurato o in errore). Sincrona:
    chiamala in threadpool. I vocali WhatsApp sono OGG/Opus, formato accettato da Whisper."""
    if not (WHATSAPP_TOKEN and OPENAI_API_KEY and media_id):
        return ""
    try:
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        # 1) media_id -> URL temporaneo del file
        info = httpx.get(f"{GRAPH_BASE}/{media_id}", headers=headers, timeout=30).json()
        url = info.get("url")
        if not url:
            logger.warning("Media WhatsApp %s: URL non trovato (%s)", media_id, info)
            return ""
        # 2) scarica i byte (serve lo STESSO bearer)
        audio = httpx.get(url, headers=headers, timeout=30).content
        mime = (info.get("mime_type") or "audio/ogg").split(";")[0]
        ext = mime.split("/")[-1] or "ogg"
        buf = io.BytesIO(audio)
        buf.name = f"audio.{ext}"   # l'estensione serve a Whisper per riconoscere il formato
        # 3) trascrizione Whisper
        from openai import OpenAI
        cli = OpenAI(api_key=OPENAI_API_KEY)
        tr = cli.audio.transcriptions.create(model=WHISPER_MODEL, file=buf, language="it")
        testo = (getattr(tr, "text", "") or "").strip()
        logger.info("🎙️ Audio WhatsApp trascritto (%d byte, %s): %s", len(audio), mime, testo[:80])
        return testo
    except Exception as e:
        logger.error("Trascrizione audio WhatsApp fallita (media %s): %s", media_id, e)
        return ""


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
