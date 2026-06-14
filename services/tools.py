"""Funzioni pure chiamabili dall'LLM scheduler via tool calling.

Ogni funzione riceve db + telefono + params specifici, ritorna dict JSON-serializzabile.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from database import (
    Paziente, Appuntamento, Servizio, Studio,
    StatoAppuntamento,
)
from services import calendar_sync

logger = logging.getLogger(__name__)

TZ_ROMA = ZoneInfo("Europe/Rome")
_GIORNI_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def _now_roma() -> datetime:
    """Ora attuale nel fuso orario italiano."""
    return datetime.now(TZ_ROMA)


def _filtra_slot_passati(orari: list[str], data_str: str) -> list[str]:
    """Rimuove orari già passati se la data è oggi."""
    now = _now_roma()
    oggi_str = now.strftime("%Y-%m-%d")
    if data_str != oggi_str:
        return orari
    ora_attuale = now.strftime("%H:%M")
    return [o for o in orari if o > ora_attuale]


def _get_studio(db: Session) -> Studio | None:
    return db.query(Studio).first()


def _get_paziente(db: Session, telefono: str) -> Paziente | None:
    return db.query(Paziente).filter(Paziente.telefono == telefono).first()


def _get_appuntamenti_attivi(db: Session, paziente_id: int) -> list[Appuntamento]:
    return (
        db.query(Appuntamento)
        .filter(
            Appuntamento.paziente_id == paziente_id,
            Appuntamento.stato.in_([StatoAppuntamento.PRENOTATO, StatoAppuntamento.CONFERMATO]),
            Appuntamento.data_ora > datetime.now(),
        )
        .order_by(Appuntamento.data_ora)
        .limit(10)
        .all()
    )


def _disambigua_appuntamento(
    db: Session,
    appuntamenti: list[Appuntamento],
    servizio: str | None = None,
    data: str | None = None,
) -> dict:
    """Filtra appuntamenti per servizio e/o data per disambiguazione.

    Returns:
        - {"trovato": Appuntamento} se esattamente 1 match
        - {"ok": False, "scelta_richiesta": True, ...} se ambiguo
        - {"ok": False, "errore": "..."} se 0 match dopo filtro
    """
    filtrati = appuntamenti

    if servizio:
        srv = db.query(Servizio).filter(Servizio.nome.ilike(f"%{servizio}%")).first()
        if srv:
            filtrati = [a for a in filtrati if a.servizio_id == srv.id]

    if data:
        try:
            data_dt = datetime.strptime(data, "%Y-%m-%d").date()
            filtrati = [a for a in filtrati if a.data_ora.date() == data_dt]
        except ValueError:
            pass

    if len(filtrati) == 1:
        return {"trovato": filtrati[0]}

    if len(filtrati) == 0:
        return {"ok": False, "errore": "Nessun appuntamento trovato con i criteri specificati."}

    # Ancora ambiguo
    lista = []
    for a in filtrati:
        srv_obj = db.query(Servizio).get(a.servizio_id)
        nome_srv = srv_obj.nome if srv_obj else "N/A"
        lista.append({
            "servizio": nome_srv,
            "data": a.data_ora.strftime("%d/%m/%Y"),
            "ora": a.data_ora.strftime("%H:%M"),
        })
    return {"ok": False, "scelta_richiesta": True, "appuntamenti": lista}


# ---------- Tool functions ----------


def registra_paziente(db: Session, telefono: str, nome: str, cognome: str = "") -> dict:
    """Registra o aggiorna nome e cognome del paziente."""
    paziente = _get_paziente(db, telefono)
    if not paziente:
        return {"ok": False, "errore": "Paziente non trovato"}

    paziente.nome = nome.strip()
    if cognome.strip():
        paziente.cognome = cognome.strip()
    db.commit()

    nome_completo = f"{paziente.nome} {paziente.cognome}".strip()
    logger.info("    -> registra_paziente: %s (%s)", nome_completo, telefono)
    return {"ok": True, "nome_completo": nome_completo}


def lista_servizi(db: Session, telefono: str) -> dict:
    """Restituisce tutti i servizi offerti dallo studio con prezzi e durate."""
    servizi = db.query(Servizio).all()
    logger.info("    -> lista_servizi: %d servizi trovati", len(servizi))
    return {
        "servizi": [
            {
                "nome": s.nome,
                "durata_minuti": s.durata_minuti,
                "prezzo_euro": s.prezzo,
                "richiamo_giorni": s.intervallo_richiamo_giorni,
            }
            for s in servizi
        ]
    }


def cerca_disponibilita(db: Session, telefono: str,
                         servizio: str = "", data: str | None = None) -> dict:
    """Cerca slot disponibili per un servizio. Se data non specificata, cerca prossimi giorni."""
    if not servizio:
        return {"errore": "servizio_mancante", "messaggio": "Specifica il servizio per cercare disponibilità."}
    studio = _get_studio(db)
    orario_ap = studio.orario_apertura if studio else "09:00"
    orario_ch = studio.orario_chiusura if studio else "18:00"
    giorni_lav = (studio.giorni_lavorativi if studio else "lun,mar,mer,gio,ven").split(",")

    # Trova servizio nel DB
    srv = db.query(Servizio).filter(Servizio.nome.ilike(f"%{servizio}%")).first()
    durata = srv.durata_minuti if srv else 30
    nome_servizio = srv.nome if srv else servizio
    prezzo = srv.prezzo if srv else 0.0

    # Orizzonte massimo prenotazione (servizio override > studio default)
    studio_max = studio.max_giorni_prenotazione if studio and studio.max_giorni_prenotazione else 14
    max_giorni = srv.max_giorni_prenotazione if (srv and srv.max_giorni_prenotazione) else studio_max

    giorno_map = {0: "lun", 1: "mar", 2: "mer", 3: "gio", 4: "ven", 5: "sab", 6: "dom"}

    # Se data specificata, cerca slot per quel giorno (solo se lavorativo e dentro orizzonte)
    if data:
        try:
            data_dt = datetime.strptime(data, "%Y-%m-%d")
            # Verifica orizzonte massimo
            limite = _now_roma() + timedelta(days=max_giorni)
            if data_dt.date() > limite.date():
                return {
                    "errore": "data_troppo_lontana",
                    "messaggio": f"Non e' possibile prenotare oltre {max_giorni} giorni in avanti. "
                                 f"La data massima e' il {limite.strftime('%d/%m/%Y')}.",
                }
            giorno_sigla = giorno_map.get(data_dt.weekday(), "")
            if giorno_sigla not in giorni_lav:
                logger.info("    -> %s è un giorno non lavorativo, cerco alternative", data)
                data = None  # forza ricerca multi-giorno
        except ValueError:
            pass

    if data:
        logger.info("    -> cerca_disponibilita: %s il %s (durata=%d)", nome_servizio, data, durata)
        slot = calendar_sync.get_slot_liberi(data, durata, orario_ap, orario_ch)
        if slot:
            orari = _filtra_slot_passati([s["inizio"] for s in slot[:8]], data)
            if orari:
                data_dt_parsed = datetime.strptime(data, "%Y-%m-%d")
                return {
                    "servizio": nome_servizio,
                    "durata_minuti": durata,
                    "prezzo_euro": prezzo,
                    "disponibilita": [{
                        "data": data,
                        "giorno": f"{_GIORNI_IT[data_dt_parsed.weekday()]} {data_dt_parsed.strftime('%d/%m')}",
                        "orari": orari,
                    }],
                }
        # Nessuno slot per la data richiesta → cerca alternative
        logger.info("    -> Nessuno slot il %s, cerco alternative", data)

    # Cerca nei prossimi giorni lavorativi
    if data:
        try:
            data_base = datetime.strptime(data, "%Y-%m-%d")
        except ValueError:
            data_base = _now_roma()
    else:
        data_base = _now_roma()

    logger.info("    -> cerca_disponibilita: cerco prossimi slot per %s da %s", nome_servizio, data_base.strftime("%Y-%m-%d"))
    proposte = []
    for i in range(0 if not data else 1, max_giorni):
        data_check = data_base + timedelta(days=i)
        if giorno_map.get(data_check.weekday(), "") not in giorni_lav:
            continue
        data_check_str = data_check.strftime("%Y-%m-%d")
        slot = calendar_sync.get_slot_liberi(data_check_str, durata, orario_ap, orario_ch)
        if slot:
            orari = _filtra_slot_passati([s["inizio"] for s in slot[:5]], data_check_str)
            if not orari:
                continue
            proposte.append({
                "data": data_check_str,
                "giorno": f"{_GIORNI_IT[data_check.weekday()]} {data_check.strftime('%d/%m')}",
                "orari": orari,
            })
            if len(proposte) >= 3:
                break

    result = {
        "servizio": nome_servizio,
        "durata_minuti": durata,
        "prezzo_euro": prezzo,
        "disponibilita": proposte,
    }
    if data and not any(p["data"] == data for p in proposte):
        try:
            nota_dt = datetime.strptime(data, "%Y-%m-%d")
            data_fmt = f"{_GIORNI_IT[nota_dt.weekday()]} {nota_dt.strftime('%d/%m')}"
        except ValueError:
            data_fmt = data
        result["nota"] = f"Purtroppo non ci sono disponibilità per {data_fmt}"
    return result


def prenota_appuntamento(db: Session, telefono: str,
                          servizio: str, data: str, ora: str,
                          note: str = "") -> dict:
    """Prenota un appuntamento. Richiede paziente registrato (nome noto)."""
    paziente = _get_paziente(db, telefono)
    if not paziente:
        return {"ok": False, "errore": "paziente_non_trovato"}

    if paziente.nome == "Nuovo" and paziente.cognome == "Paziente":
        return {
            "ok": False,
            "errore": "nome_richiesto",
            "messaggio": "Per confermare la prenotazione serve il nome del paziente. Chiedi nome e cognome.",
        }

    srv = db.query(Servizio).filter(Servizio.nome.ilike(f"%{servizio}%")).first()
    if not srv:
        return {"ok": False, "errore": "servizio_non_trovato", "messaggio": f"Servizio '{servizio}' non trovato"}

    try:
        data_ora = datetime.strptime(f"{data} {ora}", "%Y-%m-%d %H:%M")
    except ValueError:
        return {"ok": False, "errore": "data_invalida", "messaggio": "Formato data/ora non valido"}

    # Crea evento Google Calendar
    titolo = f"{srv.nome} - {paziente.nome} {paziente.cognome}"
    descrizione = f"Paziente: {paziente.nome} {paziente.cognome}\nTel: {paziente.telefono}"
    note_str = note.strip() if note else ""
    if note_str:
        descrizione += f"\nNote: {note_str}"
    logger.info("    -> prenota_appuntamento: %s per %s il %s alle %s", srv.nome, paziente.nome, data, ora)
    event_id = calendar_sync.crea_evento(titolo, data_ora, srv.durata_minuti, descrizione)

    appuntamento = Appuntamento(
        paziente_id=paziente.id,
        servizio_id=srv.id,
        data_ora=data_ora,
        durata_minuti=srv.durata_minuti,
        stato=StatoAppuntamento.PRENOTATO,
        google_event_id=event_id,
        note=note_str or None,
    )
    db.add(appuntamento)
    db.commit()
    db.refresh(appuntamento)

    logger.info("    -> Appuntamento #%d creato", appuntamento.id)
    return {
        "ok": True,
        "appuntamento": {
            "id": appuntamento.id,
            "servizio": srv.nome,
            "data": data_ora.strftime("%d/%m/%Y"),
            "ora": data_ora.strftime("%H:%M"),
            "durata_minuti": srv.durata_minuti,
            "prezzo_euro": srv.prezzo,
        },
    }


def cancella_appuntamento(db: Session, telefono: str,
                           servizio: str = "", data: str = "") -> dict:
    """Cancella un appuntamento attivo. Disambigua se necessario."""
    paziente = _get_paziente(db, telefono)
    if not paziente:
        return {"ok": False, "errore": "Paziente non trovato"}

    appuntamenti = _get_appuntamenti_attivi(db, paziente.id)
    if not appuntamenti:
        return {"ok": False, "errore": "Nessun appuntamento futuro da cancellare."}

    if len(appuntamenti) == 1:
        target = appuntamenti[0]
    else:
        result = _disambigua_appuntamento(db, appuntamenti, servizio or None, data or None)
        if "trovato" in result:
            target = result["trovato"]
        else:
            return result

    srv = db.query(Servizio).get(target.servizio_id)
    nome_servizio = srv.nome if srv else "N/A"

    if target.google_event_id:
        logger.info("    -> cancella_evento(event_id=%s)", target.google_event_id)
        calendar_sync.cancella_evento(target.google_event_id)

    target.stato = StatoAppuntamento.CANCELLATO
    db.commit()

    logger.info("    -> Appuntamento #%d cancellato", target.id)
    return {
        "ok": True,
        "cancellato": {
            "servizio": nome_servizio,
            "data": target.data_ora.strftime("%d/%m/%Y"),
            "ora": target.data_ora.strftime("%H:%M"),
        },
    }


def sposta_appuntamento(db: Session, telefono: str,
                         nuova_data: str, nuova_ora: str,
                         servizio: str = "", data_attuale: str = "") -> dict:
    """Sposta un appuntamento attivo a una nuova data/ora. Disambigua se necessario."""
    paziente = _get_paziente(db, telefono)
    if not paziente:
        return {"ok": False, "errore": "Paziente non trovato"}

    appuntamenti = _get_appuntamenti_attivi(db, paziente.id)
    if not appuntamenti:
        return {"ok": False, "errore": "Nessun appuntamento futuro da spostare."}

    if len(appuntamenti) == 1:
        target = appuntamenti[0]
    else:
        result = _disambigua_appuntamento(
            db, appuntamenti, servizio or None, data_attuale or None
        )
        if "trovato" in result:
            target = result["trovato"]
        else:
            return result

    srv = db.query(Servizio).get(target.servizio_id)
    nome_servizio = srv.nome if srv else "N/A"
    durata = srv.durata_minuti if srv else 30

    try:
        nuova_data_ora = datetime.strptime(f"{nuova_data} {nuova_ora}", "%Y-%m-%d %H:%M")
    except ValueError:
        return {"ok": False, "errore": "Formato data/ora non valido"}

    vecchia_data = target.data_ora.strftime("%d/%m/%Y alle %H:%M")
    logger.info("    -> sposta_appuntamento #%d: %s -> %s %s",
                target.id, vecchia_data, nuova_data, nuova_ora)

    if target.google_event_id:
        calendar_sync.aggiorna_evento(target.google_event_id, nuova_data_ora, durata)
    else:
        titolo = f"{nome_servizio} - {paziente.nome} {paziente.cognome}"
        descrizione = f"Paziente: {paziente.nome} {paziente.cognome}\nTel: {paziente.telefono}"
        event_id = calendar_sync.crea_evento(titolo, nuova_data_ora, durata, descrizione)
        target.google_event_id = event_id

    target.data_ora = nuova_data_ora
    db.commit()

    return {
        "ok": True,
        "spostato": {
            "servizio": nome_servizio,
            "da": vecchia_data,
            "nuova_data": nuova_data_ora.strftime("%d/%m/%Y"),
            "nuova_ora": nuova_data_ora.strftime("%H:%M"),
        },
    }


def cerca_appuntamenti(db: Session, telefono: str) -> dict:
    """Restituisce gli appuntamenti futuri del paziente."""
    paziente = _get_paziente(db, telefono)
    if not paziente:
        return {"appuntamenti": []}

    appuntamenti = _get_appuntamenti_attivi(db, paziente.id)
    logger.info("    -> cerca_appuntamenti: %d attivi per %s", len(appuntamenti), telefono)
    return {
        "appuntamenti": [
            {
                "servizio": (db.query(Servizio).get(a.servizio_id).nome
                             if db.query(Servizio).get(a.servizio_id) else "N/A"),
                "data": a.data_ora.strftime("%d/%m/%Y"),
                "ora": a.data_ora.strftime("%H:%M"),
                "stato": a.stato.value,
            }
            for a in appuntamenti
        ]
    }
