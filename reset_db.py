#!/usr/bin/env python3
"""Reset DB con dati realistici + sync su Google Calendar.

Uso: python3 reset_db.py
"""

import os
import random
import logging
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

from database import (
    init_db, Base, engine, SessionLocal,
    Studio, Servizio, Paziente, Appuntamento, Richiamo,
    StatoAppuntamento, StatoRichiamo,
)
from services import calendar_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------- Config ----------

SYNC_CALENDAR = True  # Crea eventi su Google Calendar
MAX_EVENTI_CALENDAR = 20   # Max eventi da creare su Google Calendar
MAX_GIORNI_CALENDAR = 7    # Sincronizza solo appuntamenti nei prossimi N giorni
NUM_SETTIMANE_PASSATE = 4
NUM_SETTIMANE_FUTURE = 2

# ---------- Dati ----------

NOMI = [
    ("Marco", "Rossi"), ("Giulia", "Bianchi"), ("Luca", "Ferrari"),
    ("Anna", "Russo"), ("Francesco", "Esposito"), ("Sara", "Romano"),
    ("Alessandro", "Colombo"), ("Chiara", "Ricci"), ("Davide", "Marino"),
    ("Elena", "Greco"), ("Matteo", "Bruno"), ("Valentina", "Gallo"),
    ("Andrea", "Conti"), ("Francesca", "De Luca"), ("Simone", "Mancini"),
    ("Laura", "Costa"), ("Lorenzo", "Giordano"), ("Martina", "Rizzo"),
    ("Giovanni", "Lombardi"), ("Paola", "Moretti"),
]

SERVIZI = [
    # (nome, durata_min, prezzo, richiamo_giorni, max_giorni_prenotazione, descrizione_triage)
    ("Pulizia dentale", 30, 90.0, 180, None, "Per chi vuole pulizia, ha tartaro, gengive che sanguinano, alito cattivo, o non fa igiene da tempo"),
    ("Controllo periodico", 20, 50.0, 365, None, "Per pazienti nuovi, chi ha dolore, fastidio, denti rotti, problemi generici, o non sa quale servizio serve"),
    ("Otturazione", 45, 120.0, None, None, "Per chi ha carie, buchi nei denti, dolore quando mangia dolci o beve freddo"),
    ("Sbiancamento", 60, 250.0, None, None, "Per chi vuole denti piu' bianchi, ha macchie o denti gialli"),
    ("Estrazione", 30, 150.0, None, None, "Per chi ha denti del giudizio che fanno male, denti molto danneggiati da togliere"),
    ("Visita ortodontica", 30, 80.0, 30, None, "Per chi ha denti storti, problemi di morso, vuole apparecchio o info su ortodonzia"),
    ("Devitalizzazione", 60, 200.0, None, None, "Per chi ha dolore forte e persistente, infezione al dente, ascesso dentale"),
    ("Panoramica dentale", 15, 40.0, None, None, "Per chi ha bisogno di radiografia o lastra ai denti, controllo generale"),
]

# Slot orari possibili (inizio appuntamento)
ORARI_SLOT = [
    "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
    "12:00", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00",
]


def cancella_eventi_calendar(db):
    """Cancella tutti gli eventi Google Calendar collegati ad appuntamenti."""
    try:
        appuntamenti = db.query(Appuntamento).filter(Appuntamento.google_event_id.isnot(None)).all()
    except Exception:
        logger.info("Nessuna tabella esistente, skip cancellazione Calendar")
        return
    count = 0
    for a in appuntamenti:
        if calendar_sync.cancella_evento(a.google_event_id):
            count += 1
    logger.info("Cancellati %d eventi da Google Calendar", count)


