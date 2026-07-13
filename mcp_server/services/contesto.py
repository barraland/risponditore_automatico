"""Contesto temporale condiviso da iniettare nei system prompt degli agenti.

Permette di risolvere i riferimenti relativi nelle domande dei condòmini
("bilancio dell'anno scorso", "la rata di quest'anno", "due anni fa", ecc.).
"""

from datetime import datetime

_GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
         "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def contesto_temporale() -> str:
    """Una riga (anzi poche) con data, ora e anno correnti + mapping dei riferimenti relativi."""
    now = datetime.now()
    giorno = _GIORNI[now.weekday()]
    mese = _MESI[now.month - 1]
    anno = now.year
    return (
        f"CONTESTO TEMPORALE — Adesso è {giorno} {now.day} {mese} {anno}, ore {now.strftime('%H:%M')}. "
        f"Anno corrente: {anno}. "
        f"Risolvi sempre i riferimenti relativi in anni espliciti: «quest'anno» = {anno}, "
        f"«anno scorso»/«l'anno passato» = {anno - 1}, «due anni fa» = {anno - 2}, "
        f"«prossimo anno» = {anno + 1}."
    )
