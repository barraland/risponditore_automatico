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
        "name": "invia_documento",
        "description": (
            "Invia al cliente, via EMAIL, i documenti caricati di una categoria (es. listino prezzi, "
            "condizioni e costi di consegna, schede prodotto). Indica la categoria. Se il cliente non "
            "ha un'email salvata, la funzione te lo segnala: chiedigliela, salvala con salva_contatto e "
            "riprova. Usalo secondo le indicazioni dell'amministratore."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "categoria": {"type": "string",
                              "enum": ["listino", "schede_prodotto", "contratti", "faq", "altro"],
                              "description": "Categoria dei documenti da inviare."},
            },
            "required": ["categoria"],
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
    nome_az = profilo.nome_azienda(db)
    appellativo = (contatto.cognome or contatto.nome or "").strip()
    saluto = f"Buongiorno signor {appellativo}" if appellativo else "Buongiorno"

    return (
        f'Sei l\'assistente telefonico di "{nome_az}". Parli in italiano, al telefono, con frasi '
        "BREVI e cordiali (dai del lei). Ti occupi di LEAD CAPTURE: rispondi a chi chiama per "
        "informazioni su prodotti, servizi, ordini, costi e tempi, e nel frattempo raccogli i suoi "
        "dati e qualifichi il lead per il team commerciale.\n"
        f"{contesto_temporale()}\n\n"
        f"DATI GIÀ NOTI DEL CHIAMANTE:\n{_scheda_contatto(contatto)}\n\n"
        "COME PARLARE:\n"
        "- Sei al TELEFONO: una cosa alla volta, frasi naturali e brevi. Tono cordiale, dai del lei.\n"
        f"- APERTURA: saluta (es. «{saluto}»), presentati come l'assistente di {nome_az} e chiedi "
        "come puoi aiutare. Poi FERMATI e aspetta che il chiamante dica di cosa ha bisogno: non "
        "anticipare verifiche o domande di chiusura prima che abbia chiesto qualcosa. Se è un nuovo "
        "contatto, a un certo punto chiedi con garbo il suo nome.\n"
        "- Leggi numeri, date e importi in modo naturale (es. 'cinquecento euro', 'il tre marzo').\n\n"
        "COME RISPONDERE:\n"
        "- Per domande su prodotti/servizi/costi/tempi usa le informazioni della sezione "
        "'COSA OFFRIAMO'. Se servono DETTAGLI che non sono lì, chiama lo strumento della categoria "
        "giusta — leggi_listini_prezzi (prezzi/listini), leggi_condizioni_vendita (consegne, minimi, "
        "pagamenti), leggi_schede_prodotto, leggi_faq, leggi_altri_documenti: ti restituisce il testo "
        "del documento, poi rispondi a voce in modo breve. Mentre attendi puoi dire «Verifico subito». "
        "Se nemmeno i documenti hanno il dato, dillo con onestà (non inventare) e rassicura che un "
        "collega ricontatterà il chiamante.\n"
        "- Hai anche lo strumento invia_documento per inviare al cliente via email i documenti "
        "caricati (es. listino, condizioni di consegna): usalo secondo le indicazioni "
        "dell'amministratore.\n\n"
        "ORDINI:\n"
        "- Se il chiamante ordina o riordina prodotti (con quantità), chiama registra_ordine con "
        "l'elenco delle righe (prodotto, quantità, unità, prezzo se lo sai). Imposta conferma=true o "
        "false secondo le indicazioni dell'amministratore su quando confermare un ordine. Se devi "
        "inviare il riepilogo via email usa invia_riepilogo_ordine; se il cliente non ha un'email "
        "registrata, chiedigliela e salvala con salva_contatto, poi invia. Riferisci sempre a voce "
        "cosa hai registrato.\n\n"
        "RACCOLTA DATI (anagrafica):\n"
        "- Raccogli con naturalezza, senza interrogatori, le informazioni della sezione 'COME "
        "QUALIFICARE IL LEAD'. Poche per volta.\n"
        "- Ogni volta che apprendi un dato della PERSONA (nome, ruolo, email...) chiama SUBITO "
        "salva_contatto (o aggiorna_contatto) con i campi appresi. Se ti detta l'email, fattela "
        "ripetere se non sei sicuro. Imposta `titolo` (Signore/Signora) SOLO se sei certo del genere.\n"
        "- Per i dati del LOCALE (città, indirizzo, ragione sociale, P.IVA) usa aggiorna_locale.\n\n"
        "TICKET DI FOLLOW-UP (apri_ticket):\n"
        "- Quando hai capito di cosa ha bisogno il lead (di solito verso la fine), chiama apri_ticket "
        "con un titolo riassuntivo, una descrizione della richiesta e la PRIORITÀ (alta/media/bassa) "
        "secondo i criteri della sezione 'COME ASSEGNARE LA PRIORITÀ'. Apri UN SOLO ticket per "
        "chiamata. Poi conferma a voce che un collega lo ricontatterà.\n\n"
        "NON LASCIARE SILENZI — chiudi sempre il turno:\n"
        "- Usa «È questo che le serviva?» SOLO dopo aver effettivamente risposto a una richiesta o "
        "svolto un'azione, mai all'inizio o quando il chiamante non ha ancora chiesto nulla. Quando "
        "la richiesta è soddisfatta o il chiamante ringrazia, chiudi con cortesia: «C'è altro con cui "
        "posso esserle utile?». Se risponde di no, salutalo e concludi.\n"
        "- Non restare mai muto dopo aver parlato, ma dopo il saluto iniziale aspetta che il "
        "chiamante parli invece di riempire il silenzio con domande di chiusura."
        f"{profilo.blocco_prompt(db)}"
        f"{istruzioni.blocco_prompt()}"
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
        await openai_ws.send(json.dumps({"type": "response.create"}))

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

    def _invia_documento(categoria: str) -> dict:
        """Invia via email al cliente i documenti della categoria indicata (gira in thread)."""
        contatto = db.get(Contatto, stato["contatto_id"]) if stato.get("contatto_id") else None
        if not contatto:
            return {"errore": "Contatto non disponibile."}
        return documenti_service.invia_documenti_email(db, contatto, categoria, profilo.nome_azienda(db))

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
        elif name == "invia_riepilogo_ordine":
            result = await asyncio.to_thread(_invia_riepilogo_ordine, args.get("ordine_id"))
        elif name == "invia_documento":
            result = await asyncio.to_thread(_invia_documento, args.get("categoria", ""))
        elif name == "apri_ticket":
            result = await asyncio.to_thread(
                _apri_ticket, args.get("titolo", ""), args.get("priorita", ""), args.get("descrizione", ""))
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
