"""Webhook di inizializzazione conversazione per ElevenLabs Conversational AI.

A inizio chiamata ElevenLabs POSTa qui {caller_id, agent_id, called_number, call_sid};
noi riconosciamo il contatto dal numero e restituiamo:
- dynamic_variables: il contesto del cliente da iniettare nel prompt dell'agente;
- conversation_config_override: first message personalizzato + lingua.

Configura l'URL in ElevenLabs (agente → "Conversation initiation webhook"):
  https://<dominio-ngrok>/elevenlabs/init
"""

import hashlib
import hmac
import logging
import os
import re
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from database import SessionLocal, Ordine, ChiamataVoce
from services import whatsapp_agent
from services import profilo
from services import istruzioni
from services import documenti as documenti_service
from services import promemoria
from services import prompts
from services import inoltri
from services import telefonia

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/elevenlabs")

# Auth opzionale per l'init webhook: se impostato, richiede Authorization: Bearer <token>.
_WEBHOOK_TOKEN = os.getenv("ELEVENLABS_WEBHOOK_TOKEN", "").strip()

# Secret HMAC del post-call webhook (lo genera ElevenLabs quando crei il webhook).
# Se impostato, verifichiamo la firma e rifiutiamo le richieste non valide.
_POSTCALL_SECRET = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "").strip()

# Elenco fisso delle variabili: ElevenLabs richiede che il webhook ritorni SEMPRE
# tutte le dynamic variables usate dall'agente, anche vuote.
_VARS_VUOTE = {
    "cliente_conosciuto": "no",
    "nome_cliente": "",
    "nome": "",
    "cognome": "",
    "titolo": "",
    "societa": "",
    "stato_cliente": "",
    "ruolo": "",
    "email_cliente": "",
    "ultimo_ordine": "nessuno",
    "riassunto_cliente": "Chiamante non riconosciuto: è un nuovo contatto da registrare.",
    "saluto": "Buongiorno, come posso aiutarla?",
}

# Se ELEVENLABS_INVIA_OVERRIDE=1, oltre alle variabili inviamo anche l'override di
# first_message/language. NB: richiede che quegli override siano ABILITATI nell'agente,
# altrimenti ElevenLabs rifiuta la risposta e la chiamata cade. Default: OFF (usa {{saluto}}).
_INVIA_OVERRIDE = os.getenv("ELEVENLABS_INVIA_OVERRIDE", "").strip() in ("1", "true", "yes")

# Saluto di default se l'amministratore non ha configurato una formula in dashboard.
_SALUTO_DEFAULT = "Buongiorno, come posso aiutarla?"


def _pulisci_saluto(s: str) -> str:
    """Normalizza spazi e punteggiatura dopo la sostituzione dei segnaposto vuoti."""
    s = re.sub(r"\s+", " ", s)
    for a, b in ((" ,", ","), (" .", "."), (" !", "!"), (" ?", "?")):
        s = s.replace(a, b)
    return s.strip()


def _componi_saluto(template: str, contatto, azienda_nome: str) -> str:
    """Sostituisce i segnaposto {nome}/{cognome}/{azienda} nella formula di saluto.

    Per un chiamante non riconosciuto `contatto` è None → {nome}/{cognome} diventano vuoti.
    """
    nome = (contatto.nome or "").strip() if contatto else ""
    cognome = (contatto.cognome or "").strip() if contatto else ""
    titolo = (contatto.titolo or "").strip() if contatto else ""
    s = (template.replace("{titolo}", titolo)
                 .replace("{nome}", nome)
                 .replace("{cognome}", cognome)
                 .replace("{azienda}", azienda_nome or ""))
    return _pulisci_saluto(s) or _SALUTO_DEFAULT


def _riassunto(contatto, societa, ultimo) -> str:
    pezzi = [contatto.nome_completo]
    if contatto.ruolo:
        pezzi.append(contatto.ruolo)
    if societa:
        pezzi.append(f"di {societa.nome} ({societa.stato_relazione.value})")
    elif contatto.ragione_sociale:
        pezzi.append(f"di {contatto.ragione_sociale}")
    testo = ", ".join(pezzi) + "."
    if ultimo:
        testo += (f" Ultimo ordine: #{ultimo.id} del {ultimo.data.strftime('%d/%m/%Y')}, "
                  f"{ultimo.n_articoli} articoli, € {ultimo.totale:.2f} ({ultimo.stato.value}).")
    return testo


