"""Inoltro di chiamata su Twilio, indipendente dal motore vocale (ElevenLabs o OpenAI Realtime).

La chiamata gira sempre sul TUO account Twilio, quindi possiamo comandarla via REST: all'inizio
registriamo (telefono -> call_sid, host); quando l'assistente decide l'inoltro, reindirizziamo la
call verso il destinatario con un annuncio vocale + consenso in linguaggio naturale. Niente regole
statiche dentro ElevenLabs: numeri e regole vivono nella dashboard (tabella inoltri)."""

import os
import re
import logging
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
SAY_VOICE = os.getenv("TWILIO_SAY_VOICE", "Polly.Bianca")  # voce italiana per gli annunci Twilio

# Registro in memoria delle chiamate vive: telefono normalizzato -> {call_sid, host, numero_twilio}.
# Popolato a inizio chiamata (ElevenLabs init / Realtime stream start). Single-worker (demo).
_chiamate: dict[str, dict] = {}


def _norm(t: str) -> str:
    return re.sub(r"\D", "", t or "")


def registra_chiamata(telefono: str, call_sid: str, host: str, numero_twilio: str = "") -> None:
    if telefono and call_sid:
        _chiamate[_norm(telefono)] = {"call_sid": call_sid, "host": host or "", "numero_twilio": numero_twilio or ""}
        logger.info("📇 Chiamata registrata per inoltro: %s (call_sid=%s, twilio=%s)",
                    telefono, call_sid, numero_twilio or "?")


def dati_chiamata(telefono: str) -> dict:
    return _chiamate.get(_norm(telefono), {})


def xml_escape(s: str) -> str:
    return (s or "").replace("&", " e ").replace("<", " ").replace(">", " ").replace('"', "'")


def avvia_inoltro(call_sid: str, numero: str, riepilogo: str, host: str, caller_id: str = "") -> tuple[bool, str]:
    """Reindirizza la chiamata Twilio in corso verso `numero`, con whisper di annuncio+consenso.
    Funziona identico per ElevenLabs e Realtime. `caller_id` = numero Twilio da mostrare in uscita
    (necessario per chiamare numeri esteri: non si può presentare il numero del chiamante). (ok, errore)."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
        return False, "Twilio REST non configurato (TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN)."
    if not (call_sid and numero and host):
        return False, "Dati inoltro mancanti (call_sid/host non disponibili per questa chiamata)."
    whisper = f"https://{host}/voice/inoltro-whisper?msg={urllib.parse.quote(riepilogo or 'Le passo una chiamata.')}"
    esito = f"https://{host}/voice/inoltro-esito"
    cid = f' callerId="{caller_id}"' if caller_id else ""
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Dial answerOnBridge="true" timeout="25" action="{esito}"{cid}>'
        f'<Number url="{whisper}">{numero}</Number></Dial></Response>'
    )
    logger.info("➡️  Inoltro: dial %s callerId=%s (call_sid=%s)", numero, caller_id or "(default)", call_sid)
    return _update_call(call_sid, twiml)


def _update_call(call_sid: str, twiml: str) -> tuple[bool, str]:
    """Sostituisce il TwiML in esecuzione su una chiamata Twilio viva. (ok, errore)."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
        return False, "Twilio REST non configurato (TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN)."
    if not call_sid:
        return False, "call_sid mancante per la chiamata."
    try:
        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls/{urllib.parse.quote(call_sid)}.json",
            data={"Twiml": twiml}, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=10,
        )
        if r.status_code in (200, 201):
            return True, ""
        # Diagnostica senza svelare il segreto: SID in chiaro (è un identificativo) + lunghezza token.
        logger.warning("Twilio update %s | SID usato=%s len(token)=%d | %s",
                       r.status_code, TWILIO_ACCOUNT_SID or "(vuoto)", len(TWILIO_AUTH_TOKEN), r.text[:160])
        return False, f"Twilio {r.status_code}: {r.text[:160]}"
    except Exception as e:
        return False, str(e)


def in_conferenza(call_sid: str, stanza: str) -> tuple[bool, str]:
    """Sposta una chiamata viva dentro una conference Twilio (per unire due gambe). Quando una
    delle due esce, la conference termina (endConferenceOnExit)."""
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response><Dial>'
        f'<Conference startConferenceOnEnter="true" endConferenceOnExit="true" beep="false" '
        f'waitUrl="">{stanza}</Conference></Dial></Response>'
    )
    return _update_call(call_sid, twiml)


# ---------- Interpretazione del consenso in linguaggio naturale (Twilio SpeechResult) ----------
_PAROLE_SI = {"sì", "si", "certo", "ok", "okay", "accetto", "passa", "passamelo", "passamela",
              "volentieri", "perfetto", "sicuro", "yes", "vai", "certamente", "assolutamente"}
_PAROLE_NO = {"no", "occupato", "impegnato", "negativo", "nope"}
_FRASI_SI = ("va bene", "d'accordo", "ci sono", "me lo passi", "lo passi", "fammi parlare")
_FRASI_NO = ("non posso", "più tardi", "richiamo", "non ora", "sono occupato", "in riunione",
             "non riesco", "magari dopo", "richiamami")


def consenso_positivo(testo: str) -> bool | None:
    """True = accetta, False = rifiuta, None = non chiaro (chiedi di nuovo)."""
    t = (testo or "").lower()
    parole = set(re.findall(r"[a-zàèéìòù']+", t))
    pos = bool(parole & _PAROLE_SI) or any(f in t for f in _FRASI_SI)
    neg = bool(parole & _PAROLE_NO) or any(f in t for f in _FRASI_NO)
    if pos and not neg:
        return True
    if neg and not pos:
        return False
    if pos and neg:
        return True   # es. "sì, ma fai veloce" → accetta
    return None
