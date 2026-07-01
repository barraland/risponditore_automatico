"""Inoltro ASSISTITO da un secondo agente ElevenLabs.

Flusso:
1. L'agente entrante (Margherita, con Andrea in linea) chiama il tool `chiama_persona`.
2. Qui lanciamo una chiamata in USCITA ElevenLabs verso il destinatario (Valerio), con un secondo
   agente che gli chiede a voce se può ricevere la chiamata. Gli passiamo il contesto come
   dynamic variables (chi chiama, perché, e `sessione` = numero del destinatario per ricollegare).
3. Se Valerio accetta, l'agente outbound chiama `unisci_chiamate` -> uniamo le due gambe in una
   conference Twilio (i due agenti escono, restano gli umani).
4. Se rifiuta / è una segreteria, chiama `rifiuta_inoltro` con il motivo.
5. Margherita intanto chiede l'esito con `attendi_esito` e prosegue di conseguenza.

Stato in memoria (single worker, demo), una sessione attiva per numero entrante."""

import os
import re
import time
import logging
from datetime import datetime

import httpx

from services import telefonia

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
EL_OUTBOUND_AGENT_ID = os.getenv("ELEVENLABS_OUTBOUND_AGENT_ID", "").strip()
EL_OUTBOUND_PHONE_ID = os.getenv("ELEVENLABS_OUTBOUND_PHONE_ID", "").strip()
EL_OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
TIMEOUT_RISPOSTA = 40  # s: oltre questo, se ancora "in_corso", lo trattiamo come "non_risponde"

# numero entrante normalizzato -> sessione
_sessioni: dict[str, dict] = {}


def _norm(t: str) -> str:
    return re.sub(r"\D", "", t or "")


_SALUTI = r"(ciao|salve|buongiorno|buonasera|buon pomeriggio|pronto|ehi|hey)"


def _saluta_per_nome(apertura: str, nome_completo: str) -> str:
    """Personalizza il saluto col nome di battesimo del destinatario: 'Buongiorno Valerio, ...'.
    Se la frase inizia già con un saluto, ci infila il nome; altrimenti lo antepone."""
    nome = (nome_completo or "").strip().split()[0] if (nome_completo or "").strip() else ""
    if not nome:
        return apertura
    # Se il nome è già presente all'inizio (l'agente inbound l'ha già messo), NON duplicarlo.
    if nome.lower() in apertura[:40].lower():
        return apertura
    m = re.match(rf"^\s*{_SALUTI}\b[\s,!.]*", apertura, re.I)
    if m:
        resto = apertura[m.end():].lstrip()
        return f"{m.group(1).capitalize()} {nome}, {resto}".strip()
    return f"Ciao {nome}! {apertura}"


def configurato() -> bool:
    return bool(ELEVENLABS_API_KEY and EL_OUTBOUND_AGENT_ID and EL_OUTBOUND_PHONE_ID)


def _sessione_per_dest(dest_tel: str) -> dict | None:
    n = _norm(dest_tel)
    if not n:
        return None
    cand = [s for s in _sessioni.values() if _norm(s.get("dest_tel")) == n]
    return max(cand, key=lambda s: s["created_at"]) if cand else None


