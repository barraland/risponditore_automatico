"""Connessione OAuth 2.0 a Google Calendar.

Step 1 (questo file): il flusso di consenso — l'utente clicca "Connetti", va su Google, accetta,
e noi salviamo access_token + refresh_token nel DB. Poi possiamo creare eventi a suo nome.
Step 2 (dopo): i tool per prenotare i meeting useranno `access_token_valido()`.

Il refresh_token è un segreto lungo-vivo: vive SOLO qui nel backend, mai nel frontend."""

import os
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta

import httpx

from database import SessionLocal, GoogleCalendar

logger = logging.getLogger(__name__)

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
SCOPES = "openid email https://www.googleapis.com/auth/calendar.events"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# state anti-CSRF, in memoria (single worker, demo)
_states: dict[str, datetime] = {}


def configurato() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def _redirect_uri(host: str) -> str:
    return f"https://{host}/google/callback"


def url_consenso(host: str) -> str:
    """URL della schermata di consenso Google (con access_type=offline per avere il refresh token)."""
    state = secrets.token_urlsafe(24)
    _states[state] = datetime.utcnow()
    # pulizia state vecchi
    for s, t in list(_states.items()):
        if (datetime.utcnow() - t).total_seconds() > 600:
            _states.pop(s, None)
    q = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "redirect_uri": _redirect_uri(host), "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state,
    })
    return f"{AUTH_URL}?{q}"


def valida_state(state: str) -> bool:
    ok = state in _states
    _states.pop(state, None)
    return ok


def scambia_e_salva(code: str, host: str) -> str:
    """Scambia il code con i token, recupera l'email e salva tutto. Ritorna l'email connessa."""
    r = httpx.post(TOKEN_URL, data={
        "code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "redirect_uri": _redirect_uri(host), "grant_type": "authorization_code",
    }, timeout=15)
    r.raise_for_status()
    tok = r.json()
    access = tok["access_token"]
    refresh = tok.get("refresh_token")
    expires = int(tok.get("expires_in", 3600))

    email = ""
    try:
        ui = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"}, timeout=10)
        if ui.status_code == 200:
            email = ui.json().get("email", "")
    except Exception as e:
        logger.warning("Userinfo Google non recuperato: %s", e)

    db = SessionLocal()
    try:
        row = db.query(GoogleCalendar).first()
        if not row:
            row = GoogleCalendar()
            db.add(row)
        row.email = email
        row.calendar_id = "primary"
        row.access_token = access
        if refresh:  # arriva solo al primo consenso; non sovrascrivere con vuoto
            row.refresh_token = refresh
        row.scad = datetime.utcnow() + timedelta(seconds=expires - 60)
        row.connesso_at = datetime.utcnow()
        db.commit()
        logger.info("📅 Google Calendar connesso: %s", email or "(email n/d)")
        return email
    finally:
        db.close()


def stato(db) -> dict:
    row = db.query(GoogleCalendar).first()
    if not row or not row.refresh_token:
        return {"connesso": False}
    return {"connesso": True, "email": row.email, "calendar_id": row.calendar_id,
            "connesso_at": row.connesso_at.isoformat() if row.connesso_at else None}


def disconnetti(db) -> None:
    db.query(GoogleCalendar).delete()
    db.commit()


def eventi(db, time_min: str, time_max: str, max_results: int = 100) -> list[dict]:
    """Eventi del calendario connesso tra time_min e time_max (RFC3339). Lista vuota se non connesso."""
    access = access_token_valido(db)
    if not access:
        return []
    row = db.query(GoogleCalendar).first()
    cal = (row.calendar_id if row else "primary") or "primary"
    try:
        r = httpx.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(cal)}/events",
            headers={"Authorization": f"Bearer {access}"},
            params={"timeMin": time_min, "timeMax": time_max, "singleEvents": "true",
                    "orderBy": "startTime", "maxResults": max_results}, timeout=15,
        )
    except Exception as e:
        logger.warning("Lettura eventi Google errore: %s", e)
        return []
    if r.status_code != 200:
        logger.warning("Lettura eventi Google %s: %s", r.status_code, r.text[:160])
        return []
    out = []
    for e in r.json().get("items", []):
        start, end = e.get("start", {}), e.get("end", {})
        out.append({
            "id": e.get("id"),
            "titolo": e.get("summary") or "(senza titolo)",
            "inizio": start.get("dateTime") or start.get("date"),
            "fine": end.get("dateTime") or end.get("date"),
            "allday": ("date" in start and "dateTime" not in start),
            "dove": e.get("location") or "",
        })
    return out


