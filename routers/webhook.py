import os
import logging

from fastapi import APIRouter, Request, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse
from fastapi.concurrency import run_in_threadpool

from database import SessionLocal
from services import whatsapp_agent
from services.whatsapp import invia_messaggio

logger = logging.getLogger(__name__)
router = APIRouter()

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


async def _gestisci_messaggio(telefono: str, testo: str):
    """Processa il messaggio con l'agente di lead capture e invia la risposta.

    Sessione DB propria; la chiamata LLM (bloccante) gira nel threadpool.
    """
    db = SessionLocal()
    try:
        out = await run_in_threadpool(whatsapp_agent.gestisci, db, telefono, testo)
        if out.get("risposta"):
            await invia_messaggio(telefono, out["risposta"])
    except Exception as e:
        logger.error("Errore gestione messaggio da %s: %s", telefono, e)
    finally:
        db.close()


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

            for message in messages:
                if message.get("type") != "text":
                    continue

                telefono_raw = message.get("from", "")
                testo = message.get("text", {}).get("body", "")
                wa_message_id = message.get("id")

                if not telefono_raw or not testo:
                    continue

                # Converti in formato E.164
                telefono = f"+{telefono_raw}" if not telefono_raw.startswith("+") else telefono_raw

                logger.info("Messaggio ricevuto da %s: %s", telefono, testo[:100])

                # Processa in background per rispondere subito 200 a Meta
                background_tasks.add_task(_gestisci_messaggio, telefono, testo)

    # Rispondi sempre 200 OK a Meta
    return {"status": "ok"}
