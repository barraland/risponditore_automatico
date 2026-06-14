"""Chiamate vocali (Twilio Media Streams <-> OpenAI Realtime) per il condominio.

Speech-to-speech nativo: l'audio del chiamante va al modello Realtime, che risponde
in audio. Quando il condomino chiede un'informazione che sta nei documenti, il modello
chiama il TOOL `cerca_nei_documenti`, che invoca l'agente RISPONDITORE (lo stesso usato
da WhatsApp/mail). Il risponditore resta puro: qui c'è solo il trasporto vocale.

Flusso:
  1. Twilio -> POST /voice/incoming -> TwiML che apre un Media Stream verso /voice/stream.
  2. /voice/stream fa da ponte audio bidirezionale Twilio <-> OpenAI Realtime.
  3. function_call `cerca_nei_documenti(domanda)` -> agente.rispondi(...) in background
     (la ricerca dura qualche secondo: NON deve bloccare l'audio) -> risultato verbalizzato.

Naturalezza dell'attesa: il modello avvisa a voce che sta controllando prima di chiamare
il tool, e — poiché la ricerca gira in un task separato — può continuare a rassicurare il
chiamante impaziente mentre la risposta viene preparata.
"""

import os
import json
import asyncio
import logging
from datetime import datetime

import websockets
from fastapi import APIRouter, WebSocket, Request
from fastapi.responses import PlainTextResponse
from fastapi.websockets import WebSocketState

from database import SessionLocal, Inquilino, Condominio, Documento, StatoDocumento
from services import agente
from services import whatsapp_agent
from services import voice_log
from services import email as email_service
from services import invii_email
from services import ticket as ticket_service
from services.contesto import contesto_temporale

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "alloy")
OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"


# ---------- Tool Realtime: cerca nei documenti ----------

REALTIME_TOOLS = [
    {
        "type": "function",
        "name": "cerca_nei_documenti",
        "description": (
            "Cerca la risposta a una domanda del condomino nei documenti del suo condominio "
            "(bilanci, riparti, consumi, verbali, regolamento, avvisi, polizza, ecc.). "
            "Usa SEMPRE questo strumento quando il condomino chiede un'informazione che può stare "
            "nei documenti. L'operazione richiede qualche secondo: PRIMA di chiamarla avvisa a voce "
            "il chiamante che stai controllando."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domanda": {
                    "type": "string",
                    "description": (
                        "La domanda completa e autosufficiente da cercare. Se il chiamante usa la prima "
                        "persona ('quanto ho consumato', 'la mia rata'), includi il suo NOME e UNITÀ "
                        "(es. 'Quanto ha consumato Mario Rossi, unità Scala A interno 1, nel 2025?')."
                    ),
                }
            },
            "required": ["domanda"],
        },
    },
    {
        "type": "function",
        "name": "invia_documento_via_email",
        "description": (
            "Invia al condomino, via email con allegato, il documento di cui avete appena parlato "
            "(l'ultimo trovato con cerca_nei_documenti). Usa questo strumento quando il condomino "
            "chiede di ricevere il documento per email. Se conosci già la sua email la usi; altrimenti "
            "CHIEDIGLI a voce l'indirizzo e passalo nel parametro 'email'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": (
                        "Indirizzo email del destinatario, se il condomino lo detta a voce. "
                        "Ometti se va usato l'indirizzo già presente in archivio."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "apri_ticket",
        "description": (
            "Apre una SEGNALAZIONE per l'amministratore. Usalo quando: (a) non riesci a "
            "rispondere perché il dato non è nei documenti; (b) il condomino non è convinto "
            "della risposta, si lamenta, contesta o insiste; (c) segnala un problema/guasto. "
            "Dopo averlo aperto, di' al condomino che hai registrato la segnalazione e che "
            "l'amministratore lo ricontatterà."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "titolo": {"type": "string", "description": "Titolo breve della segnalazione (max ~10 parole)."},
                "descrizione": {"type": "string", "description": "Descrizione del problema o della richiesta, con i dettagli utili."},
            },
            "required": ["titolo", "descrizione"],
        },
    },
]


