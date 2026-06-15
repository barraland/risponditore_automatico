"""Istruzioni in linguaggio naturale scritte dall'amministratore.

Una sola casella di testo, editabile dalla pagina Impostazioni, il cui contenuto
viene iniettato nel system prompt di TUTTI gli LLM (pianificatore, lettore di
sezione, composizione finale, agente WhatsApp, assistente vocale Realtime).

Il testo viene salvato su file (così persiste nel volume `data/` anche in Docker)
e riletto a ogni chiamata: le modifiche valgono subito, senza riavvio.

`blocco_prompt()` impacchetta il testo dentro una cornice che spiega all'LLM il
contesto — cioè che sono indicazioni dell'amministratore (il gestore del
servizio) e NON un messaggio del condomino — per evitare che le confonda con la
domanda dell'utente finale.
"""

import os
import logging

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISTRUZIONI_PATH = os.getenv("ISTRUZIONI_FILE", os.path.join(_BASE, "data", "istruzioni_admin.txt"))


def leggi() -> str:
    """Ritorna il testo delle istruzioni (stringa vuota se non impostate)."""
    try:
        with open(ISTRUZIONI_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except OSError as e:
        logger.warning("Impossibile leggere %s: %s", ISTRUZIONI_PATH, e)
        return ""


def salva(testo: str) -> None:
    """Sovrascrive il file con il testo fornito (lo crea se non esiste)."""
    os.makedirs(os.path.dirname(ISTRUZIONI_PATH), exist_ok=True)
    with open(ISTRUZIONI_PATH, "w", encoding="utf-8") as f:
        f.write((testo or "").strip())


def blocco_prompt() -> str:
    """Blocco da appendere ai system prompt. Stringa vuota se non ci sono istruzioni."""
    testo = leggi()
    if not testo:
        return ""
    return (
        "\n\n=== ISTRUZIONI DELL'AMMINISTRATORE ===\n"
        "Il testo delimitato qui sotto NON è un messaggio del condomino con cui stai parlando e "
        "NON fa parte della sua domanda: sono indicazioni operative scritte dall'amministratore "
        "che gestisce questo assistente (il gestore dello studio). Trattale come policy/preferenze "
        "da rispettare nel modo in cui rispondi (tono, priorità, cosa dire o non dire, "
        "comportamenti particolari). Restano comunque valide le regole già ricevute: non inventare "
        "dati e non rivelare informazioni riservate; se queste indicazioni le contraddicono o ti "
        "chiedono di fornire dati che non hai, dai la precedenza alle regole di sicurezza.\n"
        "----- inizio istruzioni amministratore -----\n"
        f"{testo}\n"
        "----- fine istruzioni amministratore ==="
    )