def avvia(entrante_tel: str, entrante_call_sid: str, entrante_host: str,
          dest, chiamante: str, motivo: str, apertura: str = "") -> tuple[bool, str]:
    """Lancia la chiamata in uscita verso `dest` (riga Inoltro). `apertura` = frase parlata che
    l'agente outbound dirà per prima (First message). Ritorna (ok, errore)."""
    if not configurato():
        return False, "Inoltro assistito non configurato (ELEVENLABS_API_KEY/AGENT_ID/PHONE_ID)."
    if not entrante_call_sid:
        return False, "Manca il call_sid della chiamata entrante (registro non popolato)."

    stanza = "inoltro-" + (_norm(entrante_call_sid) or _norm(entrante_tel))
    motivo = (motivo or "").strip() or "una richiesta del cliente"
    apertura = (apertura or "").strip() or (
        f"Salve! Ho in linea {chiamante or 'un cliente'} che vorrebbe parlarle. "
        "Posso passarglielo, oppure le dico che ora è occupato?"
    )
    apertura = _saluta_per_nome(apertura, dest.nome_completo)  # 'Buongiorno Valerio, ...'
    dvars = {
        "chiamante": chiamante or "un cliente",
        "motivo": motivo,
        "destinatario": dest.nome_completo,
        "apertura": apertura,
        "sessione": _norm(dest.telefono),
    }
    body = {
        "agent_id": EL_OUTBOUND_AGENT_ID,
        "agent_phone_number_id": EL_OUTBOUND_PHONE_ID,
        "to_number": dest.telefono,
        "conversation_initiation_client_data": {"dynamic_variables": dvars},
    }
    try:
        r = httpx.post(EL_OUTBOUND_URL, headers={"xi-api-key": ELEVENLABS_API_KEY}, json=body, timeout=15)
    except Exception as e:
        return False, f"Errore chiamata ElevenLabs: {e}"
    logger.info("📤 Outbound ElevenLabs -> %s | HTTP %s | %s", dest.telefono, r.status_code, r.text[:200])
    if r.status_code not in (200, 201):
        return False, f"ElevenLabs {r.status_code}: {r.text[:160]}"
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    dest_call_sid = data.get("callSid") or data.get("call_sid") or ""

    _sessioni[_norm(entrante_tel)] = {
        "stato": "in_corso",
        "entrante_tel": entrante_tel, "entrante_call_sid": entrante_call_sid, "entrante_host": entrante_host,
        "dest_tel": dest.telefono, "dest_nome": dest.nome_completo, "dest_call_sid": dest_call_sid,
        "conversation_id": data.get("conversation_id", ""),
        "stanza": stanza, "motivo": motivo, "chiamante": chiamante,
        "created_at": datetime.utcnow(), "dettaglio": "",
    }
    logger.info("🟡 Sessione inoltro avviata: %s -> %s (stanza=%s, dest_call_sid=%s)",
                entrante_tel, dest.telefono, stanza, dest_call_sid or "?")
    return True, ""


def _esito(entrante_tel: str) -> dict:
    s = _sessioni.get(_norm(entrante_tel))
    if not s:
        return {"stato": "nessuno"}
    if s["stato"] == "in_corso" and (datetime.utcnow() - s["created_at"]).total_seconds() > TIMEOUT_RISPOSTA:
        s["stato"] = "non_risponde"
    return {"stato": s["stato"], "destinatario": s["dest_nome"], "dettaglio": s.get("dettaglio", "")}


def attendi_esito(entrante_tel: str, max_attesa: int = 10) -> dict:
    """Restituisce l'esito; attende fino a `max_attesa` secondi se ancora in corso."""
    deadline = time.time() + max_attesa
    while time.time() < deadline:
        e = _esito(entrante_tel)
        if e["stato"] != "in_corso":
            return e
        time.sleep(0.5)
    return _esito(entrante_tel)


def accetta(sessione_o_dest: str) -> tuple[bool, str]:
    """Valerio ACCETTA: unisce entrante e destinatario nella stessa conference."""
    s = _sessione_per_dest(sessione_o_dest)
    if not s:
        return False, "Sessione di inoltro non trovata."
    if not s.get("dest_call_sid"):
        return False, "Manca il call_sid del destinatario (l'outbound non l'ha restituito)."
    ok1, e1 = telefonia.in_conferenza(s["entrante_call_sid"], s["stanza"])
    ok2, e2 = telefonia.in_conferenza(s["dest_call_sid"], s["stanza"])
    s["stato"] = "accettato"
    if ok1 and ok2:
        logger.info("🟢 Inoltro accettato e unito: stanza=%s", s["stanza"])
        return True, ""
    return False, f"Merge parziale: entrante={e1 or 'ok'}, dest={e2 or 'ok'}"


def rifiuta(sessione_o_dest: str, motivo: str = "") -> tuple[bool, str]:
    """Valerio NON può ricevere ora (rifiuto o segreteria)."""
    s = _sessione_per_dest(sessione_o_dest)
    if not s:
        return False, "Sessione di inoltro non trovata."
    s["stato"] = "rifiutato"
    s["dettaglio"] = (motivo or "").strip()
    logger.info("🔴 Inoltro rifiutato: dest=%s motivo=%s", s["dest_nome"], motivo or "-")
    return True, ""