def genera_giornate(db, servizi, pazienti):
    """Genera appuntamenti realistici: giornate piene con qualche buco."""
    oggi = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    inizio = oggi - timedelta(weeks=NUM_SETTIMANE_PASSATE)
    fine = oggi + timedelta(weeks=NUM_SETTIMANE_FUTURE)

    giorno_corrente = inizio
    appuntamenti_creati = 0
    eventi_calendar = 0

    while giorno_corrente < fine:
        # Salta weekend
        if giorno_corrente.weekday() >= 5:
            giorno_corrente += timedelta(days=1)
            continue

        # Decidi quanti slot riempire (70-95% della giornata, o giornata vuota rara)
        if random.random() < 0.05:
            # ~5% giorni vuoti (ferie, malattia)
            giorno_corrente += timedelta(days=1)
            continue

        slot_disponibili = list(ORARI_SLOT)
        random.shuffle(slot_disponibili)

        # Riempi 70-95% degli slot
        n_appuntamenti = random.randint(
            int(len(slot_disponibili) * 0.70),
            int(len(slot_disponibili) * 0.95),
        )
        slot_usati = sorted(slot_disponibili[:n_appuntamenti])

        for ora_str in slot_usati:
            ora, minuti = map(int, ora_str.split(":"))
            data_ora = giorno_corrente.replace(hour=ora, minute=minuti)

            paziente = random.choice(pazienti)
            servizio = random.choice(servizi)

            # Verifica che lo slot non si sovrapponga (semplificato: skip se troppo vicino)
            fine_slot = data_ora + timedelta(minutes=servizio.durata_minuti)
            ora_fine_h = fine_slot.hour + fine_slot.minute / 60
            if ora_fine_h > 18:
                continue  # Non sfora oltre le 18

            # Stato basato su data
            if data_ora < oggi:
                stato = random.choices(
                    [StatoAppuntamento.COMPLETATO, StatoAppuntamento.NO_SHOW, StatoAppuntamento.CANCELLATO],
                    weights=[85, 10, 5],
                )[0]
            elif data_ora.date() == oggi.date():
                stato = random.choices(
                    [StatoAppuntamento.CONFERMATO, StatoAppuntamento.PRENOTATO],
                    weights=[70, 30],
                )[0]
            else:
                stato = random.choices(
                    [StatoAppuntamento.PRENOTATO, StatoAppuntamento.CONFERMATO],
                    weights=[60, 40],
                )[0]

            # Crea evento su Google Calendar solo per appuntamenti futuri (max N)
            event_id = None
            limite_calendar = oggi + timedelta(days=MAX_GIORNI_CALENDAR)
            if SYNC_CALENDAR and eventi_calendar < MAX_EVENTI_CALENDAR and data_ora >= oggi and data_ora < limite_calendar and stato != StatoAppuntamento.CANCELLATO:
                titolo = f"{servizio.nome} - {paziente.nome} {paziente.cognome}"
                desc = f"Paziente: {paziente.nome} {paziente.cognome}\nTel: {paziente.telefono}"
                event_id = calendar_sync.crea_evento(titolo, data_ora, servizio.durata_minuti, desc)
                if event_id:
                    eventi_calendar += 1

            appuntamento = Appuntamento(
                paziente_id=paziente.id,
                servizio_id=servizio.id,
                data_ora=data_ora,
                durata_minuti=servizio.durata_minuti,
                stato=stato,
                google_event_id=event_id,
            )
            db.add(appuntamento)
            appuntamenti_creati += 1

        giorno_corrente += timedelta(days=1)

    db.commit()
    logger.info("Creati %d appuntamenti, %d eventi su Calendar", appuntamenti_creati, eventi_calendar)
    return appuntamenti_creati