# ---------- Istruzioni vocali ----------

def _build_voice_instructions(db, inquilino, condominio) -> str:
    studio = None
    try:
        from database import Studio
        studio = db.query(Studio).first()
    except Exception:
        pass
    nome_studio = (studio.nome if studio else None) or "lo studio di amministrazione"

    if not inquilino:
        return (
            f'Sei l\'assistente telefonico di "{nome_studio}", che amministra condomìni. '
            "Parli in italiano, al telefono, con frasi brevi e cordiali (dai del lei).\n"
            "ATTENZIONE: il numero del chiamante NON risulta tra i condòmini registrati. "
            "Saluta con gentilezza, spiega che non puoi fornire informazioni perché il numero non è "
            "registrato, e invita a contattare l'amministratore. Non usare strumenti."
        )

    n_ready = sum(1 for d in (condominio.documenti if condominio else []) if d.stato == StatoDocumento.READY)
    nome = f"{inquilino.nome} {inquilino.cognome or ''}".strip()
    unita = inquilino.unita or "non specificata"
    # Appellativo per il saluto: cognome se c'è, altrimenti nome.
    appellativo = (inquilino.cognome or inquilino.nome or "").strip()

    return (
        f'Sei l\'assistente telefonico di "{nome_studio}", che amministra condomìni. '
        f"Stai parlando con {nome}, del condominio «{condominio.nome}», unità {unita}.\n"
        f"Documenti disponibili per questo condominio: {n_ready}.\n"
        f"{contesto_temporale()}\n\n"
        "COME PARLARE:\n"
        "- Sei al TELEFONO: frasi BREVI e naturali, una cosa alla volta, in italiano. Dai del lei, tono cordiale.\n"
        f"- APERTURA: la PRIMA cosa che dici, salutalo per cognome, es. «Buongiorno signor {appellativo}». "
        "Poi presentati come l'assistente del condominio e chiedi come puoi aiutarlo. "
        "Usa il suo nome anche più avanti, quando è naturale.\n"
        "- Leggi numeri, date e importi in modo naturale (es. 'cinquecento euro', 'il tre marzo').\n\n"
        "COME RISPONDERE ALLE DOMANDE:\n"
        "- Per qualsiasi informazione che può stare nei documenti (rate, spese, riparti, consumi, "
        "verbali, regolamento, scadenze, fornitori...) usa lo strumento cerca_nei_documenti.\n"
        "- IMPORTANTE: la ricerca richiede qualche secondo. PRIMA di chiamare lo strumento, di' a voce "
        "una frase breve tipo 'Un attimo, controllo nei documenti…'. Se il chiamante si spazientisce "
        "mentre aspetti, rassicuralo ('ci sono, sto ancora controllando, un secondo').\n"
        "- Lo strumento ti restituisce una risposta dettagliata con le fonti: NON leggerla parola per "
        "parola. Riassumila al telefono in modo breve e chiaro, e se utile cita la fonte "
        "('lo trovo nel bilancio 2025'). \n"
        "- Se lo strumento non trova la risposta, dillo con onestà, APRI una segnalazione con "
        "apri_ticket (così l'amministratore se ne occupa) e di' al condomino che lo ricontatteranno. "
        "Non inventare mai dati.\n"
        "- SEGNALAZIONI (apri_ticket): aprine una anche quando il condomino NON è convinto della "
        "risposta, si lamenta, contesta o segnala un problema/guasto. Conferma a voce che hai "
        "registrato la segnalazione e che l'amministratore lo ricontatterà.\n"
        "- Se la domanda è in prima persona ('quanto ho speso io'), quando chiami lo strumento includi "
        f"nel testo il nome e l'unità del chiamante ({nome}, unità {unita}).\n\n"
        "NON LASCIARE SILENZI — chiudi sempre il turno (è sgradevole se taci):\n"
        "- Dopo una risposta presa dai documenti, VERIFICA che sia utile: «È questa l'informazione che le "
        "serviva?». Se dice di no o non è convinto, approfondisci oppure apri una segnalazione (apri_ticket).\n"
        "- Quando la richiesta sembra soddisfatta o il chiamante ringrazia, chiudi con cortesia: «C'è altro "
        "con cui posso esserle utile?». Se risponde di no, salutalo per nome e concludi la chiamata.\n"
        "- Usa con buon senso: la verifica DOPO una risposta informativa, il «c'è altro?» per CHIUDERE. "
        "Non ripeterle a ogni frase, ma non restare mai muto dopo aver parlato.\n\n"
        "INVIO DEI DOCUMENTI VIA EMAIL:\n"
        "- Quando hai risposto usando un documento (lo strumento cerca_nei_documenti restituisce il campo "
        "'documento'), PROPONI tu, senza aspettare che lo chieda: «Vuole che le invii [nome documento] "
        "via email?».\n"
        "- ECCEZIONE: se lo strumento restituisce 'gia_inviato_via_email' = true, NON proporre di inviarlo di "
        "nuovo. Di' invece che il dato si trova nel documento «[nome documento]» che gli hai GIÀ inviato via "
        "email in precedenza.\n"
        "- Per inviare chiama SUBITO invia_documento_via_email SENZA chiedere a quale indirizzo: di default "
        "va all'email registrata del condomino. Quando lo strumento conferma l'invio, DI' a voce l'indirizzo "
        "a cui l'hai mandato (es. «Gliel'ho inviato a mario.rossi@email.it»), utile se non ricorda quale "
        "email ha registrato.\n"
        "- SOLO se lo strumento risponde che serve l'indirizzo (nessuna email registrata), allora chiedilo "
        "a voce, fattelo scandire se non sei sicuro, e richiama lo strumento.\n"
        "- Non dire mai che non puoi inviare email."
    )


