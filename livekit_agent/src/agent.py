import logging
import os
import json
import time
import hmac
import hashlib
import asyncio
import aiohttp

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    JobContext,
    cli,
    inference,
    mcp,
    room_io,
    utils,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import ai_coustics, openai

logger = logging.getLogger("agent-margherita")

load_dotenv(".env.local")

# Endpoint del backend (Azure). init = prompt + dati cliente; mcp = tool; post-call = trascrizione+riassunto.
INIT_URL = os.getenv(
    "INIT_URL",
    "https://horeca-app.ashymushroom-7f7b92f9.westeurope.azurecontainerapps.io/elevenlabs/init",
)
MCP_URL = os.getenv(
    "MCP_URL",
    "https://horeca-app.ashymushroom-7f7b92f9.westeurope.azurecontainerapps.io/mcp",
)
POSTCALL_URL = os.getenv(
    "POSTCALL_URL",
    "https://horeca-app.ashymushroom-7f7b92f9.westeurope.azurecontainerapps.io/elevenlabs/post-call",
)
INIT_WEBHOOK_TOKEN = os.getenv("INIT_WEBHOOK_TOKEN", "")
# Stesso segreto HMAC che il backend usa per verificare il post-call (ELEVENLABS_WEBHOOK_SECRET).
POSTCALL_SECRET = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")


async def _fetch_init_vars(caller: str, called: str, call_id: str) -> dict:
    """Chiama /elevenlabs/init e ritorna le dynamic_variables (configurazione, saluto, tenant, ...)."""
    body = {"caller_id": caller, "called_number": called, "call_sid": call_id}
    headers = {"Content-Type": "application/json"}
    if INIT_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {INIT_WEBHOOK_TOKEN}"
    try:
        http = utils.http_context.http_session()
        resp = await http.post(
            INIT_URL, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        )
        data = await resp.json()
        await resp.release()
        dv = data.get("dynamic_variables", {}) or {}
        logger.info(
            "init webhook OK: caller=%s tenant=%s configurazione=%d char",
            caller or "(vuoto)", dv.get("tenant"), len(dv.get("configurazione", "")),
        )
        return dv
    except Exception as e:
        logger.error("init webhook fallito: %s", e)
        return {}


def _transcript_turns(chat_ctx) -> list:
    """Trascrizione come lista di turni {role, message} nel formato atteso dal backend."""
    turns = []
    for item in getattr(chat_ctx, "items", []):
        if getattr(item, "type", None) == "message" and getattr(item, "role", None) in ("user", "assistant"):
            msg = (item.text_content or "").strip()
            if msg:
                turns.append({"role": "user" if item.role == "user" else "agent", "message": msg})
    return turns


async def _summarize(chat_ctx) -> str:
    """Riassunto breve della chiamata (via LiveKit Inference). Vuoto se fallisce."""
    try:
        turns = _transcript_turns(chat_ctx)
        if not turns:
            return ""
        sctx = ChatContext()
        sctx.add_message(
            role="system",
            content=("Riassumi in italiano questa telefonata a una clinica veterinaria: motivo del "
                     "contatto, dati raccolti (persona/animale), esito e eventuale follow-up. Max 4 frasi."),
        )
        for t in turns:
            sctx.add_message(role="user", content=f"{t['role']}: {t['message']}")
        r = await inference.LLM(model="openai/gpt-5.2-chat-latest").chat(chat_ctx=sctx).collect()
        return (r.text or "").strip()
    except Exception as e:
        logger.warning("summary fallito: %s", e)
        return ""


