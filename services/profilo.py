"""Profilo aziendale + configurazione comportamentale del risponditore.

Legge la riga singleton `Azienda` (descrizione servizi, criteri di priorità, info di
qualificazione) e costruisce il blocco di system prompt condiviso da assistente vocale
e WhatsApp. È il sapere con cui il risponditore risponde ai lead, li qualifica e decide
la priorità del ticket.

Si combina con `services/istruzioni.py` (istruzioni libere dell'amministratore), che
resta indipendente e viene appeso a valle.
"""

import logging

from sqlalchemy.orm import Session

from database import Azienda

logger = logging.getLogger(__name__)

# Pre-fill mostrato in Impostazioni quando il campo info_qualificazione è vuoto.
INFO_QUALIFICAZIONE_DEFAULT = (
    "Raccogli, in modo naturale e senza interrogatori, almeno:\n"
    "- nome e cognome della persona;\n"
    "- ragione sociale della società e ruolo della persona (es. titolare, ufficio acquisti);\n"
    "- email e telefono per essere ricontattati;\n"
    "- sede / località;\n"
    "- di cosa ha bisogno (prodotto/servizio di interesse), quantità/volumi se rilevanti, "
    "tempistiche e, se emerge, budget."
)


def get_azienda(db: Session, azienda_id: int | None = None) -> Azienda | None:
    """Il tenant richiesto (per id) o, in transizione, l'unica/prima azienda."""
    if azienda_id:
        return db.get(Azienda, azienda_id)
    return db.query(Azienda).first()


def nome_azienda(db: Session, azienda_id: int | None = None) -> str:
    az = get_azienda(db, azienda_id)
    return (az.nome if az else None) or "la nostra azienda"


def _sezione(titolo: str, corpo: str) -> str:
    corpo = (corpo or "").strip()
    if not corpo:
        return ""
    return f"\n\n=== {titolo} ===\n{corpo}"


def blocco_prompt(db: Session, azienda_id: int | None = None) -> str:
    """Blocco di conoscenza/condotta da inserire nel system prompt del risponditore.

    Contiene cosa offre l'azienda, come qualificare il lead e come assegnare la priorità.
    Stringa vuota se l'azienda non ha ancora configurato nulla.
    """
    az = get_azienda(db, azienda_id)
    if not az:
        return ""

    blocchi = [
        _sezione(
            "COSA OFFRIAMO (usa SOLO queste informazioni per rispondere su prodotti/servizi/costi)",
            az.descrizione_servizi,
        ),
        _sezione(
            "COME QUALIFICARE IL LEAD (informazioni da raccogliere durante la conversazione)",
            az.info_qualificazione or INFO_QUALIFICAZIONE_DEFAULT,
        ),
        _sezione(
            "COME ASSEGNARE LA PRIORITÀ AL LEAD (alta / media / bassa)",
            az.criteri_priorita,
        ),
    ]
    return "".join(b for b in blocchi if b)