def genera_richiami(db, pazienti, servizi):
    """Genera richiami per appuntamenti completati con servizi che lo prevedono."""
    servizi_con_richiamo = [s for s in servizi if s.intervallo_richiamo_giorni]
    count = 0
    for s in servizi_con_richiamo:
        apps = (
            db.query(Appuntamento)
            .filter(
                Appuntamento.servizio_id == s.id,
                Appuntamento.stato == StatoAppuntamento.COMPLETATO,
            )
            .order_by(Appuntamento.data_ora.desc())
            .limit(10)
            .all()
        )
        for a in apps:
            data_prevista = a.data_ora.date() + timedelta(days=s.intervallo_richiamo_giorni)
            richiamo = Richiamo(
                paziente_id=a.paziente_id,
                servizio_id=s.id,
                data_prevista=data_prevista,
                stato=StatoRichiamo.DA_INVIARE,
            )
            db.add(richiamo)
            count += 1
    db.commit()
    logger.info("Creati %d richiami", count)


def main():
    logger.info("=" * 60)
    logger.info("RESET DATABASE + GOOGLE CALENDAR SYNC")
    logger.info("=" * 60)

    db = SessionLocal()

    # 1. Cancella eventi Calendar esistenti
    logger.info("Cancellazione eventi Calendar esistenti...")
    cancella_eventi_calendar(db)

    # 2. Droppa e ricrea tutte le tabelle
    logger.info("Reset database...")
    db.close()
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()

    # 3. Studio
    studio = Studio(
        nome=os.getenv("STUDIO_NOME", "Studio Dentistico Demo"),
        nome_dottore="Dott. Marco Bianchi",
        telefono=os.getenv("STUDIO_TELEFONO", "+39 02 1234567"),
        indirizzo="Via Roma 1, Milano",
        orario_apertura="09:00",
        orario_chiusura="18:00",
        giorni_lavorativi="lun,mar,mer,gio,ven",
        durata_slot_default=30,
        max_giorni_prenotazione=14,
    )
    db.add(studio)

    # 4. Servizi
    servizi = []
    for nome, durata, prezzo, richiamo, max_giorni, triage in SERVIZI:
        s = Servizio(
            nome=nome, durata_minuti=durata,
            prezzo=prezzo, intervallo_richiamo_giorni=richiamo,
            max_giorni_prenotazione=max_giorni,
            descrizione_triage=triage,
        )
        db.add(s)
        servizi.append(s)
    db.flush()
    logger.info("Creati %d servizi", len(servizi))

    # 5. Pazienti
    pazienti = []
    for i, (nome, cognome) in enumerate(NOMI):
        telefono = f"+3933{random.randint(1000000, 9999999)}"
        p = Paziente(nome=nome, cognome=cognome, telefono=telefono)
        db.add(p)
        pazienti.append(p)
    db.flush()
    logger.info("Creati %d pazienti", len(pazienti))

    # 6. Appuntamenti realistici
    logger.info("Generazione appuntamenti (passati + futuri)...")
    n_app = genera_giornate(db, servizi, pazienti)

    # 7. Richiami
    genera_richiami(db, pazienti, servizi)

    # 8. Riepilogo
    n_futuri = db.query(Appuntamento).filter(
        Appuntamento.data_ora >= datetime.now(),
        Appuntamento.stato != StatoAppuntamento.CANCELLATO,
    ).count()
    n_oggi = db.query(Appuntamento).filter(
        Appuntamento.data_ora >= datetime.now().replace(hour=0, minute=0),
        Appuntamento.data_ora < datetime.now().replace(hour=23, minute=59),
        Appuntamento.stato != StatoAppuntamento.CANCELLATO,
    ).count()

    logger.info("=" * 60)
    logger.info("DONE!")
    logger.info("  Pazienti: %d", len(pazienti))
    logger.info("  Servizi: %d", len(servizi))
    logger.info("  Appuntamenti totali: %d", n_app)
    logger.info("  Appuntamenti oggi: %d", n_oggi)
    logger.info("  Appuntamenti futuri: %d", n_futuri)
    logger.info("  Richiami: %d", db.query(Richiamo).count())
    logger.info("=" * 60)

    db.close()


if __name__ == "__main__":
    main()