async def _on_session_end(ctx: JobContext) -> None:
    """A fine chiamata invia trascrizione + riassunto al backend (/elevenlabs/post-call), firmato HMAC.
    Non bloccante: qualsiasi errore viene solo loggato."""
    try:
        report = ctx.make_session_report()
        chat = report.chat_history
        turns = _transcript_turns(chat)
        if not turns:
            logger.info("post-call: nessuna trascrizione, salto")
            return
        summary = await _summarize(chat)
        caller = ""
        try:
            caller = ctx.proc.userdata.get("caller", "") or ""
        except Exception:
            pass
        started = getattr(report, "started_at", None)
        duration = int(time.time() - started) if started else None

        body = {
            "type": "post_call_transcription",
            "data": {
                "transcript": turns,
                "analysis": {"transcript_summary": summary},
                "metadata": {
                    "phone_number": caller,
                    "call_duration_secs": duration,
                    "start_time_unix_secs": int(started) if started else None,
                },
                "conversation_initiation_client_data": {
                    "dynamic_variables": {"telefono_chiamante": caller},
                },
            },
        }
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if POSTCALL_SECRET:
            ts = str(int(time.time()))
            mac = hmac.new(POSTCALL_SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
            headers["elevenlabs-signature"] = f"t={ts},v0={mac}"
        http = utils.http_context.http_session()
        resp = await http.post(POSTCALL_URL, data=raw, headers=headers, timeout=aiohttp.ClientTimeout(total=20))
        logger.info("post-call HTTP %s (turni=%d, summary=%d char)", resp.status, len(turns), len(summary))
        await resp.release()
    except Exception as e:
        logger.warning("post-call fallito (non bloccante): %s", e)


class MargheritaAgent(Agent):
    def __init__(self, configurazione: str, saluto: str) -> None:
        super().__init__(
            instructions=configurazione or "Sei l'assistente vocale della clinica veterinaria. Rispondi in italiano.",
            tools=[EndCallTool(delete_room=False)],
            mcp_servers=[
                mcp.MCPServerHTTP(url=MCP_URL, client_session_timeout_seconds=30),
            ],
        )
        self._saluto = saluto

    async def on_enter(self):
        # NB: con GPT-Realtime `session.say` NON produce audio (servirebbe un TTS separato, con voce
        # diversa) → usiamo generate_reply. Istruzione SEMPLICE e senza contraddizioni: recita la frase
        # di benvenuto ALLA LETTERA e poi fermati (il saluto può già contenere una domanda tipo
        # "come posso aiutarla?", quindi NON vietare le domande, altrimenti il modello va in stallo).
        if self._saluto:
            await self.session.generate_reply(
                instructions=(
                    "Inizia la conversazione pronunciando ESATTAMENTE questa frase di benvenuto, "
                    "parola per parola, senza aggiungere nulla prima o dopo. Poi fermati e ascolta "
                    f"la risposta del cliente:\n«{self._saluto}»"
                ),
                allow_interruptions=True,
            )
        else:
            await self.session.generate_reply(
                instructions="Saluta brevemente in italiano e chiedi come puoi aiutare.",
                allow_interruptions=True,
            )


server = AgentServer()


@server.rtc_session(agent_name="margherita", on_session_end=_on_session_end)
async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    # Connetti PRIMA, così puoi leggere il partecipante SIP (il chiamante) e caricare il prompt giusto.
    await ctx.connect()

    caller = called = call_id = ""
    try:
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=10)
        attrs = dict(participant.attributes or {})
        logger.info("SIP participant attributes: %s", attrs)
        caller = attrs.get("sip.phoneNumber") or attrs.get("sip.from") or ""
        called = (attrs.get("sip.trunkPhoneNumber") or attrs.get("sip.to")
                  or attrs.get("sip.calledNumber") or "")
        call_id = attrs.get("sip.callID") or attrs.get("sip.callId") or ""
    except Exception as e:
        logger.warning("attributi SIP non letti: %s", e)

    # Salva il numero per il post-call di fine chiamata.
    try:
        ctx.proc.userdata["caller"] = caller
    except Exception:
        pass

    init_vars = await _fetch_init_vars(caller, called, call_id)
    configurazione = init_vars.get("configurazione", "")
    saluto = init_vars.get("saluto", "")
    logger.info("PROMPT: configurazione=%d char | saluto=%r", len(configurazione), saluto)

    # GPT-Realtime full-duplex: niente STT separato → capisce l'italiano direttamente.
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(model="gpt-realtime-2", voice="marin"),
    )

    await session.start(
        agent=MargheritaAgent(configurazione, saluto),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_S
                ),
            ),
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