TZ_DEFAULT = os.getenv("CALENDAR_TZ", "Europe/Rome")


def crea_evento(db, titolo: str, inizio_iso: str, fine_iso: str, invitati: list[str],
                descrizione: str = "", online: bool = True, tz: str = TZ_DEFAULT) -> dict:
    """Crea un evento sul calendario connesso e INVIA l'invito ai destinatari. Se online=True crea
    anche una Google Meet. `inizio_iso`/`fine_iso` = datetime ISO locale (es. 2026-07-01T16:00:00).
    Ritorna {ok, event_id, link_evento, link_meet, invitati}."""
    access = access_token_valido(db)
    if not access:
        return {"ok": False, "errore": "Google Calendar non connesso."}
    row = db.query(GoogleCalendar).first()
    cal = (row.calendar_id if row else "primary") or "primary"

    body: dict = {
        "summary": titolo or "Meeting",
        "description": descrizione or "",
        "start": {"dateTime": inizio_iso, "timeZone": tz},
        "end": {"dateTime": fine_iso, "timeZone": tz},
        "attendees": [{"email": e} for e in (invitati or []) if e],
    }
    params = {"sendUpdates": "all"}   # invia davvero le email di invito
    if online:
        body["conferenceData"] = {"createRequest": {
            "requestId": secrets.token_hex(16),
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }}
        params["conferenceDataVersion"] = 1
    try:
        r = httpx.post(
            f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(cal)}/events",
            headers={"Authorization": f"Bearer {access}"}, params=params, json=body, timeout=20,
        )
    except Exception as e:
        return {"ok": False, "errore": f"Errore Google: {e}"}
    if r.status_code not in (200, 201):
        logger.warning("Creazione evento Google %s: %s", r.status_code, r.text[:200])
        return {"ok": False, "errore": f"Google {r.status_code}: {r.text[:160]}"}
    ev = r.json()
    meet = ev.get("hangoutLink") or ""
    if not meet:
        for ep in (ev.get("conferenceData", {}).get("entryPoints") or []):
            if ep.get("entryPointType") == "video":
                meet = ep.get("uri", "")
                break
    logger.info("📅 Evento creato '%s' (%s) invitati=%s meet=%s", titolo, ev.get("id"),
                len(body["attendees"]), bool(meet))
    return {"ok": True, "event_id": ev.get("id"), "link_evento": ev.get("htmlLink", ""),
            "link_meet": meet, "invitati": [a["email"] for a in body["attendees"]]}


def _parse_iso(s: str):
    from datetime import datetime
    return datetime.fromisoformat((s or "").replace("Z", "+00:00"))


def disponibilita(db, giorno: str, durata_min: int = 30, ora_inizio: int = 9, ora_fine: int = 18,
                  tz: str = TZ_DEFAULT, max_slot: int = 6) -> dict:
    """Slot liberi in un giorno (freeBusy), nell'orario lavorativo [ora_inizio, ora_fine].
    `giorno` = 'YYYY-MM-DD'. Ritorna {ok, giorno, slot_liberi:[...], occupato:bool}."""
    from datetime import datetime, timedelta, time, date
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
    except Exception:
        zone = None
    access = access_token_valido(db)
    if not access:
        return {"ok": False, "errore": "Google Calendar non connesso."}
    row = db.query(GoogleCalendar).first()
    cal = (row.calendar_id if row else "primary") or "primary"
    try:
        d = date.fromisoformat(giorno.strip()[:10])
    except ValueError:
        return {"ok": False, "errore": "giorno non valido: usa YYYY-MM-DD."}

    inizio = datetime.combine(d, time(ora_inizio, 0), tzinfo=zone)
    fine = datetime.combine(d, time(ora_fine, 0), tzinfo=zone)
    # Occupato = dagli EVENTI del giorno (events.list: ok con scope calendar.events, niente freeBusy).
    try:
        r = httpx.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(cal)}/events",
            headers={"Authorization": f"Bearer {access}"},
            params={"timeMin": inizio.isoformat(), "timeMax": fine.isoformat(),
                    "singleEvents": "true", "orderBy": "startTime", "maxResults": 50}, timeout=15)
    except Exception as e:
        return {"ok": False, "errore": f"Errore Google: {e}"}
    if r.status_code != 200:
        return {"ok": False, "errore": f"Google {r.status_code}: {r.text[:160]}"}
    occupati = []
    for e in r.json().get("items", []):
        st, en = e.get("start", {}), e.get("end", {})
        if "dateTime" in st and "dateTime" in en:
            occupati.append((_parse_iso(st["dateTime"]), _parse_iso(en["dateTime"])))
        elif "date" in st:  # evento tutto il giorno → giornata occupata
            return {"ok": True, "giorno": d.isoformat(), "slot_liberi": [], "occupato": True}

    durata = timedelta(minutes=int(durata_min or 30))
    liberi, s = [], inizio
    while s + durata <= fine and len(liberi) < max_slot:
        e = s + durata
        if not any(s < be and e > bs for bs, be in occupati):
            liberi.append(f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}")
        s += durata
    return {"ok": True, "giorno": d.isoformat(), "slot_liberi": liberi, "occupato": not liberi}


