"""Endpoint OAuth 2.0 per collegare Google Calendar.

- /google/connect  → APERTO: reindirizza l'utente alla schermata di consenso Google.
- /google/callback → APERTO: riceve il code, salva i token, torna alla SPA.
- /google/status   → autenticato (token Supabase): stato della connessione, per la SPA.
- /google/disconnect → autenticato: scollega.
"""

import os
import logging

from fastapi import APIRouter, Request, Depends, Header, Query, Body
from fastapi.responses import RedirectResponse, HTMLResponse

from database import SessionLocal
from services import google_calendar as gc
from routers.api_documenti import _verify_user   # riusa la verifica del token Supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/google")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _spa_base() -> str:
    base = os.getenv("SPA_BASE_URL", "").strip().rstrip("/")
    if base:
        return base
    for o in os.getenv("CORS_ORIGINS", "").split(","):
        o = o.strip().rstrip("/")
        if o.startswith("https://"):
            return o
    return ""


@router.get("/connect")
async def connect(request: Request, azienda_id: int = Query(0)):
    if not gc.configurato():
        return HTMLResponse("Google OAuth non configurato (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).",
                            status_code=500)
    host = request.headers.get("host", request.url.hostname)
    return RedirectResponse(gc.url_consenso(host, azienda_id or None))


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"Connessione annullata: {error}")
    ok, azienda_id = gc.consuma_state(state)
    if not ok:
        return HTMLResponse("Sessione di connessione scaduta o non valida: riprova dalla dashboard.",
                            status_code=400)
    host = request.headers.get("host", request.url.hostname)
    try:
        email = gc.scambia_e_salva(code, host, azienda_id)
    except Exception as e:
        logger.error("Callback Google fallito: %s", e)
        return HTMLResponse(f"Errore nella connessione a Google: {e}", status_code=500)
    base = _spa_base()
    if base:
        return RedirectResponse(f"{base}/calendario?connected=1")
    return HTMLResponse(f"<h3>Google connesso come {email or 'account Google'}. "
                        "Può chiudere questa scheda.</h3>")


@router.get("/status")
async def status(azienda_id: int = Query(0), authorization: str | None = Header(None), db=Depends(get_db)):
    await _verify_user(authorization)
    return gc.stato(db, azienda_id or None)


@router.post("/disconnect")
async def disconnect(azienda_id: int = Query(0), authorization: str | None = Header(None), db=Depends(get_db)):
    await _verify_user(authorization)
    gc.disconnetti(db, azienda_id or None)
    return {"ok": True}


@router.get("/events")
async def events(da: str = Query(...), a: str = Query(...),
                 authorization: str | None = Header(None), db=Depends(get_db)):
    """Eventi tra `da` e `a` (ISO/RFC3339). Usato dalla vista calendario in dashboard."""
    await _verify_user(authorization)
    return {"eventi": gc.eventi(db, da, a)}


@router.get("/calendari")
async def calendari(azienda_id: int = Query(0), authorization: str | None = Header(None),
                    db=Depends(get_db)):
    """Elenco dei calendari dell'account Google connesso (per il menù a tendina)."""
    await _verify_user(authorization)
    return {"calendari": gc.calendari(db, azienda_id or None)}


@router.post("/calendario")
async def imposta_calendario(payload: dict = Body(...), azienda_id: int = Query(0),
                             authorization: str | None = Header(None), db=Depends(get_db)):
    """Imposta il calendario attivo (id passato nel body: {"calendar_id": "..."})."""
    await _verify_user(authorization)
    ok = gc.imposta_calendario(db, azienda_id or None, (payload or {}).get("calendar_id", ""))
    return {"ok": ok}
