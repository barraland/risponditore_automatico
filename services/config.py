"""Lettura/scrittura delle impostazioni tecniche nel file .env.

Gestisce solo le chiavi esposte nella schermata Impostazioni della dashboard.
Salvando, riscrive il .env (preservando commenti e altre righe), aggiorna
os.environ e — dove possibile — i valori già caricati nei moduli, così alcune
modifiche (es. mittente email) si applicano senza riavvio.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Percorso del .env (radice del progetto).
ENV_PATH = os.getenv("ENV_FILE", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# Chiavi editabili dalla GUI (ordine = ordine di visualizzazione).
CHIAVI = [
    "AGENTE_MODEL",
    "AGENTE_EFFORT",
    "GMAIL_FROM",
    "GMAIL_APP_PASSWORD",
    "OPENAI_API_KEY",
    "WHATSAPP_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_VERIFY_TOKEN",
    "NGROK_AUTHTOKEN",
]

# Valori di default mostrati quando la chiave non è nel .env.
DEFAULTS = {
    "AGENTE_MODEL": "gpt-5-mini",
    "AGENTE_EFFORT": "medium",
}

EFFORT_VALIDI = ["minimal", "low", "medium", "high"]

# Chiavi il cui valore è segreto (mascherato nelle stampe / input password).
SEGRETE = {
    "GMAIL_APP_PASSWORD", "OPENAI_API_KEY", "WHATSAPP_TOKEN",
    "WHATSAPP_VERIFY_TOKEN", "NGROK_AUTHTOKEN",
}


def leggi() -> dict:
    """Valori correnti (da os.environ, con fallback ai default) delle chiavi gestite."""
    return {k: (os.getenv(k) or DEFAULTS.get(k, "")) for k in CHIAVI}


def maschera(val: str) -> str:
    """Rappresentazione mascherata di un segreto (per anteprima)."""
    if not val:
        return ""
    if len(val) <= 8:
        return "•" * len(val)
    return f"{val[:4]}…{val[-4:]}"


def _quota(val: str) -> str:
    """Mette tra virgolette i valori con spazi (es. app password Gmail)."""
    if val and (" " in val or '"' in val):
        return '"' + val.replace('"', '\\"') + '"'
    return val


def aggiorna(updates: dict) -> None:
    """Riscrive le chiavi nel .env, aggiorna os.environ e i moduli a caldo."""
    updates = {k: (v or "").strip() for k, v in updates.items() if k in CHIAVI}

    righe = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            righe = f.read().splitlines()

    viste = set()
    out = []
    for riga in righe:
        s = riga.strip()
        if s and not s.startswith("#") and "=" in riga:
            chiave = riga.split("=", 1)[0].strip()
            if chiave in updates:
                out.append(f"{chiave}={_quota(updates[chiave])}")
                viste.add(chiave)
                continue
        out.append(riga)
    for k, v in updates.items():
        if k not in viste:
            out.append(f"{k}={_quota(v)}")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")

    for k, v in updates.items():
        os.environ[k] = v
    _applica_live(updates)
    logger.info("Impostazioni aggiornate: %s", ", ".join(updates.keys()))


def _applica_live(updates: dict) -> None:
    """Aggiorna i valori già cacheati nei moduli importati (best-effort)."""
    try:
        from services import agente
        if "AGENTE_MODEL" in updates and updates["AGENTE_MODEL"]:
            agente.MODEL = updates["AGENTE_MODEL"]
        if "AGENTE_EFFORT" in updates and updates["AGENTE_EFFORT"]:
            agente.EFFORT = updates["AGENTE_EFFORT"]
    except Exception:
        pass
    try:
        from services import email as email_service
        if "GMAIL_FROM" in updates:
            email_service.GMAIL_FROM = updates["GMAIL_FROM"]
        if "GMAIL_APP_PASSWORD" in updates:
            email_service.GMAIL_APP_PASSWORD = updates["GMAIL_APP_PASSWORD"]
    except Exception:
        pass
    try:
        from services import whatsapp as wa
        if "WHATSAPP_TOKEN" in updates:
            wa.WHATSAPP_TOKEN = updates["WHATSAPP_TOKEN"]
        if "WHATSAPP_PHONE_NUMBER_ID" in updates:
            wa.WHATSAPP_PHONE_NUMBER_ID = updates["WHATSAPP_PHONE_NUMBER_ID"]
            wa.GRAPH_API_URL = f"https://graph.facebook.com/v21.0/{updates['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    except Exception:
        pass
    try:
        from routers import webhook
        if "WHATSAPP_VERIFY_TOKEN" in updates:
            webhook.VERIFY_TOKEN = updates["WHATSAPP_VERIFY_TOKEN"]
    except Exception:
        pass


def endpoints(request_host: str | None = None) -> dict:
    """URL pubblici (non editabili) di webhook WhatsApp e voce Twilio.

    Base: NGROK_DOMAIN se impostato; altrimenti l'host pubblico della richiesta
    (se non locale); altrimenti un placeholder.
    """
    dominio = os.getenv("NGROK_DOMAIN", "").strip()
    if dominio:
        base = f"https://{dominio.rstrip('/')}"
    elif request_host and "localhost" not in request_host and "127.0.0.1" not in request_host:
        base = f"https://{request_host}"
    else:
        base = "https://<DOMINIO-NGROK>"
    return {
        "base": base,
        "whatsapp": f"{base}/webhook",
        "voce": f"{base}/voice/incoming",
        "dominio_impostato": bool(dominio),
    }