def disponibilita_settimana(db, giorni: int = 7, durata_min: int = 30, ora_inizio: int = 9,
                            ora_fine: int = 18, tz: str = TZ_DEFAULT) -> dict:
    """Liberi + occupati per i prossimi `giorni` giorni (oggi incluso), orario [ora_inizio, ora_fine].
    Una sola chiamata a Google. Ritorna {ok, giorni:[{giorno, slot_liberi, occupati}]}."""
    from datetime import datetime, timedelta, time
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
    except Exception:
        zone = None
    access = access_token_valido(db)
    if not access:
        return {"ok": False, "errore": "Google Calendar non connesso."}
    row = db.query(GoogleCalendar).first()
    cal = (row.calendar_id if row else "primary") or "primary"

    oggi = datetime.now(zone).date()
    finestra_ini = datetime.combine(oggi, time(0, 0), tzinfo=zone)
    finestra_fin = datetime.combine(oggi + timedelta(days=giorni), time(0, 0), tzinfo=zone)
    try:
        r = httpx.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(cal)}/events",
            headers={"Authorization": f"Bearer {access}"},
            params={"timeMin": finestra_ini.isoformat(), "timeMax": finestra_fin.isoformat(),
                    "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}, timeout=20)
    except Exception as e:
        return {"ok": False, "errore": f"Errore Google: {e}"}
    if r.status_code != 200:
        return {"ok": False, "errore": f"Google {r.status_code}: {r.text[:160]}"}

    timed, allday_days = [], set()
    for e in r.json().get("items", []):
        st, en = e.get("start", {}), e.get("end", {})
        if "dateTime" in st and "dateTime" in en:
            timed.append((_parse_iso(st["dateTime"]), _parse_iso(en["dateTime"])))
        elif "date" in st:  # evento tutto il giorno
            allday_days.add(st["date"])

    durata = timedelta(minutes=int(durata_min or 30))
    out = []
    for i in range(giorni):
        d = oggi + timedelta(days=i)
        inizio = datetime.combine(d, time(ora_inizio, 0), tzinfo=zone)
        fine = datetime.combine(d, time(ora_fine, 0), tzinfo=zone)
        if d.isoformat() in allday_days:
            out.append({"giorno": d.isoformat(), "slot_liberi": [], "occupati": ["tutto il giorno"]})
            continue
        occ = [(max(bs, inizio), min(be, fine)) for bs, be in timed if bs < fine and be > inizio]
        occupati = [f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for s, e in sorted(occ)]
        liberi, s = [], inizio
        while s + durata <= fine:
            e = s + durata
            if not any(s < be and e > bs for bs, be in occ):
                liberi.append(f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}")
            s += durata
        out.append({"giorno": d.isoformat(), "slot_liberi": liberi, "occupati": occupati})
    return {"ok": True, "giorni": out}


def access_token_valido(db) -> str | None:
    """Access token valido, rinnovato col refresh token se scaduto. None se non connesso. (Step 2)."""
    row = db.query(GoogleCalendar).first()
    if not row or not row.refresh_token:
        return None
    if row.scad and row.scad > datetime.utcnow() and row.access_token:
        return row.access_token
    try:
        r = httpx.post(TOKEN_URL, data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "refresh_token": row.refresh_token, "grant_type": "refresh_token",
        }, timeout=15)
        if r.status_code != 200:
            logger.warning("Refresh token Google fallito: %s", r.text[:160])
            return None
        tok = r.json()
        row.access_token = tok["access_token"]
        row.scad = datetime.utcnow() + timedelta(seconds=int(tok.get("expires_in", 3600)) - 60)
        db.commit()
        return row.access_token
    except Exception as e:
        logger.warning("Refresh token Google errore: %s", e)
        return None
