"""Risoluzione del TENANT (azienda) per la multi-tenancy.

Un solo backend/DB ospita più clienti (aziende). Ogni canale identifica il tenant:
- VOCE: dal NUMERO CHIAMATO (ElevenLabs called_number / Twilio "to") → azienda.numeri_voce.
- WHATSAPP: dal phone_number_id nel webhook Meta → azienda.whatsapp_phone_id.
- TOOL MCP: l'agente passa la dynamic variable {{tenant}} = azienda.id (iniettata all'init).
- DASHBOARD: (prossimo step) utente Supabase → tenant, con RLS per-tenant.

Finché la migrazione non è completa, se non si risolve nulla si ricade sull'UNICA azienda
(compatibilità single-tenant)."""

import re
import logging

from sqlalchemy.orm import Session

from database import Azienda

logger = logging.getLogger(__name__)


def _norm(t: str) -> str:
    return re.sub(r"\D", "", t or "")


def default(db: Session) -> Azienda | None:
    """Fallback single-tenant: l'unica azienda (o la prima)."""
    return db.query(Azienda).first()


def da_id(db: Session, tenant) -> Azienda | None:
    try:
        return db.get(Azienda, int(tenant)) if tenant not in (None, "") else None
    except (TypeError, ValueError):
        return None


def da_numero_voce(db: Session, numero: str) -> Azienda | None:
    """Tenant dal numero chiamato (voce). Confronto sulle cifre finali per tollerare i prefissi."""
    n = _norm(numero)
    if not n:
        return None
    for az in db.query(Azienda).filter(Azienda.numeri_voce.isnot(None)).all():
        for cand in re.split(r"[,;\s]+", az.numeri_voce or ""):
            c = _norm(cand)
            if c and (c == n or c.endswith(n) or n.endswith(c)):
                return az
    return None


def da_whatsapp(db: Session, phone_id: str) -> Azienda | None:
    pid = (phone_id or "").strip()
    if not pid:
        return None
    return db.query(Azienda).filter(Azienda.whatsapp_phone_id == pid).first()


def risolvi(db: Session, tenant=None, numero_chiamato: str = "", whatsapp_phone_id: str = "") -> Azienda | None:
    """Prova nell'ordine: id esplicito → numero voce → phone_id WhatsApp → fallback all'unica azienda."""
    return (da_id(db, tenant)
            or da_numero_voce(db, numero_chiamato)
            or da_whatsapp(db, whatsapp_phone_id)
            or default(db))
