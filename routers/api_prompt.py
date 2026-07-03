"""API per l'editor del prompt vocale MODULARE (pagina 'Prompt' della SPA).

La SPA legge/salva i moduli via queste rotte (non via Supabase diretto), così i testi DEFAULT
restano nel backend e l'assemblaggio è unico. Auth: stesso bearer Supabase degli altri endpoint.
"""

import logging

from fastapi import APIRouter, Depends, Header, Body
from sqlalchemy.orm import Session

from database import SessionLocal
from services import prompt_moduli, tenant as tenant_service
from routers.api_documenti import _verify_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/prompt")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _aid(db: Session, payload: dict) -> int | None:
    az = tenant_service.risolvi(db, tenant=payload.get("azienda_id"))
    return az.id if az else None


@router.post("/moduli")
async def lista_moduli(payload: dict = Body(default={}), authorization: str | None = Header(None),
                       db: Session = Depends(get_db)):
    """Moduli effettivi del tenant (default + override) + anteprima del prompt assemblato."""
    await _verify_user(authorization)
    aid = _aid(db, payload)
    return {"azienda_id": aid, "moduli": prompt_moduli.effettivi(db, aid),
            "anteprima": prompt_moduli.componi(db, aid).strip()}


@router.post("/modulo")
async def salva_modulo(payload: dict = Body(...), authorization: str | None = Header(None),
                       db: Session = Depends(get_db)):
    """Salva l'override di un modulo (testo/attivo/ordine/titolo) per il tenant."""
    await _verify_user(authorization)
    aid = _aid(db, payload)
    chiave = (payload.get("chiave") or "").strip()
    if not aid or not chiave:
        return {"ok": False, "errore": "azienda_id e chiave obbligatori"}
    prompt_moduli.salva(db, aid, chiave, titolo=payload.get("titolo"), ordine=payload.get("ordine"),
                        attivo=payload.get("attivo"), testo=payload.get("testo"))
    return {"ok": True}


@router.post("/modulo/reset")
async def reset_modulo(payload: dict = Body(...), authorization: str | None = Header(None),
                       db: Session = Depends(get_db)):
    """Rimuove l'override: il modulo torna al default (o sparisce se era custom)."""
    await _verify_user(authorization)
    aid = _aid(db, payload)
    chiave = (payload.get("chiave") or "").strip()
    if aid and chiave:
        prompt_moduli.reset(db, aid, chiave)
    return {"ok": True}
