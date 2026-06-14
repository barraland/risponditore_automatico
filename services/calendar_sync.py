import os
import logging
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

_service = None


def _get_service():
    """Inizializza e restituisce il servizio Google Calendar."""
    global _service
    if _service is not None:
        return _service

    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        logger.warning("File credentials.json non trovato: Google Calendar disabilitato")
        return None

    try:
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
        )
        _service = build("calendar", "v3", credentials=credentials)
        return _service
    except Exception as e:
        logger.error("Errore inizializzazione Google Calendar: %s", e)
        return None


def get_slot_liberi(data: str, durata_minuti: int = 30,
                    orario_apertura: str = "09:00",
                    orario_chiusura: str = "18:00") -> list[dict]:
    """Restituisce gli slot liberi per una data specifica.

    Args:
        data: data in formato YYYY-MM-DD
        durata_minuti: durata richiesta in minuti
        orario_apertura: orario apertura studio (HH:MM)
        orario_chiusura: orario chiusura studio (HH:MM)

    Returns:
        lista di dict con chiavi 'inizio' e 'fine' (stringhe HH:MM)
    """
    service = _get_service()
    if not service or not GOOGLE_CALENDAR_ID:
        logger.warning("Google Calendar non disponibile, restituisco slot predefiniti")
        return _slot_predefiniti(data, durata_minuti, orario_apertura, orario_chiusura)

    try:
        time_min = f"{data}T{orario_apertura}:00+01:00"
        time_max = f"{data}T{orario_chiusura}:00+01:00"

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": "Europe/Rome",
            "items": [{"id": GOOGLE_CALENDAR_ID}],
        }

        result = service.freebusy().query(body=body).execute()
        busy_periods = result.get("calendars", {}).get(GOOGLE_CALENDAR_ID, {}).get("busy", [])

        # Converti busy periods in datetime
        busy = []
        for period in busy_periods:
            start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
            busy.append((start, end))

        # Genera slot disponibili
        slot_inizio = datetime.fromisoformat(f"{data}T{orario_apertura}:00+01:00")
        slot_fine_giornata = datetime.fromisoformat(f"{data}T{orario_chiusura}:00+01:00")
        slot_durata = timedelta(minutes=durata_minuti)

        slot_liberi = []
        current = slot_inizio
        while current + slot_durata <= slot_fine_giornata:
            slot_end = current + slot_durata
            is_free = True
            for busy_start, busy_end in busy:
                if current < busy_end and slot_end > busy_start:
                    is_free = False
                    break
            if is_free:
                slot_liberi.append({
                    "inizio": current.strftime("%H:%M"),
                    "fine": slot_end.strftime("%H:%M"),
                })
            current += timedelta(minutes=30)  # avanza di 30 min

        return slot_liberi

    except Exception as e:
        logger.error("Errore lettura slot liberi: %s", e)
        return _slot_predefiniti(data, durata_minuti, orario_apertura, orario_chiusura)


def _slot_predefiniti(data: str, durata_minuti: int,
                      orario_apertura: str, orario_chiusura: str) -> list[dict]:
    """Genera slot predefiniti senza consultare Google Calendar."""
    slot_liberi = []
    ora_inizio = datetime.strptime(f"{data} {orario_apertura}", "%Y-%m-%d %H:%M")
    ora_fine = datetime.strptime(f"{data} {orario_chiusura}", "%Y-%m-%d %H:%M")
    durata = timedelta(minutes=durata_minuti)

    current = ora_inizio
    while current + durata <= ora_fine:
        slot_liberi.append({
            "inizio": current.strftime("%H:%M"),
            "fine": (current + durata).strftime("%H:%M"),
        })
        current += timedelta(minutes=30)

    return slot_liberi


def crea_evento(titolo: str, data_ora: datetime,
                durata_minuti: int, descrizione: str = "") -> str | None:
    """Crea un evento su Google Calendar.

    Returns:
        event_id o None in caso di errore
    """
    service = _get_service()
    if not service or not GOOGLE_CALENDAR_ID:
        logger.warning("Google Calendar non disponibile, evento non creato")
        return None

    try:
        fine = data_ora + timedelta(minutes=durata_minuti)
        event = {
            "summary": titolo,
            "description": descrizione,
            "start": {
                "dateTime": data_ora.isoformat(),
                "timeZone": "Europe/Rome",
            },
            "end": {
                "dateTime": fine.isoformat(),
                "timeZone": "Europe/Rome",
            },
        }

        result = service.events().insert(
            calendarId=GOOGLE_CALENDAR_ID, body=event
        ).execute()

        event_id = result.get("id")
        logger.info("Evento Google Calendar creato: %s", event_id)
        return event_id

    except Exception as e:
        logger.error("Errore creazione evento Calendar: %s", e)
        return None


def cancella_evento(event_id: str) -> bool:
    """Cancella un evento da Google Calendar."""
    service = _get_service()
    if not service or not GOOGLE_CALENDAR_ID:
        return False

    try:
        service.events().delete(
            calendarId=GOOGLE_CALENDAR_ID, eventId=event_id
        ).execute()
        logger.info("Evento Calendar cancellato: %s", event_id)
        return True
    except Exception as e:
        logger.error("Errore cancellazione evento Calendar: %s", e)
        return False


def aggiorna_evento(event_id: str, nuova_data_ora: datetime,
                    durata_minuti: int = 30) -> bool:
    """Aggiorna data/ora di un evento su Google Calendar."""
    service = _get_service()
    if not service or not GOOGLE_CALENDAR_ID:
        return False

    try:
        fine = nuova_data_ora + timedelta(minutes=durata_minuti)
        event = service.events().get(
            calendarId=GOOGLE_CALENDAR_ID, eventId=event_id
        ).execute()

        event["start"] = {
            "dateTime": nuova_data_ora.isoformat(),
            "timeZone": "Europe/Rome",
        }
        event["end"] = {
            "dateTime": fine.isoformat(),
            "timeZone": "Europe/Rome",
        }

        service.events().update(
            calendarId=GOOGLE_CALENDAR_ID, eventId=event_id, body=event
        ).execute()
        logger.info("Evento Calendar aggiornato: %s", event_id)
        return True
    except Exception as e:
        logger.error("Errore aggiornamento evento Calendar: %s", e)
        return False
