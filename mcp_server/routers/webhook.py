import os
import logging

from fastapi import APIRouter, Request, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse
from fastapi.concurrency import run_in_threadpool

from database import SessionLocal
from services import whatsapp_agent
from services.whatsapp import invia_messaggio, trascrivi_audio

logger = logging.getLogger(__name__)
# Prefisso /whatsapp: l'endpoint pubblico diventa /whatsapp/webhook (più chiaro di /webhook).
router = APIRouter(prefix="/whatsapp")

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "mio_token_segreto_per_webhook")


@router.get("/webhook")
async def verify_webhook(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verifica del webhook Meta (subscription verification)."""
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verificato con successo")
        return PlainTextResponse(content=hub_challenge)

    logger.warning("Verifica webhook fallita: token non valido")
    return PlainTextResponse(content="Forbidden", status_code=403)


async def _gestisci_messaggio(telefono: str, testo: str, phone_number_id: str = "",
                              display_phone_number: str = ""):
    """Processa il messaggio con l'agente di lead capture e invia la risposta.

    `phone_number_id`/`display_phone_number`: il numero business che ha ricevuto il messaggio
    (dal metadata Meta) → identifica il TENANT. Sessione DB propria; la chiamata LLM (bloccante)
    gira nel threadpool.
    """
    db = SessionLocal()
    try:
        out = await run_in_threadpool(whatsapp_agent.gestisci, db, telefono, testo,
                                      phone_number_id, display_phone_number)
        if out.get("risposta"):
            await invia_messaggio(telefono, out["risposta"])
    except Exception as e:
        logger.error("Errore gestione messaggio da %s: %s", telefono, e)
    finally:
        db.close()


async def _gestisci_audio(telefono: str, media_id: str, phone_number_id: str = "",
                          display_phone_number: str = ""):
    """Trascrive un messaggio vocale WhatsApp con Whisper, poi lo processa come un testo normale
    (stesso canale/agente). Se la trascrizione fallisce, chiede al cliente di riprovare."""
    testo = await run_in_threadpool(trascrivi_audio, media_id)
    if not testo:
        await invia_messaggio(telefono, "Scusi, non sono riuscito a capire il messaggio vocale. "
                                        "Può riprovare o scrivermelo?")
        return
    logger.info("Audio da %s trascritto: %s", telefono, testo[:100])
    await _gestisci_messaggio(telefono, testo, phone_number_id, display_phone_number)


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Riceve messaggi in arrivo da Meta WhatsApp Cloud API."""
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    # Estrai messaggi dal payload Meta
    entries = body.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            # Il numero business che ha RICEVUTO il messaggio → identifica il tenant.
            meta = value.get("metadata", {}) or {}
            phone_number_id = meta.get("phone_number_id", "") or ""
            display_phone_number = meta.get("display_phone_number", "") or ""

            for message in messages:
                tipo = message.get("type")
                telefono_raw = message.get("from", "")
                if not telefono_raw:
                    continue
                # Converti in formato E.164
                telefono = f"+{telefono_raw}" if not telefono_raw.startswith("+") else telefono_raw

                if tipo == "text":
                    testo = message.get("text", {}).get("body", "")
                    if not testo:
                        continue
                    logger.info("Messaggio da %s (verso pid=%s / %s): %s",
                                telefono, phone_number_id, display_phone_number, testo[:100])
                    background_tasks.add_task(_gestisci_messaggio, telefono, testo,
                                              phone_number_id, display_phone_number)
                elif tipo == "audio":
                    # Messaggio vocale/audio: trascrivi con Whisper, poi stesso flusso del testo.
                    media_id = (message.get("audio") or {}).get("id")
                    if not media_id:
                        continue
                    logger.info("Audio WhatsApp da %s (media %s) → trascrizione", telefono, media_id)
                    background_tasks.add_task(_gestisci_audio, telefono, media_id,
                                              phone_number_id, display_phone_number)
                # altri tipi (immagini, documenti, ecc.) per ora ignorati

    # Rispondi sempre 200 OK a Meta
    return {"status": "ok"}
