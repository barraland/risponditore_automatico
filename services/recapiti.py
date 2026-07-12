"""Recapiti dei contatti: telefoni ed email multipli (cardinalità 1:N).

La tabella `recapito` è la FONTE DI VERITÀ per l'identità: un numero/una mail in arrivo si risolve al
contatto confrontando la forma normalizzata (`valore_norm`). Le colonne `contatti.telefono` /
`contatti.email` restano come CACHE del recapito «principale» di ciascun tipo (display/ricerca/invii):
in Postgres un trigger le mantiene allineate; qui le aggiorniamo anche in-memory quando l'agente
aggiunge un recapito, così l'oggetto ORM è coerente senza refresh.

Tutte le funzioni sono difensive: se la tabella non esiste ancora (migrazione non lanciata) NON
sollevano — i chiamanti ricadono sul comportamento legacy (colonna singola su `contatti`).
"""

import logging
import re

from sqlalchemy.orm import Session

from database import Recapito, Contatto, TipoRecapito

logger = logging.getLogger(__name__)


def _tenum(tipo) -> TipoRecapito:
    return tipo if isinstance(tipo, TipoRecapito) else TipoRecapito(str(tipo))


def normalizza(tipo, valore: str) -> str:
    """Forma normalizzata per il MATCH: email → minuscolo/trim; telefono → ultime 10 cifre."""
    v = (valore or "").strip()
    if _tenum(tipo) == TipoRecapito.EMAIL:
        return v.lower()
    d = re.sub(r"\D", "", v)
    if d.startswith("00"):
        d = d[2:]
    return d[-10:] if len(d) >= 10 else d


def trova_contatto(db: Session, tipo, valore: str, azienda_id: int | None = None) -> Contatto | None:
    """Contatto (nel tenant) che possiede quel recapito. None se assente o tabella non ancora creata."""
    norm = normalizza(tipo, valore)
    if not norm:
        return None
    try:
        q = db.query(Recapito).filter(Recapito.tipo == _tenum(tipo), Recapito.valore_norm == norm)
        if azienda_id:
            q = q.filter(Recapito.azienda_id == azienda_id)
        r = q.order_by(Recapito.principale.desc(), Recapito.id).first()
        return r.contatto if r else None
    except Exception as e:
        logger.warning("Lookup recapito fallito (tabella assente?): %s", e)
        return None


def lista(db: Session, contatto_id: int) -> list[Recapito]:
    """Recapiti del contatto (telefoni prima, poi email; principale in cima). [] se tabella assente."""
    try:
        return (db.query(Recapito).filter(Recapito.contatto_id == contatto_id)
                .order_by(Recapito.tipo, Recapito.principale.desc(), Recapito.id).all())
    except Exception:
        return []


def aggiungi(db: Session, contatto: Contatto, tipo, valore: str,
             principale: bool | None = None, commit: bool = False) -> Recapito | None:
    """Aggiunge (o aggiorna) un recapito del contatto. Il primo del suo tipo diventa principale.
    Aggiorna anche la cache in-memory `contatti.telefono`/`email`. Ritorna il recapito o None
    (valore vuoto o tabella non ancora creata)."""
    norm = normalizza(tipo, valore)
    if not norm:
        return None
    tipo = _tenum(tipo)
    try:
        esistenti = (db.query(Recapito)
                     .filter(Recapito.contatto_id == contatto.id, Recapito.tipo == tipo).all())
        stesso = next((r for r in esistenti if r.valore_norm == norm), None)
        primo = not esistenti
        vuoi_princ = bool(principale) if principale is not None else primo
        if stesso:
            stesso.valore = (valore or "").strip()
            r = stesso
        else:
            r = Recapito(azienda_id=contatto.azienda_id, contatto_id=contatto.id, tipo=tipo,
                         valore=(valore or "").strip(), valore_norm=norm, principale=primo)
            db.add(r)
            esistenti.append(r)
        if vuoi_princ:
            for e in esistenti:
                e.principale = (e is r)
        # Cache in-memory: allinea la colonna del tipo al recapito principale.
        princ = next((e for e in esistenti if e.principale), r)
        setattr(contatto, "telefono" if tipo == TipoRecapito.TELEFONO else "email", princ.valore)
        db.flush()
        if commit:
            db.commit()
        return r
    except Exception as e:
        logger.warning("Aggiunta recapito fallita (tabella assente?): %s", e)
        if commit:
            db.rollback()
        return None