@router.post("/init")
async def init_conversazione(request: Request):
    """Restituisce a ElevenLabs il contesto del chiamante per la conversazione."""
    if _WEBHOOK_TOKEN and request.headers.get("authorization", "") != f"Bearer {_WEBHOOK_TOKEN}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        data = await request.json()
    except Exception:
        form = await request.form()
        data = dict(form)
    caller = (data.get("caller_id") or "").strip()
    # Registra la chiamata per l'eventuale inoltro: ElevenLabs gira sul tuo Twilio, quindi col
    # call_sid possiamo comandare la call. L'host pubblico serve per le TwiML di whisper.
    telefonia.registra_chiamata(caller, (data.get("call_sid") or "").strip(),
                                request.headers.get("host", request.url.hostname))

    db = SessionLocal()
    try:
        admin = promemoria.is_admin(caller, db) if caller else False
        contatto = whatsapp_agent.trova_contatto(db, caller) if (caller and not admin) else None
        az = profilo.get_azienda(db)
        template_noto = ((az.saluto or "").strip() if az else "")
        template_sconosciuto = ((az.saluto_sconosciuto or "").strip() if az else "")
        az_nome = (az.nome if az else "") or ""
        if admin:
            dv = dict(_VARS_VUOTE)
            dv["saluto"] = "Buongiorno, sono l'assistente. Vuole lasciare un promemoria per un cliente?"
            first = dv["saluto"]
        elif contatto:
            societa = contatto.societa
            ultimo = (db.query(Ordine).filter(Ordine.contatto_id == contatto.id)
                      .order_by(Ordine.data.desc()).first())
            dv = {
                "cliente_conosciuto": "sì",
                "nome_cliente": contatto.nome_completo,
                "nome": contatto.nome or "",
                "cognome": contatto.cognome or "",
                "titolo": contatto.titolo or "",
                "societa": (societa.nome if societa else (contatto.ragione_sociale or "")),
                "stato_cliente": (societa.stato_relazione.value if societa else contatto.stato.value),
                "ruolo": contatto.ruolo or "",
                "email_cliente": contatto.email or "",
                "ultimo_ordine": (
                    f"#{ultimo.id} del {ultimo.data.strftime('%d/%m/%Y')}, {ultimo.n_articoli} "
                    f"articoli, € {ultimo.totale:.2f} ({ultimo.stato.value})" if ultimo else "nessuno"),
                "riassunto_cliente": _riassunto(contatto, societa, ultimo),
            }
            if template_noto:
                first = _componi_saluto(template_noto, contatto, az_nome)
            else:
                appellativo = (contatto.cognome or contatto.nome or "").strip()
                first = (f"Buongiorno signor {appellativo}, come posso aiutarla?" if appellativo
                         else _SALUTO_DEFAULT)
            dv["saluto"] = first
            logger.info("ElevenLabs init: riconosciuto %s (%s)", contatto.nome_completo, caller)
        else:
            dv = dict(_VARS_VUOTE)
            if template_sconosciuto:
                dv["saluto"] = _componi_saluto(template_sconosciuto, None, az_nome)
            first = dv["saluto"]
            logger.info("ElevenLabs init: numero %s non riconosciuto", caller)

        # Numero del chiamante tra le variabili: torna nel payload post-call, così a fine
        # chiamata sappiamo a quale contatto associare la trascrizione.
        dv["telefono_chiamante"] = caller
        # "Configurazione assistente" della dashboard (profilo + istruzioni admin): la stessa
        # iniettata negli LLM di WhatsApp/voce. Così ElevenLabs usa il TUO prompt, non il suo.
        if admin:
            # Chiama l'amministratore: prompt di sistema DEDICATO (in prompts/voce_admin.txt),
            # NON il prompt clienti della dashboard. Niente registrazione/ticket per lui.
            dv["configurazione"] = prompts.voce_admin()
        else:
            dv["configurazione"] = (profilo.blocco_prompt(db) + istruzioni.blocco_prompt()
                                    + documenti_service.catalogo_prompt(db)
                                    + inoltri.blocco_prompt(db)).strip()
            if contatto:  # promemoria mirati lasciati dall'amministratore per questo cliente
                dv["configurazione"] += promemoria.blocco_prompt(db, contatto.id)
        # ElevenLabs sostituisce {{configurazione}} ma NON i {{segnaposto}} contenuti dentro:
        # li risolviamo qui, così {{telefono_chiamante}}, {{cliente_conosciuto}}, ecc. arrivano
        # già valorizzati ai tool e nel prompt (altrimenti i tool ricevono il testo letterale).
        _cfg = dv["configurazione"]
        for _k, _v in dv.items():
            if _k != "configurazione":
                _cfg = _cfg.replace("{{" + _k + "}}", str(_v))
        dv["configurazione"] = _cfg
        logger.info("📞 ElevenLabs init: %s | %s", caller or "(sconosciuto)",
                    "AMMINISTRATORE" if admin else ("riconosciuto" if contatto else "nuovo contatto"))
        risposta = {
            "type": "conversation_initiation_client_data",
            "dynamic_variables": dv,
        }
        # Override solo se esplicitamente richiesto E abilitato nell'agente (vedi nota sopra).
        if _INVIA_OVERRIDE:
            risposta["conversation_config_override"] = {
                "agent": {"first_message": first, "language": "it"},
            }
        return risposta
    finally:
        db.close()


