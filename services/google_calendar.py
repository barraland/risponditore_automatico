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
# Una sola connessione Google per tenant copre Calendar + elenco calendari + invio email.
SCOPES = ("openid email https://www.googleapis.com/auth/calendar.events "
          "https://www.googleapis.com/auth/calendar.calendarlist.readonly "
          "https://www.googleapis.com/auth/gmail.send")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# state anti-CSRF → azienda_id del tenant che sta connettendo (in memoria, single worker, demo)
_states: dict[str, tuple[datetime, int | None]] = {}


def _row(db, azienda_id: int | None = None):
    """Riga di connessione del tenant (per azienda_id); senza azienda_id, la prima (single-tenant)."""
    q = db.query(GoogleCalendar)
    if azienda_id:
        q = q.filter(GoogleCalendar.azienda_id == azienda_id)
    return q.first()


def configurato() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def _redirect_uri(host: str) -> str:
    return f"https://{host}/google/callback"


def url_consenso(host: str, azienda_id: int | None = None) -> str:
    """URL della schermata di consenso Google (access_type=offline per il refresh token). Lo `state`
    memorizza il tenant che sta connettendo, così il callback salva la riga per l'azienda giusta."""
    state = secrets.token_urlsafe(24)
    _states[state] = (datetime.utcnow(), azienda_id)
    for s, (t, _a) in list(_states.items()):  # pulizia state vecchi
        if (datetime.utcnow() - t).total_seconds() > 600:
            _states.pop(s, None)
    q = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "redirect_uri": _redirect_uri(host), "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state,
    })
    return f"{AUTH_URL}?{q}"


def consuma_state(state: str) -> tuple[bool, int | None]:
    """Valida (e consuma) lo state; ritorna (valido, azienda_id)."""
    dato = _states.pop(state, None)
    if dato is None:
        return False, None
    return True, dato[1]


def scambia_e_salva(code: str, host: str, azienda_id: int | None = None) -> str:
    """Scambia il code con i token, recupera l'email e salva tutto per il tenant. Ritorna l'email."""
    r = httpx.post(TOKEN_URL, data={
        "code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "redirect_uri": _redirect_uri(host), "grant_type": "authorization_code",
    }, timeout=15)
    r.raise_for_status()
    tok = r.json()
    access = tok["access_token"]
    refresh = tok.get("refresh_token")
    scopes = tok.get("scope", "")
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
        row = _row(db, azienda_id)
        if not row:
            row = GoogleCalendar(azienda_id=azienda_id)
            db.add(row)
        row.email = email
        if not (row.calendar_id or "").strip():   # non resettare il calendario già scelto sui riconnect
            row.calendar_id = "primary"
        row.access_token = access
        row.scopes = scopes
        if refresh:  # arriva solo al primo consenso; non sovrascrivere con vuoto
            row.refresh_token = refresh
        row.scad = datetime.utcnow() + timedelta(seconds=expires - 60)
        row.connesso_at = datetime.utcnow()
        db.commit()
        logger.info("📅 Google connesso (tenant=%s): %s | scopes=%s", azienda_id, email or "n/d", scopes)
        return email
    finally:
        db.close()


def stato(db, azienda_id: int | None = None) -> dict:
    row = _row(db, azienda_id)
    if not row or not row.refresh_token:
        return {"connesso": False}
    return {"connesso": True, "email": row.email, "calendar_id": row.calendar_id,
            "email_attiva": "gmail.send" in (row.scopes or ""),
            "connesso_at": row.connesso_at.isoformat() if row.connesso_at else None}


def disconnetti(db, azienda_id: int | None = None) -> None:
    row = _row(db, azienda_id)
    if row:
        db.delete(row)
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


def access_token_valido(db, azienda_id: int | None = None) -> str | None:
    """Access token valido, rinnovato col refresh token se scaduto. None se non connesso."""
    row = _row(db, azienda_id)
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


# ---------- Elenco calendari + selezione di quello attivo ----------

CALENDARLIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"


def calendari(db, azienda_id: int | None = None) -> list[dict]:
    """Elenco dei calendari dell'account Google connesso: [{id, nome, primario, selezionato}].
    Lista vuota se non connesso o in errore (serve lo scope calendar.calendarlist.readonly)."""
    token = access_token_valido(db, azienda_id)
    row = _row(db, azienda_id)
    if not token or not row:
        return []
    attivo = (row.calendar_id or "primary")
    try:
        r = httpx.get(CALENDARLIST_URL, headers={"Authorization": f"Bearer {token}"},
                      params={"minAccessRole": "reader", "maxResults": 250}, timeout=15)
        if r.status_code != 200:
            logger.warning("calendarList fallito (%s): %s", r.status_code, r.text[:160])
            return []
        out = []
        for it in r.json().get("items", []):
            cid = it.get("id")
            out.append({"id": cid, "nome": it.get("summary") or cid,
                        "primario": bool(it.get("primary")), "selezionato": cid == attivo})
        # primario in cima, poi per nome
        out.sort(key=lambda c: (not c["primario"], (c["nome"] or "").lower()))
        return out
    except Exception as e:
        logger.warning("Elenco calendari Google errore: %s", e)
        return []


def imposta_calendario(db, azienda_id: int | None, calendar_id: str) -> bool:
    """Imposta il calendario attivo (usato per vista, disponibilità e prenotazione meeting)."""
    row = _row(db, azienda_id)
    if not row or not (calendar_id or "").strip():
        return False
    row.calendar_id = calendar_id.strip()
    db.commit()
    logger.info("📅 Calendario attivo (tenant=%s) -> %s", azienda_id, row.calendar_id)
    return True


# ---------- Invio email via Gmail API (dalla casella connessa del tenant) ----------

def puo_email(db, azienda_id: int | None = None) -> bool:
    """True se il tenant ha una connessione Google con lo scope gmail.send (può inviare email)."""
    row = _row(db, azienda_id)
    return bool(row and row.refresh_token and "gmail.send" in (row.scopes or ""))


def invia_email_gmail(db, azienda_id: int | None, destinatario: str, oggetto: str,
                      corpo: str, allegati: list[str] | None = None) -> bool:
    """Invia un'email via Gmail API DALLA casella Google connessa del tenant. False se non possibile."""
    import base64
    import os
    from email.message import EmailMessage
    from services import email as email_svc   # lazy: riusa _subtype (no import circolare)

    token = access_token_valido(db, azienda_id)
    row = _row(db, azienda_id)
    if not token or not row:
        return False
    msg = EmailMessage()
    msg["To"] = destinatario
    if row.email:
        msg["From"] = row.email
    msg["Subject"] = oggetto or ""
    msg.set_content(corpo or "")
    for path in (allegati or []):
        try:
            with open(path, "rb") as f:
                data = f.read()
            maintype, subtype = email_svc._subtype(path)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=os.path.basename(path))
        except Exception as e:
            logger.warning("Allegato %s saltato: %s", path, e)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        r = httpx.post(GMAIL_SEND_URL, headers={"Authorization": f"Bearer {token}"},
                       json={"raw": raw}, timeout=20)
    except Exception as e:
        logger.warning("Gmail API send errore di rete: %s", e)
        return False
    if r.status_code == 200:
        logger.info("📧 Email via Gmail API da %s a %s", row.email, destinatario)
        return True
    logger.warning("Gmail API send fallito (%s): %s", r.status_code, r.text[:200])
    return False