# ---------- 1) Webhook Twilio ----------

@router.post("/incoming")
async def incoming_call(request: Request):
    """Risponde con TwiML che apre lo stream audio bidirezionale."""
    form = await request.form()
    caller = form.get("From", "")
    host = request.headers.get("host", request.url.hostname)
    ws_url = f"wss://{host}/voice/stream"
    logger.info("Chiamata in arrivo da %s -> stream %s", caller, ws_url)

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f'<Stream url="{ws_url}">'
        f'<Parameter name="from" value="{caller}"/>'
        "</Stream>"
        "</Connect>"
        "</Response>"
    )
    return PlainTextResponse(content=twiml, media_type="text/xml")


# ---------- 2) WebSocket: ponte Twilio <-> OpenAI Realtime ----------

@router.websocket("/stream")
async def media_stream(twilio_ws: WebSocket):
    await twilio_ws.accept()
    logger.info("WS Twilio connesso")

    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY mancante: impossibile gestire la chiamata")
        await twilio_ws.close()
        return

    db = SessionLocal()
    stato = {
        "stream_sid": None,
        "telefono": None,
        "condominio_id": None,
        "inquilino_id": None,
        "iniziata_at": None,
        "response_active": False,   # c'è una risposta del modello in corso?
        "speak_pending": False,     # un risultato di tool attende di essere verbalizzato
        "tasks": set(),             # task di ricerca in background
        "trascrizione": [],         # [{ruolo, testo, item}, ...] per il log chiamata
        "ordine_item": {},          # item_id -> sequenza di creazione (per ordinare la trascrizione)
        "seq_item": 0,
        "assist_buf": "",           # accumulo del transcript dell'assistente nel turno corrente
        "ultimo_doc_id": None,      # documento-fonte dell'ultima risposta (per l'invio email)
        "ultimo_doc_nome": None,
    }

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    try:
        try:
            openai_ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers)
        except TypeError:
            openai_ws = await websockets.connect(OPENAI_WS_URL, extra_headers=headers)
    except Exception as e:
        logger.error("Connessione a OpenAI Realtime fallita: %s", e)
        db.close()
        await twilio_ws.close()
        return

    async def configura_sessione():
        """Identifica il chiamante, imposta audio/voce/VAD/tool/istruzioni, fa salutare."""
        inquilino = whatsapp_agent.trova_inquilino(db, stato["telefono"] or "")
        condominio = inquilino.condominio if inquilino else None
        stato["condominio_id"] = inquilino.condominio_id if inquilino else None
        stato["inquilino_id"] = inquilino.id if inquilino else None
        if inquilino:
            logger.info("Chiamante riconosciuto: %s (cond %s)", inquilino.nome, stato["condominio_id"])
        else:
            logger.info("Chiamante NON registrato: %s", stato["telefono"])

        await openai_ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": _build_voice_instructions(db, inquilino, condominio),
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcmu"},
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 500,
                        },
                        "transcription": {"model": "whisper-1"},
                    },
                    "output": {
                        "format": {"type": "audio/pcmu"},
                        "voice": REALTIME_VOICE,
                    },
                },
                "tools": REALTIME_TOOLS,
                "tool_choice": "auto",
            },
        }))
        await openai_ws.send(json.dumps({"type": "response.create"}))

    async def richiedi_risposta():
        """Chiede al modello di generare una risposta, rispettando la concorrenza:
        se ce n'è già una attiva, rimanda finché non finisce (response.done)."""
        if stato["response_active"]:
            stato["speak_pending"] = True
        else:
            stato["response_active"] = True
            await openai_ws.send(json.dumps({"type": "response.create"}))

    def _cerca(domanda: str) -> dict:
        """Ricerca documentale (sincrona, gira in thread). Memorizza la fonte primaria."""
        domanda = (domanda or "").strip()
        cond_id = stato["condominio_id"]
        if not cond_id:
            return {"errore": "Chiamante non registrato: nessun condominio associato."}
        if not domanda:
            return {"errore": "Domanda mancante."}
        try:
            esito = agente.rispondi(db, cond_id, domanda)
        except Exception as e:
            logger.error("  errore ricerca documentale: %s", e)
            return {"errore": str(e)}
        # Log accessi del risponditore nella trascrizione (subito prima della risposta).
        stato["trascrizione"].append({
            "ruolo": "Risponditore", "ord": stato["seq_item"] - 0.5,
            "testo": f"[ricerca: {domanda}]\n{agente.formatta_accessi(esito)}",
        })
        fonti_passi = [p for p in esito.get("passi", []) if p.get("trovato") and p.get("documento_id")]
        doc_nome = None
        gia_inviato = False
        if fonti_passi:
            stato["ultimo_doc_id"] = fonti_passi[0]["documento_id"]
            stato["ultimo_doc_nome"] = doc_nome = fonti_passi[0]["documento"]
            gia_inviato = invii_email.gia_inviato(db, stato.get("inquilino_id"), fonti_passi[0]["documento_id"])
        fonti = sorted({f"{p['documento']} pp.{p['pagine']}" for p in fonti_passi})
        # 'documento' valorizzato => c'è un documento-fonte che puoi proporre di inviare via email.
        # 'gia_inviato_via_email' True => NON rioffrirlo, citalo come già spedito.
        return {"risposta": esito.get("risposta", ""), "fonti": fonti,
                "documento": doc_nome, "gia_inviato_via_email": gia_inviato}

    def _invia_documento(email_arg: str | None) -> dict:
        """Invia via email l'ultimo documento citato (sincrona, gira in thread)."""
        doc_id = stato.get("ultimo_doc_id")
        if not doc_id:
            return {"errore": "Non c'è un documento da inviare: prima cerca l'informazione nei documenti."}
        doc = db.get(Documento, doc_id)
        if not doc:
            return {"errore": "Il documento non è più disponibile."}
        inq = db.get(Inquilino, stato["inquilino_id"]) if stato.get("inquilino_id") else None

        email = whatsapp_agent._estrai_email(email_arg or "")
        if not email and inq and inq.email:
            email = inq.email
        if not email:
            return {"serve_email": True}  # il modello chiederà l'indirizzo a voce

        if inq and email and inq.email != email:
            inq.email = email  # salva/aggiorna in anagrafica
            db.commit()

        ok = email_service.invia_email(
            destinatario=email,
            oggetto=f"Documento del condominio: {doc.nome_file}",
            corpo=(f"Gentile {inq.nome if inq else ''},\n\n"
                   f"in allegato il documento richiesto: {doc.nome_file}.\n\n"
                   f"Cordiali saluti,\nL'assistente del condominio"),
            allegati=[doc.percorso],
        )
        if ok and inq:
            invii_email.registra_invio(db, inq.id, doc.id, email)
        logger.info("  voce: invio email %s -> %s (%s)", doc.nome_file, email, "ok" if ok else "FALLITO")
        return {"inviato": bool(ok), "email": email, "documento": doc.nome_file}

    def _trascrizione_ordinata() -> list:
        """Trascrizione riordinata per ordine di creazione degli item (cronologico),
        non per ordine di arrivo degli eventi: la trascrizione del chiamante (Whisper)
        può arrivare in ritardo rispetto alle risposte dell'assistente. Le righe con
        'ord' esplicito (es. log del risponditore) usano quello."""
        ordine = stato["ordine_item"]
        def _key(d):
            return d["ord"] if "ord" in d else ordine.get(d.get("item"), 10**9)
        return sorted(stato["trascrizione"], key=_key)

    def _apri_ticket(titolo: str, descrizione: str) -> dict:
        """Apre un ticket usando la trascrizione corrente come storia (sincrona)."""
        storia = ticket_service.formatta_storia(_trascrizione_ordinata())
        t = ticket_service.apri_ticket(
            db,
            condominio_id=stato.get("condominio_id"),
            inquilino_id=stato.get("inquilino_id"),
            titolo=titolo or "Segnalazione telefonica",
            descrizione=descrizione or "",
            storia=storia,
            canale="voce",
        )
        if not t:
            return {"errore": "Non sono riuscito a registrare la segnalazione."}
        return {"aperto": True, "ticket_id": t.id}

    async def esegui_tool(name: str, call_id: str, arguments: str):
        """Esegue il tool richiesto in background e ne verbalizza il risultato."""
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            args = {}
        logger.info("  voce tool: %s(%s)", name, args)

        if name == "cerca_nei_documenti":
            result = await asyncio.to_thread(_cerca, args.get("domanda", ""))
        elif name == "invia_documento_via_email":
            result = await asyncio.to_thread(_invia_documento, args.get("email"))
        elif name == "apri_ticket":
            result = await asyncio.to_thread(_apri_ticket, args.get("titolo", ""), args.get("descrizione", ""))
        else:
            result = {"errore": f"Strumento sconosciuto: {name}"}

        try:
            await openai_ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                },
            }))
            await richiedi_risposta()
        except Exception as e:
            logger.info("Invio risultato tool fallito (ws chiuso?): %s", e)

    def avvia_tool(name: str, call_id: str, arguments: str):
        """Lancia il tool come task così da NON bloccare il loop audio."""
        task = asyncio.create_task(esegui_tool(name, call_id, arguments))
        stato["tasks"].add(task)
        task.add_done_callback(stato["tasks"].discard)

    # ----- Twilio -> OpenAI -----
    async def da_twilio():
        try:
            async for raw in twilio_ws.iter_text():
                data = json.loads(raw)
                event = data.get("event")
                if event == "start":
                    stato["stream_sid"] = data["start"]["streamSid"]
                    params = data["start"].get("customParameters", {})
                    stato["telefono"] = params.get("from") or "sconosciuto"
                    stato["iniziata_at"] = datetime.utcnow()
                    logger.info("Stream avviato (sid=%s, da=%s)", stato["stream_sid"], stato["telefono"])
                    await configura_sessione()
                elif event == "media":
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": data["media"]["payload"],
                    }))
                elif event == "stop":
                    logger.info("Stream Twilio terminato")
                    break
        except Exception as e:
            logger.info("Loop Twilio chiuso: %s", e)
        finally:
            try:
                await openai_ws.close()
            except Exception:
                pass

    # ----- OpenAI -> Twilio -----
    async def da_openai():
        try:
            async for raw in openai_ws:
                evt = json.loads(raw)
                etype = evt.get("type")

                if etype == "response.output_audio.delta" and stato["stream_sid"]:
                    await twilio_ws.send_text(json.dumps({
                        "event": "media",
                        "streamSid": stato["stream_sid"],
                        "media": {"payload": evt["delta"]},
                    }))

                elif etype == "response.created":
                    stato["response_active"] = True

                elif etype == "response.done":
                    stato["response_active"] = False
                    # Se un risultato di tool era in attesa di essere detto, verbalizzalo ora.
                    if stato["speak_pending"]:
                        stato["speak_pending"] = False
                        stato["response_active"] = True
                        await openai_ws.send(json.dumps({"type": "response.create"}))

                elif etype == "conversation.item.created":
                    # Registra l'ordine cronologico di creazione degli item della conversazione.
                    item_id = (evt.get("item") or {}).get("id")
                    if item_id and item_id not in stato["ordine_item"]:
                        stato["ordine_item"][item_id] = stato["seq_item"]
                        stato["seq_item"] += 1

                elif etype == "response.output_audio_transcript.delta":
                    stato["assist_buf"] += evt.get("delta", "")

                elif etype == "response.output_audio_transcript.done":
                    testo = (evt.get("transcript") or stato["assist_buf"]).strip()
                    stato["assist_buf"] = ""
                    if testo:
                        stato["trascrizione"].append(
                            {"ruolo": "Assistente", "testo": testo, "item": evt.get("item_id")})

                elif etype == "input_audio_buffer.speech_started":
                    # Barge-in: il chiamante riprende a parlare -> svuota l'audio in coda e
                    # annulla la risposta in corso.
                    if stato["stream_sid"]:
                        await twilio_ws.send_text(json.dumps({
                            "event": "clear", "streamSid": stato["stream_sid"],
                        }))
                    if stato["response_active"]:
                        await openai_ws.send(json.dumps({"type": "response.cancel"}))

                elif etype == "response.function_call_arguments.done":
                    # Esegui il tool SENZA bloccare questo loop (audio + barge-in restano vivi).
                    avvia_tool(evt.get("name"), evt.get("call_id"), evt.get("arguments", "{}"))

                elif etype == "conversation.item.input_audio_transcription.completed":
                    trascr = evt.get("transcript", "").strip()
                    logger.info("  chiamante: %s", trascr)
                    if trascr:
                        stato["trascrizione"].append(
                            {"ruolo": "Condomino", "testo": trascr, "item": evt.get("item_id")})

                elif etype == "error":
                    logger.error("  OpenAI error: %s", evt.get("error"))
        except Exception as e:
            logger.info("Loop OpenAI chiuso: %s", e)

    try:
        await asyncio.gather(da_twilio(), da_openai())
    finally:
        for t in list(stato["tasks"]):
            t.cancel()
        # Salva il log della chiamata (trascrizione + riassunto) se il chiamante è
        # registrato e c'è stato dialogo. Il riassunto è bloccante (LLM): in thread.
        if stato["inquilino_id"] and stato["trascrizione"]:
            durata = None
            if stato["iniziata_at"]:
                durata = int((datetime.utcnow() - stato["iniziata_at"]).total_seconds())
            try:
                await asyncio.to_thread(
                    voice_log.salva_chiamata, db, stato["inquilino_id"], stato["telefono"],
                    _trascrizione_ordinata(), stato["iniziata_at"], durata,
                )
            except Exception as e:
                logger.error("Salvataggio log chiamata fallito: %s", e)
        db.close()
        if twilio_ws.client_state != WebSocketState.DISCONNECTED:
            await twilio_ws.close()
        logger.info("Chiamata terminata, risorse liberate")