def _firma_valida(raw: bytes, header: str, secret: str) -> bool:
    """Verifica la firma HMAC di ElevenLabs (header 't=<ts>,v0=<hmac sha256 di "ts.body">')."""
    try:
        parti = dict(p.split("=", 1) for p in header.split(","))
        ts, sig = parti.get("t", ""), parti.get("v0", "")
        atteso = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(atteso, sig)
    except Exception:
        return False


def _testo_trascrizione(turni: list) -> str:
    righe = []
    for t in (turni or []):
        ruolo = "Cliente" if t.get("role") == "user" else "Assistente"
        msg = (t.get("message") or "").strip()
        if msg:
            righe.append(f"{ruolo}: {msg}")
    return "\n".join(righe)


@router.post("/post-call")
async def post_call(request: Request):
    """Riceve da ElevenLabs, a fine chiamata, la trascrizione + riassunto e li salva
    come ChiamataVoce sul contatto (identificato dal numero passato in init)."""
    raw = await request.body()
    if _POSTCALL_SECRET:
        firma = request.headers.get("elevenlabs-signature", "")
        if not _firma_valida(raw, firma, _POSTCALL_SECRET):
            return JSONResponse({"error": "firma non valida"}, status_code=401)

    try:
        import json
        payload = json.loads(raw or b"{}")
    except Exception:
        return JSONResponse({"error": "payload non valido"}, status_code=400)

    # Ci interessa solo la trascrizione completa.
    if payload.get("type") != "post_call_transcription":
        return {"ok": True, "ignorato": payload.get("type")}

    data = payload.get("data") or {}
    meta = data.get("metadata") or {}
    dvars = ((data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {})
    telefono = (dvars.get("telefono_chiamante") or meta.get("phone_number") or "").strip()

    trascr = _testo_trascrizione(data.get("transcript") or [])
    riassunto = ((data.get("analysis") or {}).get("transcript_summary") or "").strip() or None
    durata = meta.get("call_duration_secs")
    inizio = meta.get("start_time_unix_secs")
    iniziata_at = datetime.utcfromtimestamp(inizio) if inizio else datetime.utcnow()

    db = SessionLocal()
    try:
        contatto = whatsapp_agent.trova_o_crea_contatto(db, telefono or "sconosciuto")
        db.add(ChiamataVoce(
            contatto_id=contatto.id, telefono=telefono or None,
            iniziata_at=iniziata_at, durata_sec=int(durata) if durata else None,
            trascrizione=trascr or None, riassunto=riassunto,
        ))
        db.commit()
        logger.info("ElevenLabs post-call: chiamata salvata per %s (%s)", contatto.nome_completo, telefono)
        return {"ok": True, "contatto_id": contatto.id}
    finally:
        db.close()
