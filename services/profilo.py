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


# Campi della PERSONA (contatto) che l'assistente può raccogliere, con etichetta.
CONTATTO_CAMPI = [("nome", "Nome"), ("cognome", "Cognome"), ("telefono", "Telefono"),
                  ("email", "Email"), ("ruolo", "Ruolo")]
_CONTATTO_OBBL_DEFAULT = ["nome", "telefono"]


def contatto_obbligatori(db: Session, azienda_id: int | None = None) -> list[str]:
    """Chiavi dei campi persona obbligatori per il tenant (config), o il default. La colonna
    `azienda.contatto_obbligatori` NON è mappata sull'ORM: la leggiamo con SQL grezzo in una sessione
    separata, così se la migrazione non è ancora girata (colonna assente) NON rompiamo nulla."""
    import json
    from sqlalchemy import text
    from database import SessionLocal
    az = get_azienda(db, azienda_id)
    aid = az.id if az else None
    raw = None
    s = SessionLocal()
    try:
        if aid:
            raw = s.execute(text("select contatto_obbligatori from azienda where id = :id"), {"id": aid}).scalar()
        else:
            raw = s.execute(text("select contatto_obbligatori from azienda limit 1")).scalar()
    except Exception:
        raw = None                      # colonna assente (migrazione non ancora fatta) → default
    finally:
        s.close()
    if raw and str(raw).strip():
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return [k for k in val if k in dict(CONTATTO_CAMPI)]
        except Exception:
            pass
    return list(_CONTATTO_OBBL_DEFAULT)


def contatto_campi_prompt(db: Session, azienda_id: int | None = None) -> str:
    """Blocco da iniettare: dati della PERSONA da raccogliere, con gli OBBLIGATORI (da config).
    Dinamico come quello dell'entità: nel prompt non si scrivono i campi a mano."""
    obb_keys = contatto_obbligatori(db, azienda_id)
    obb = [lab for k, lab in CONTATTO_CAMPI if k in obb_keys]
    opz = [lab for k, lab in CONTATTO_CAMPI if k not in obb_keys]
    righe = ["\n\n=== DATI DELLA PERSONA (contatto) — da raccogliere e registrare con salva_contatto ==="]
    if obb:
        righe.append("Chiedi SEMPRE (obbligatori): " + ", ".join(obb) + ".")
    if opz:
        righe.append("Raccogli se emergono (opzionali): " + ", ".join(opz) + ".")
    return "\n".join(righe)


# Canali su cui il SALUTO d'apertura (azienda.saluto*) è attivo. Default: solo WhatsApp
# (la voce, se deflaggata, usa comunque il saluto di sistema di default).
_SALUTO_CANALI_VALIDI = ("voce", "whatsapp")
_SALUTO_CANALI_DEFAULT = ["whatsapp"]


def saluto_canali(db: Session, azienda_id: int | None = None) -> list[str]:
    """Canali su cui il saluto d'apertura è attivo (config del tenant), o il default. La colonna
    `azienda.saluto_canali` NON è mappata sull'ORM: la leggiamo con SQL grezzo in una sessione
    separata, così se la migrazione non è ancora girata (colonna assente) NON rompiamo nulla."""
    import json
    from sqlalchemy import text
    from database import SessionLocal
    az = get_azienda(db, azienda_id)
    aid = az.id if az else None
    raw = None
    s = SessionLocal()
    try:
        if aid:
            raw = s.execute(text("select saluto_canali from azienda where id = :id"), {"id": aid}).scalar()
        else:
            raw = s.execute(text("select saluto_canali from azienda limit 1")).scalar()
    except Exception:
        raw = None                      # colonna assente (migrazione non ancora fatta) → default
    finally:
        s.close()
    if raw and str(raw).strip():
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return [c for c in val if c in _SALUTO_CANALI_VALIDI]
        except Exception:
            pass
    return list(_SALUTO_CANALI_DEFAULT)


def saluto_attivo(db: Session, canale: str, azienda_id: int | None = None) -> bool:
    """True se il saluto d'apertura configurabile è attivo per questo canale ('voce'/'whatsapp')."""
    return canale in saluto_canali(db, azienda_id)


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

    # Qualifica lead e priorità sono ora MODULI (services/prompt_moduli), non più qui, per non
    # duplicarli. Qui resta solo la conoscenza prodotto ("Cosa offriamo", editata in Documenti).
    blocchi = [
        _sezione(
            "COSA OFFRIAMO (usa queste informazioni per rispondere su prodotti/servizi/costi)",
            az.descrizione_servizi,
        ),
    ]
    return "".join(b for b in blocchi if b)
