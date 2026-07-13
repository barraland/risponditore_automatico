import logging
import os
import json
import asyncio
import aiohttp

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    cli,
    mcp,
    room_io,
    utils,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import ai_coustics, openai

logger = logging.getLogger("agent-margherita")

load_dotenv(".env.local")

# Endpoint del backend (Azure). Il prompt + i dati cliente arrivano da /elevenlabs/init; i tool da /mcp.
INIT_URL = os.getenv(
    "INIT_URL",
    "https://horeca-app.ashymushroom-7f7b92f9.westeurope.azurecontainerapps.io/elevenlabs/init",
)
MCP_URL = os.getenv(
    "MCP_URL",
    "https://horeca-app.ashymushroom-7f7b92f9.westeurope.azurecontainerapps.io/mcp",
)
INIT_WEBHOOK_TOKEN = os.getenv("INIT_WEBHOOK_TOKEN", "")


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
        if self._saluto:
            await self.session.generate_reply(
                instructions=f"Apri la conversazione con questo saluto: «{self._saluto}»",
                allow_interruptions=True,
            )
        else:
            await self.session.generate_reply(
                instructions="Saluta brevemente in italiano e chiedi come puoi aiutare.",
                allow_interruptions=True,
            )


server = AgentServer()


@server.rtc_session(agent_name="margherita")
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
