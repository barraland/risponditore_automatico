"""Istruzioni in linguaggio naturale scritte dall'amministratore.

Una sola casella di testo (editabile dalla pagina "Configurazione assistente", sia
dashboard backend sia SPA) il cui contenuto viene iniettato nel system prompt di TUTTI
gli LLM (pianificatore, lettore di sezione, composizione finale, agente WhatsApp,
assistente vocale) e in ElevenLabs via la dynamic var {{configurazione}}.

Unica fonte: la colonna `istruzioni_admin` della riga singleton `Azienda` (su Supabase).
Così la SPA la edita direttamente e il backend la rilegge a ogni chiamata, senza riavvio.
Per i deployment legacy (testo ancora nel vecchio file `data/istruzioni_admin.txt`) c'è un
fallback in lettura quando la colonna non è mai stata impostata.

`blocco_prompt()` impacchetta il testo dentro una cornice che spiega all'LLM che sono
indicazioni dell'amministratore (il gestore del servizio) e NON un messaggio del cliente.
"""

import os
import logging

from database import SessionLocal, Azienda

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISTRUZIONI_PATH = os.getenv("ISTRUZIONI_FILE", os.path.join(_BASE, "data", "istruzioni_admin.txt"))


def _leggi_file() -> str:
    try:
        with open(ISTRUZIONI_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except OSError as e:
        logger.warning("Impossibile leggere %s: %s", ISTRUZIONI_PATH, e)
        return ""


def leggi(db=None) -> str:
    """Ritorna il testo delle istruzioni (stringa vuota se non impostate).

    Legge dalla colonna `azienda.istruzioni_admin`; se la colonna non è mai stata
    impostata (NULL), ripiega sul vecchio file per non perdere configurazioni legacy.
    """
    own = db is None
    if own:
        db = SessionLocal()
    try:
        az = db.query(Azienda).first()
        if az is not None and az.istruzioni_admin is not None:
            return (az.istruzioni_admin or "").strip()
    except Exception as e:  # pragma: no cover - robustezza
        logger.warning("Lettura istruzioni da DB fallita: %s", e)
    finally:
        if own:
            db.close()
    return _leggi_file()


def salva(testo: str, db=None) -> None:
    """Salva il testo nella colonna `azienda.istruzioni_admin`."""
    testo = (testo or "").strip()
    own = db is None
    if own:
        db = SessionLocal()
    try:
        az = db.query(Azienda).first()
        if az is not None:
            az.istruzioni_admin = testo
            db.commit()
            return
    except Exception as e:  # pragma: no cover - robustezza
        if own:
            db.rollback()
        logger.warning("Salvataggio istruzioni su DB fallito: %s", e)
    finally:
        if own:
            db.close()
    # fallback: file (se non c'è ancora la riga azienda)
    os.makedirs(os.path.dirname(ISTRUZIONI_PATH), exist_ok=True)
    with open(ISTRUZIONI_PATH, "w", encoding="utf-8") as f:
        f.write(testo)


def leggi_regole(db=None) -> str:
    """Ritorna le regole commerciali/promozioni (stringa vuota se non impostate)."""
    own = db is None
    if own:
        db = SessionLocal()
    try:
        az = db.query(Azienda).first()
        if az is not None and az.regole_commerciali:
            return az.regole_commerciali.strip()
    except Exception as e:  # pragma: no cover - robustezza
        logger.warning("Lettura regole commerciali fallita: %s", e)
    finally:
        if own:
            db.close()
    return ""


def _cornice_istruzioni(testo: str) -> str:
    return (
        "\n\n=== ISTRUZIONI DELL'AMMINISTRATORE ===\n"
        "Il testo delimitato qui sotto NON è un messaggio del cliente con cui stai parlando e "
        "NON fa parte della sua richiesta: sono indicazioni operative scritte dall'amministratore "
        "che gestisce questo assistente (il gestore del servizio). Trattale come policy/preferenze "
        "da rispettare nel modo in cui rispondi (tono, priorità, cosa dire o non dire, "
        "comportamenti particolari). Restano comunque valide le regole già ricevute: non inventare "
        "dati e non rivelare informazioni riservate; se queste indicazioni le contraddicono o ti "
        "chiedono di fornire dati che non hai, dai la precedenza alle regole di sicurezza.\n"
        "----- inizio istruzioni amministratore -----\n"
        f"{testo}\n"
        "----- fine istruzioni amministratore ==="
    )


def _cornice_regole(testo: str) -> str:
    return (
        "\n\n=== REGOLE COMMERCIALI E PROMOZIONI ===\n"
        "Politiche di prezzo, sconti e promozioni impostate dall'amministratore. Applicale quando "
        "rispondi su prezzi/offerte e quando registri un ordine (es. quantità omaggio, sconti a "
        "scaglioni). Se un calcolo è ambiguo o non sei certo, chiedi conferma invece di indovinare.\n"
        "----- inizio regole commerciali -----\n"
        f"{testo}\n"
        "----- fine regole commerciali ==="
    )


def blocco_prompt(db=None, canale=None) -> str:
    """Blocco da appendere ai system prompt: prompt admin (per canale) + regole commerciali.

    `canale`: "whatsapp" usa il prompt WhatsApp (con fallback al prompt vocale se vuoto);
    qualsiasi altro valore (voce/None) usa il prompt vocale `istruzioni_admin`.
    Letti dalla riga `azienda` in un'unica query. Stringa vuota se tutto manca.
    """
    own = db is None
    if own:
        db = SessionLocal()
    try:
        az = db.query(Azienda).first()
        istr = None
        if az is not None:
            if canale == "whatsapp" and (az.prompt_whatsapp or "").strip():
                istr = az.prompt_whatsapp
            else:
                istr = az.istruzioni_admin  # prompt vocale = default + fallback per gli altri canali
        regole = (az.regole_commerciali.strip() if (az is not None and az.regole_commerciali) else "")
    except Exception as e:  # pragma: no cover - robustezza
        logger.warning("Lettura blocco prompt fallita: %s", e)
        istr, regole = None, ""
    finally:
        if own:
            db.close()

    if istr is None:  # colonna mai impostata: fallback al vecchio file (deployment legacy)
        istr = _leggi_file()
    istr = (istr or "").strip()

    blocco = ""
    if istr:
        blocco += _cornice_istruzioni(istr)
    if regole:
        blocco += _cornice_regole(regole)
    return blocco
