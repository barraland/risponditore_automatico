"""Chiamate vocali (Twilio Media Streams <-> OpenAI Realtime) per la lead capture.

Speech-to-speech nativo: l'audio del chiamante va al modello Realtime, che risponde
in audio. L'assistente risponde alle domande su prodotti/servizi usando il profilo
aziendale (testo libero), qualifica il lead, COMPILA l'anagrafica del contatto via il
tool `salva_contatto` e a fine chiamata APRE un ticket di follow-up con `apri_ticket`
(titolo riassuntivo + priorità + trascrizione).

Flusso:
  1. Twilio -> POST /voice/incoming -> TwiML che apre un Media Stream verso /voice/stream.
  2. /voice/stream fa da ponte audio bidirezionale Twilio <-> OpenAI Realtime.
  3. I tool (salva_contatto, apri_ticket) girano in background per non bloccare l'audio.
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

from database import (
    SessionLocal, Contatto, ContattoStato, Ordine,
    CanaleOrdine, OrigineOrdine, StatoOrdine,
)
from services import whatsapp_agent
from services import voice_log
from services import ticket as ticket_service
from services import istruzioni
from services import profilo
from services import crm
from services import email as email_service
from services import documenti as documenti_service
from services.contesto import contesto_temporale
# Riusa la STESSA logica dei tool MCP (ElevenLabs) per i documenti e l'anagrafica locale,
# così il path vocale Realtime espone tool identici senza duplicare il codice.
from routers import mcp_server

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "alloy")
OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"


# ---------- Tool Realtime ----------

REALTIME_TOOLS = [
    {
        "type": "function",
        "name": "salva_contatto",
        "description": (
            "Salva o aggiorna in anagrafica i dati del lead con cui stai parlando. Chiamalo "
            "ogni volta che apprendi un dato nuovo (nome, azienda, email, ecc.), anche più volte "
            "durante la chiamata: i campi che ometti restano invariati. NON inventare dati: passa "
            "solo ciò che il chiamante ti ha detto."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nome": {"type": "string", "description": "Nome della persona."},
                "cognome": {"type": "string", "description": "Cognome della persona."},
                "ragione_sociale": {"type": "string", "description": "Ragione sociale della società."},
                "ruolo": {"type": "string", "description": "Ruolo della persona nella società."},
                "email": {"type": "string", "description": "Email di contatto."},
                "telefono": {"type": "string", "description": "Telefono, se diverso da quello della chiamata."},
                "sede": {"type": "string", "description": "Sede / località."},
                "stato": {"type": "string", "enum": ["cliente", "prospect"],
                          "description": "cliente se è già cliente, prospect se potenziale."},
                "titolo": {"type": "string", "enum": ["Signore", "Signora"],
                           "description": "Appellativo: imposta SOLO se sei certo del genere, altrimenti OMETTI."},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "aggiorna_contatto",
        "description": (
            "Aggiorna i dati ANAGRAFICI DELLA PERSONA quando emergono info nuove (email, ruolo, "
            "cognome, oppure titolo se diventa chiaro il genere). Passa solo i campi nuovi. "
            "Equivale a salva_contatto: usalo per gli aggiornamenti in corso di chiamata."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nome": {"type": "string"}, "cognome": {"type": "string"},
                "ragione_sociale": {"type": "string"}, "ruolo": {"type": "string"},
                "email": {"type": "string"}, "sede": {"type": "string"},
                "titolo": {"type": "string", "enum": ["Signore", "Signora"],
                           "description": "Imposta SOLO se certo del genere."},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "aggiorna_locale",
        "description": (
            "Aggiorna l'anagrafica del LOCALE/azienda del chiamante (ristorante/bar/hotel): città, "
            "indirizzo, ragione sociale, P.IVA, insegna. Usalo quando emerge un dato del locale prima "
            "mancante (es. la CITTÀ). Passa solo i campi nuovi."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "citta": {"type": "string"}, "indirizzo": {"type": "string"},
                "ragione_sociale": {"type": "string"}, "piva": {"type": "string"},
                "insegna": {"type": "string"},
            },
            "required": [],
        },
    },
    {"type": "function", "name": "leggi_listini_prezzi",
     "description": "Restituisce per intero i LISTINI e i PREZZI caricati. Usalo per prezzi, costi, sconti di listino, formati/confezioni. Poi rispondi a voce in modo breve.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"type": "function", "name": "leggi_condizioni_vendita",
     "description": "Restituisce le CONDIZIONI DI VENDITA: consegne, tempi, ordine minimo, pagamenti, contratti.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"type": "function", "name": "leggi_schede_prodotto",
     "description": "Restituisce le SCHEDE PRODOTTO: caratteristiche e dettagli tecnici dei prodotti.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"type": "function", "name": "leggi_faq",
     "description": "Restituisce le FAQ e il materiale informativo generale.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"type": "function", "name": "leggi_altri_documenti",
     "description": "Restituisce i documenti della categoria 'altro' (non classificati).",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {
        "type": "function",
        "name": "registra_ordine",
        "description": (
            "Registra un ORDINE del cliente con cui stai parlando (ristorante/bar/hotel). Chiamalo "
            "quando il chiamante ordina o riordina prodotti con le quantità. Passa l'elenco delle "
            "righe. Imposta conferma=true per registrarlo come CONFERMATO, false per lasciarlo in "
            "bozza: segui le indicazioni dell'amministratore su quando confermare. Dopo, riferisci a "
            "voce cosa hai registrato."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "righe": {
                    "type": "array",
                    "description": "I prodotti ordinati.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "descrizione": {"type": "string", "description": "Nome del prodotto."},
                            "quantita": {"type": "number"},
                            "unita": {"type": "string", "description": "Unità (pz, kg, casse, bottiglie, sacchi...)."},
                            "prezzo_unitario": {"type": "number", "description": "Prezzo unitario se noto, altrimenti 0."},
                        },
                        "required": ["descrizione"],
                    },
                },
                "note": {"type": "string", "description": "Eventuali note sull'ordine (consegna, urgenze...)."},
                "conferma": {"type": "boolean",
                             "description": "true = ordine confermato; false/omesso = bozza."},
            },
            "required": ["righe"],
        },
    },
    {
        "type": "function",
        "name": "aggiorna_ordine",
        "description": (
            "Aggiorna le NOTE di un ordine già registrato (orario di consegna preferito, richieste "
            "particolari, note sugli sconti applicati). Se non passi ordine_id, aggiorna l'ULTIMO "
            "ordine del cliente. Le note SOSTITUISCONO le precedenti (per aggiungere, includi anche "
            "il testo già presente). Non tocca le righe."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "Testo completo delle note dell'ordine."},
                "ordine_id": {"type": "number", "description": "ID dell'ordine (opzionale; default = ultimo)."},
            },
            "required": ["note"],
        },
    },
    {
        "type": "function",
        "name": "storico_ordini",
        "description": (
            "Restituisce gli ordini recenti del cliente (prodotti e quantità). Usalo per capire cosa "
            "ordina di solito e DISAMBIGUARE un prodotto generico (es. 'la Peroni' → quale formato; se "
            "ne ha ordinati più di uno, chiedi quale), o per RIORDINARE un ordine passato. "
            "giorni: finestra (7=ultima settimana, 30=ultimo mese; 0/omesso=tutti)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "giorni": {"type": "number", "description": "Finestra temporale in giorni (7=settimana, 30=mese; 0=tutti)."},
                "limite": {"type": "number", "description": "Numero massimo di ordini (default 10)."},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "invia_riepilogo_ordine",
        "description": (
            "Invia via EMAIL al cliente il riepilogo dell'ordine appena registrato. Usa l'ordine_id "
            "restituito da registra_ordine (se lo ometti, usa l'ultimo ordine del cliente). Se il "
            "cliente non ha un'email salvata, la funzione te lo segnala: in quel caso chiedigli "
            "l'indirizzo, salvalo con salva_contatto e riprova."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ordine_id": {"type": "number", "description": "ID dell'ordine da riepilogare (opzionale)."},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "invia_mail",
        "description": (
            "Invia un'email al cliente. `testo` = corpo del messaggio, OBBLIGATORIO: scrivilo tu, "
            "chiaro e completo (è quello che leggerà il cliente). `oggetto` opzionale. "
            "`categoria_allegato` opzionale: se vuoi ALLEGARE documenti indica la categoria, usando "
            "SOLO quelle elencate in DOCUMENTI DISPONIBILI; lascia vuoto se non c'è nulla da allegare "
            "(la mail parte col solo testo). Se manca l'email del cliente te lo segnala: chiedigliela, "
            "salvala con aggiorna_contatto e riprova."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "testo": {"type": "string", "description": "Corpo dell'email (obbligatorio)."},
                "oggetto": {"type": "string", "description": "Oggetto dell'email (opzionale)."},
                "categoria_allegato": {"type": "string",
                                       "enum": ["", "listino", "schede_prodotto", "contratti", "faq", "altro"],
                                       "description": "Categoria dei documenti da allegare (vuoto = nessun allegato)."},
            },
            "required": ["testo"],
        },
    },
    {
        "type": "function",
        "name": "apri_ticket",
        "description": (
            "Apre (o aggiorna) il TICKET di follow-up per questo lead, per il team commerciale. "
            "Chiamalo quando hai capito di cosa ha bisogno il lead — tipicamente verso la fine "
            "della chiamata. Riepiloga in titolo e descrizione la richiesta e assegna la priorità "
            "secondo i criteri ricevuti. Dopo averlo aperto, di' al chiamante che un collega lo "
            "ricontatterà."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "titolo": {"type": "string", "description": "Titolo breve riassuntivo (max ~10 parole)."},
                "priorita": {"type": "string", "enum": ["alta", "media", "bassa"],
                             "description": "Priorità del lead secondo i criteri dell'azienda."},
                "descrizione": {"type": "string", "description": "Sintesi della richiesta e dei dati utili raccolti."},
            },
            "required": ["titolo", "descrizione"],
        },
    },
    {
        "type": "function",
        "name": "lascia_promemoria",
        "description": (
            "[SOLO AMMINISTRATORE] Registra un promemoria per un CLIENTE: quando quel cliente "
            "chiamerà, l'assistente ne terrà conto (es. un'offerta da comunicargli). Indica il nome "
            "del cliente (e la società se serve a distinguerlo), il testo dell'avviso e i giorni di "
            "validità (0 = senza scadenza). Se più clienti corrispondono, te li elenco per scegliere."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nome_cliente": {"type": "string", "description": "Nome e/o cognome del cliente destinatario."},
                "testo": {"type": "string", "description": "Il messaggio/avviso da ricordare."},
                "societa": {"type": "string", "description": "Società del cliente, per distinguerlo (opzionale)."},
                "giorni_validita": {"type": "number", "description": "Validità in giorni (0 = senza scadenza)."},
            },
            "required": ["nome_cliente", "testo"],
        },
    },
]


# ---------- Istruzioni vocali ----------

def _scheda_contatto(c: Contatto) -> str:
    campi = [
        ("Nome", c.nome), ("Cognome", c.cognome), ("Ragione sociale", c.ragione_sociale),
        ("Ruolo", c.ruolo), ("Email", c.email), ("Sede", c.sede),
        ("Stato", c.stato.value if c.stato else None),
    ]
    noti = [f"{k}: {v}" for k, v in campi if v]
    return "\n".join(noti) if noti else "(nessun dato ancora: è un nuovo lead)"


def _build_voice_instructions(db, contatto: Contatto) -> str:
    """Prompt vocale = SOLO la 'configurazione' della dashboard (come ElevenLabs {{configurazione}}),
    con i segnaposto delle dynamic variables sostituiti con i dati reali del chiamante.
    Così il comportamento si governa da un unico posto e i due provider restano allineati."""
    from routers import elevenlabs  # _riassunto: stessa logica del webhook ElevenLabs (import pigro)

    configurazione = (profilo.blocco_prompt(db) + istruzioni.blocco_prompt()
                      + documenti_service.catalogo_prompt(db)).strip()
    if contatto:  # promemoria mirati dell'amministratore per questo cliente
        from services import promemoria
        configurazione += promemoria.blocco_prompt(db, contatto.id)
        if promemoria.is_admin(contatto.telefono or "", db):
            configurazione += (
                "\n\n=== MODALITÀ AMMINISTRATORE (il chiamante è l'amministratore) ===\n"
                "Puoi LASCIARE PROMEMORIA per i clienti: quando ti chiede di avvisare un cliente di "
                "qualcosa (es. uno sconto), usa lascia_promemoria con il nome del cliente (e la società "
                "se serve a distinguerlo), il testo e i giorni di validità. Se più clienti corrispondono, "
                "chiedi quale. Conferma a voce quando l'hai registrato."
            )

    nome = (contatto.nome or "").strip() if contatto else ""
    cognome = (contatto.cognome or "").strip() if contatto else ""
    titolo = (contatto.titolo or "").strip() if contatto else ""
    rag = (contatto.ragione_sociale or "").strip() if contatto else ""
    known = bool(nome or cognome or rag)
    societa = crm.societa_di_contatto(db, contatto) if contatto else None
    ultimo = ((db.query(Ordine).filter(Ordine.contatto_id == contatto.id)
               .order_by(Ordine.data.desc()).first()) if contatto else None)

    if known:
        riassunto = elevenlabs._riassunto(contatto, societa, ultimo)
        stato_cli = (societa.stato_relazione.value if societa else contatto.stato.value)
        ultimo_txt = ((f"#{ultimo.id} del {ultimo.data.strftime('%d/%m/%Y')}, {ultimo.n_articoli} "
                       f"articoli, € {ultimo.totale:.2f} ({ultimo.stato.value})") if ultimo else "nessuno")
    else:
        riassunto = "Chiamante non riconosciuto: è un nuovo contatto da registrare."
        stato_cli, ultimo_txt = "", "nessuno"

    vals = {
        "cliente_conosciuto": "sì" if known else "no",
        "nome_cliente": (f"{nome} {cognome}".strip() or rag) if known else "",
        "nome": nome if known else "",
        "cognome": cognome if known else "",
        "titolo": titolo if known else "",
        "societa": (societa.nome if societa else rag) if known else "",
        "stato_cliente": stato_cli,
        "ruolo": (contatto.ruolo or "") if known else "",
        "email_cliente": (contatto.email or "") if known else "",
        "ultimo_ordine": ultimo_txt,
        "riassunto_cliente": riassunto,
        "telefono_chiamante": (contatto.telefono or "") if contatto else "",
        "azienda": profilo.nome_azienda(db),
        "saluto": "",          # nel Realtime il saluto lo dice il modello dal prompt
        "configurazione": "",  # evita auto-riferimenti se il testo contiene {{configurazione}}
    }
    testo = configurazione or (
        f'Sei l\'assistente telefonico di "{profilo.nome_azienda(db)}". Parla in italiano, frasi '
        "brevi e cordiali, dai del lei. (Configura il prompt in dashboard → Configurazione assistente.)"
    )
    for k, v in vals.items():
        testo = testo.replace("{{" + k + "}}", v or "")
    return testo


def _saluto_voce(db, contatto) -> str:
    """Saluto d'apertura per la voce: stesso template della dashboard usato da ElevenLabs
    (azienda.saluto per i noti, azienda.saluto_sconosciuto per gli anonimi), con i segnaposto
    {nome}/{cognome}/{titolo}/{azienda} sostituiti."""
    from routers import elevenlabs
    az = profilo.get_azienda(db)
    known = bool(contatto and (contatto.nome or contatto.cognome or contatto.ragione_sociale))
    template = (((az.saluto if known else az.saluto_sconosciuto) or "").strip()) if az else ""
    az_nome = (az.nome if az else "") or ""
    if template:
        return elevenlabs._componi_saluto(template, contatto if known else None, az_nome)
    if known:
        app = (contatto.cognome or contatto.nome or "").strip()
        return f"Buongiorno signor {app}, come posso aiutarla?" if app else elevenlabs._SALUTO_DEFAULT
    return elevenlabs._SALUTO_DEFAULT


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
        "contatto_id": None,
        "iniziata_at": None,
        "response_active": False,   # c'è una risposta del modello in corso?
        "speak_pending": False,     # un risultato di tool attende di essere verbalizzato
        "tasks": set(),             # task in background
        "trascrizione": [],         # [{ruolo, testo, item}, ...] per il log chiamata
        "ordine_item": {},          # item_id -> sequenza di creazione (per ordinare la trascrizione)
        "seq_item": 0,
        "assist_buf": "",           # accumulo del transcript dell'assistente nel turno corrente
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
        """Identifica/crea il contatto, imposta audio/voce/VAD/tool/istruzioni, fa salutare."""
        contatto = whatsapp_agent.trova_o_crea_contatto(db, stato["telefono"] or "sconosciuto")
        stato["contatto_id"] = contatto.id
        logger.info("Chiamante: contatto id %s (%s)", contatto.id, contatto.nome_completo)

        await openai_ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": _build_voice_instructions(db, contatto),
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
        # Prima battuta: fai dire ESATTAMENTE il saluto configurato in dashboard, poi ascolta.
        saluto = _saluto_voce(db, contatto)
        await openai_ws.send(json.dumps({
            "type": "response.create",
            "response": {"instructions": (
                "Apri la conversazione dicendo ESATTAMENTE questa frase, con tono cordiale e "
                f"naturale, e poi fermati ad ascoltare il chiamante: «{saluto}»")},
        }))

    async def richiedi_risposta():
        """Chiede al modello di generare una risposta, rispettando la concorrenza."""
        if stato["response_active"]:
            stato["speak_pending"] = True
        else:
            stato["response_active"] = True
            await openai_ws.send(json.dumps({"type": "response.create"}))

    def _salva_contatto(args: dict) -> dict:
        """Crea/aggiorna l'anagrafica del contatto (sincrona, gira in thread)."""
        contatto = db.get(Contatto, stato["contatto_id"]) if stato.get("contatto_id") else None
        if not contatto:
            return {"errore": "Contatto non disponibile."}
        campi = ["titolo", "nome", "cognome", "ragione_sociale", "ruolo", "email", "telefono", "sede"]
        cambiato = False
        for c in campi:
            val = (args.get(c) or "").strip()
            if val and getattr(contatto, c) != val:
                setattr(contatto, c, val)
                cambiato = True
        st = (args.get("stato") or "").strip()
        if st in (ContattoStato.CLIENTE.value, ContattoStato.PROSPECT.value):
            nuovo = ContattoStato(st)
            if contatto.stato != nuovo:
                contatto.stato = nuovo
                cambiato = True
        if cambiato:
            db.commit()
        # Registrazione prospect: crea/aggancia la società corrispondente (find-or-create,
        # niente duplicati). Avviene solo quando si conosce la ragione sociale.
        societa = crm.societa_di_contatto(db, contatto)
        risultato = {"salvato": True, "contatto_id": contatto.id}
        if societa:
            risultato["societa"] = societa.nome
        return risultato

    def _leggi(categoria: str) -> dict:
        """Ritorna il testo integrale dei documenti di una categoria (no LLM). Stessa logica MCP."""
        return mcp_server._leggi_categoria(categoria)

    def _aggiorna_locale(args: dict) -> dict:
        """Aggiorna l'anagrafica del locale/società del chiamante. Stessa logica del tool MCP."""
        tel = stato.get("telefono") or ""
        if not tel:
            return {"errore": "Numero del chiamante non disponibile."}
        # @mcp.tool() può ritornare la funzione grezza o un wrapper con .fn: gestiamo entrambi.
        fn = getattr(mcp_server.aggiorna_locale, "fn", mcp_server.aggiorna_locale)
        return fn(
            telefono=tel,
            citta=args.get("citta", ""), indirizzo=args.get("indirizzo", ""),
            ragione_sociale=args.get("ragione_sociale", ""), piva=args.get("piva", ""),
            insegna=args.get("insegna", ""),
        )

    def _aggiorna_ordine(args: dict) -> dict:
        """Aggiorna le note di un ordine già creato del chiamante. Stessa logica del tool MCP."""
        tel = stato.get("telefono") or ""
        if not tel:
            return {"errore": "Numero del chiamante non disponibile."}
        fn = getattr(mcp_server.aggiorna_ordine, "fn", mcp_server.aggiorna_ordine)
        return fn(telefono=tel, note=args.get("note", ""), ordine_id=int(args.get("ordine_id") or 0))

    def _storico_ordini(args: dict) -> dict:
        """Storico ordini del cliente (per disambiguare prodotti o riordinare). Logica MCP."""
        tel = stato.get("telefono") or ""
        if not tel:
            return {"errore": "Numero del chiamante non disponibile."}
        fn = getattr(mcp_server.storico_ordini, "fn", mcp_server.storico_ordini)
        return fn(telefono=tel, giorni=int(args.get("giorni") or 0), limite=int(args.get("limite") or 10))

    def _registra_ordine(righe: list, note: str, conferma: bool) -> dict:
        """Registra un ordine sulla società del chiamante (sincrona, gira in thread).
        Lo stato (confermato/bozza) lo decide il modello via `conferma`."""
        contatto = db.get(Contatto, stato["contatto_id"]) if stato.get("contatto_id") else None
        if not contatto:
            return {"errore": "Contatto non disponibile."}
        righe = [r for r in (righe or []) if (r.get("descrizione") or "").strip()]
        if not righe:
            return {"errore": "Nessun prodotto da registrare."}
        societa = crm.societa_di_contatto(db, contatto) or crm.trova_o_crea_societa(db, insegna=contatto.nome_completo)
        if not contatto.societa_id:
            contatto.societa_id = societa.id
            contatto.is_primario = True
            db.commit()
        ordine, creato = crm.registra_ordine_conversazione(
            db, societa_id=societa.id, righe=righe, contatto_id=contatto.id,
            origine=OrigineOrdine.CLIENTE, canale=CanaleOrdine.VOCE,
            note=(note or "").strip() or None,
            stato=StatoOrdine.CONFERMATO if conferma else StatoOrdine.BOZZA,
        )
        if not ordine:
            return {"errore": "Non sono riuscito a registrare l'ordine."}
        return {"registrato": True, "aggiornato": not creato, "ordine_id": ordine.id,
                "stato": ordine.stato.value, "articoli": ordine.n_articoli, "totale": ordine.totale}

    def _invia_mail(args: dict) -> dict:
        """Invia un'email a testo libero al cliente, con allegato opzionale per categoria (in thread)."""
        contatto = db.get(Contatto, stato["contatto_id"]) if stato.get("contatto_id") else None
        if not contatto:
            return {"errore": "Contatto non disponibile."}
        return documenti_service.invia_mail_contatto(
            db, contatto, args.get("testo", ""), args.get("oggetto", ""),
            args.get("categoria_allegato", ""), profilo.nome_azienda(db))

    def _invia_riepilogo_ordine(ordine_id) -> dict:
        """Invia al cliente via email il riepilogo dell'ordine (sincrona, gira in thread).
        Se manca l'email del contatto, lo segnala così il modello può chiederla."""
        contatto = db.get(Contatto, stato["contatto_id"]) if stato.get("contatto_id") else None
        if not contatto:
            return {"errore": "Contatto non disponibile."}
        ordine = None
        if ordine_id:
            try:
                ordine = db.get(Ordine, int(ordine_id))
            except (ValueError, TypeError):
                ordine = None
        if not ordine:
            ordine = (db.query(Ordine).filter(Ordine.contatto_id == contatto.id)
                      .order_by(Ordine.data.desc()).first())
        if not ordine:
            return {"errore": "Nessun ordine da riepilogare."}
        email = (contatto.email or "").strip()
        if not email:
            return {"email_mancante": True,
                    "messaggio": "Il cliente non ha un'email salvata: chiedigliela e salvala con salva_contatto, poi riprova."}
        oggetto = f"Riepilogo ordine #{ordine.id} - {profilo.nome_azienda(db)}"
        corpo = (f"Gentile {contatto.nome or contatto.nome_completo},\n\n"
                 f"come da accordi telefonici, le confermiamo il suo ordine:\n\n"
                 f"{crm.riepilogo_ordine(ordine)}\n\n"
                 f"Cordiali saluti,\n{profilo.nome_azienda(db)}")
        inviata = email_service.invia_email(destinatario=email, oggetto=oggetto, corpo=corpo)
        if not inviata:
            return {"errore": "Invio email non riuscito (verifica la configurazione Gmail)."}
        return {"inviato": True, "email": email, "ordine_id": ordine.id}

    def _trascrizione_ordinata() -> list:
        """Trascrizione riordinata per ordine di creazione degli item (cronologico)."""
        ordine = stato["ordine_item"]
        def _key(d):
            return d["ord"] if "ord" in d else ordine.get(d.get("item"), 10**9)
        return sorted(stato["trascrizione"], key=_key)

    def _lascia_promemoria(args: dict) -> dict:
        """[admin] Registra un promemoria per un cliente. Stessa logica del tool MCP (verifica admin)."""
        tel = stato.get("telefono") or ""
        fn = getattr(mcp_server.lascia_promemoria, "fn", mcp_server.lascia_promemoria)
        return fn(telefono=tel, nome_cliente=args.get("nome_cliente", ""), testo=args.get("testo", ""),
                  societa=args.get("societa", ""), giorni_validita=int(args.get("giorni_validita") or 0))

    def _apri_ticket(titolo: str, priorita: str, descrizione: str) -> dict:
        """Apre/aggiorna il ticket di follow-up usando la trascrizione come storia (sincrona)."""
        storia = ticket_service.formatta_storia(_trascrizione_ordinata())
        contatto_id = stato.get("contatto_id")
        esistente = whatsapp_agent._ticket_aperto(db, contatto_id) if contatto_id else None
        if esistente:
            esistente.titolo = (titolo or esistente.titolo).strip()[:300]
            p = ticket_service.normalizza_priorita(priorita)
            if p:
                esistente.priorita = p
            esistente.descrizione = (descrizione or "").strip() or esistente.descrizione
            esistente.storia = storia or esistente.storia
            db.commit()
            return {"aperto": True, "ticket_id": esistente.id}
        t = ticket_service.apri_ticket(
            db, contatto_id=contatto_id,
            titolo=titolo or "Lead telefonico", priorita=priorita,
            descrizione=descrizione or "", storia=storia, canale="voce",
        )
        if not t:
            return {"errore": "Non sono riuscito a registrare il follow-up."}
        return {"aperto": True, "ticket_id": t.id}

    async def esegui_tool(name: str, call_id: str, arguments: str):
        """Esegue il tool richiesto in background e ne verbalizza il risultato."""
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            args = {}
        logger.info("  voce tool: %s(%s)", name, args)

        _LEGGI = {
            "leggi_listini_prezzi": "listino", "leggi_condizioni_vendita": "contratti",
            "leggi_schede_prodotto": "schede_prodotto", "leggi_faq": "faq",
            "leggi_altri_documenti": "altro",
        }
        if name in ("salva_contatto", "aggiorna_contatto"):
            result = await asyncio.to_thread(_salva_contatto, args)
        elif name == "aggiorna_locale":
            result = await asyncio.to_thread(_aggiorna_locale, args)
        elif name in _LEGGI:
            result = await asyncio.to_thread(_leggi, _LEGGI[name])
        elif name == "registra_ordine":
            result = await asyncio.to_thread(
                _registra_ordine, args.get("righe", []), args.get("note", ""), bool(args.get("conferma")))
        elif name == "aggiorna_ordine":
            result = await asyncio.to_thread(_aggiorna_ordine, args)
        elif name == "storico_ordini":
            result = await asyncio.to_thread(_storico_ordini, args)
        elif name == "invia_riepilogo_ordine":
            result = await asyncio.to_thread(_invia_riepilogo_ordine, args.get("ordine_id"))
        elif name == "invia_mail":
            result = await asyncio.to_thread(_invia_mail, args)
        elif name == "apri_ticket":
            result = await asyncio.to_thread(
                _apri_ticket, args.get("titolo", ""), args.get("priorita", ""), args.get("descrizione", ""))
        elif name == "lascia_promemoria":
            result = await asyncio.to_thread(_lascia_promemoria, args)
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
                    if stato["speak_pending"]:
                        stato["speak_pending"] = False
                        stato["response_active"] = True
                        await openai_ws.send(json.dumps({"type": "response.create"}))

                elif etype == "conversation.item.created":
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
                    # Barge-in: il chiamante riprende a parlare -> svuota l'audio in coda e annulla.
                    if stato["stream_sid"]:
                        await twilio_ws.send_text(json.dumps({
                            "event": "clear", "streamSid": stato["stream_sid"],
                        }))
                    if stato["response_active"]:
                        await openai_ws.send(json.dumps({"type": "response.cancel"}))

                elif etype == "response.function_call_arguments.done":
                    avvia_tool(evt.get("name"), evt.get("call_id"), evt.get("arguments", "{}"))

                elif etype == "conversation.item.input_audio_transcription.completed":
                    trascr = evt.get("transcript", "").strip()
                    logger.info("  chiamante: %s", trascr)
                    if trascr:
                        stato["trascrizione"].append(
                            {"ruolo": "Cliente", "testo": trascr, "item": evt.get("item_id")})

                elif etype == "error":
                    logger.error("  OpenAI error: %s", evt.get("error"))
        except Exception as e:
            logger.info("Loop OpenAI chiuso: %s", e)

    try:
        await asyncio.gather(da_twilio(), da_openai())
    finally:
        for t in list(stato["tasks"]):
            t.cancel()
        # Salva il log della chiamata (trascrizione + riassunto) se c'è stato dialogo.
        if stato["contatto_id"] and stato["trascrizione"]:
            durata = None
            if stato["iniziata_at"]:
                durata = int((datetime.utcnow() - stato["iniziata_at"]).total_seconds())
            try:
                await asyncio.to_thread(
                    voice_log.salva_chiamata, db, stato["contatto_id"], stato["telefono"],
                    _trascrizione_ordinata(), stato["iniziata_at"], durata,
                )
            except Exception as e:
                logger.error("Salvataggio log chiamata fallito: %s", e)
        db.close()
        if twilio_ws.client_state != WebSocketState.DISCONNECTED:
            await twilio_ws.close()
        logger.info("Chiamata terminata, risorse liberate")
