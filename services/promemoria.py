"""Promemoria per cliente: note mirate dell'amministratore, iniettate nel contesto
dell'assistente quando quel contatto chiama/scrive. Gestibili da dashboard e via voce."""

import re
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import SessionLocal, Promemoria, Contatto, Societa, Amministratore, Inoltro


def _numeri_admin(db=None, azienda_id: int | None = None) -> set[str]:
    """Numeri abilitati come amministratore (sole cifre) NEL TENANT: dalla tabella amministratori
    E dalle persone della rubrica inoltri flaggate `admin`."""
    own = db is None
    if own:
        db = SessionLocal()
    numeri: set[str] = set()
    try:
        qa = db.query(Amministratore.telefono)
        qi = db.query(Inoltro.telefono).filter(Inoltro.admin.is_(True))
        if azienda_id:
            qa = qa.filter(Amministratore.azienda_id == azienda_id)
            qi = qi.filter(Inoltro.azienda_id == azienda_id)
        for (t,) in list(qa.all()) + list(qi.all()):
            d = re.sub(r"\D", "", t or "")
            if d:
                numeri.add(d)
    except Exception:
        pass
    finally:
        if own:
            db.close()
    return numeri


def is_admin(telefono: str, db=None, azienda_id: int | None = None) -> bool:
    d = re.sub(r"\D", "", telefono or "")
    return bool(d) and d in _numeri_admin(db, azienda_id)


def attivi(db: Session, contatto_id: int) -> list[Promemoria]:
    """Promemoria non scaduti per il contatto, dal più recente."""
    if not contatto_id:
        return []
    now = datetime.utcnow()
    return (db.query(Promemoria)
            .filter(Promemoria.contatto_id == contatto_id,
                    or_(Promemoria.scade_il.is_(None), Promemoria.scade_il >= now))
            .order_by(Promemoria.created_at.desc()).all())


def blocco_prompt(db: Session, contatto_id: int) -> str:
    """Blocco da iniettare nel prompt con i promemoria attivi del contatto (vuoto se nessuno)."""
    note = attivi(db, contatto_id)
    if not note:
        return ""
    righe = []
    for n in note:
        scad = f" (valido fino al {n.scade_il.strftime('%d/%m/%Y')})" if n.scade_il else ""
        righe.append(f"- {n.testo.strip()}{scad}")
    return (
        "\n\n=== PROMEMORIA PER QUESTO CLIENTE (lasciati dall'amministratore) ===\n"
        "Tienine conto durante la conversazione: comunica al cliente, al momento opportuno e in "
        "modo naturale, le offerte/avvisi qui sotto se pertinenti a ciò di cui parlate.\n"
        + "\n".join(righe)
    )


def crea(db: Session, contatto_id: int, testo: str, giorni_validita: int = 0) -> Promemoria | None:
    testo = (testo or "").strip()
    if not contatto_id or not testo:
        return None
    scade = (datetime.utcnow() + timedelta(days=int(giorni_validita))) if giorni_validita else None
    p = Promemoria(contatto_id=contatto_id, testo=testo, scade_il=scade)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# Parole di riempimento da ignorare nel nome (es. "quello della Trattoria" → [trattoria]).
_FILLER = {"il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "del", "dello", "della",
           "dei", "degli", "delle", "da", "dal", "dalla", "a", "al", "alla", "e", "che", "quello",
           "quella", "sig", "signor", "signora", "titolare", "cliente", "referente", "per"}


def _filtro_societa(q, soc: str):
    return q.outerjoin(Societa, Contatto.societa_id == Societa.id).filter(or_(
        Societa.insegna.ilike(f"%{soc}%"), Societa.ragione_sociale.ilike(f"%{soc}%"),
        Contatto.ragione_sociale.ilike(f"%{soc}%"),
    ))


def trova_target(db: Session, nome: str, societa: str = "", limite: int = 5,
                 azienda_id: int | None = None) -> list[Contatto]:
    """Cerca i contatti (NEL TENANT) destinatari di un promemoria. Robusto:
    - il NOME è spezzato in PAROLE (ignora riempitivi): ogni parola deve comparire in
      nome/cognome/ragione sociale (così «Andrea Barral» matcha anche se detto con giro di parole);
    - se il nome non produce match (o è assente/vago), FALLBACK: cerca per SOCIETÀ.
    Ritorna i candidati (0, 1 o più)."""
    soc = (societa or "").strip()
    tokens = [t for t in re.split(r"[^0-9a-zàèéìòùü]+", (nome or "").lower())
              if len(t) > 1 and t not in _FILLER]

    def base():
        q = db.query(Contatto)
        return q.filter(Contatto.azienda_id == azienda_id) if azienda_id else q

    risultati = []
    if tokens:
        q = base()
        for t in tokens:   # AND fra le parole: tutte devono comparire in un campo-nome
            like = f"%{t}%"
            q = q.filter(or_(Contatto.nome.ilike(like), Contatto.cognome.ilike(like),
                             Contatto.ragione_sociale.ilike(like)))
        if soc:
            q = _filtro_societa(q, soc)
        risultati = q.limit(limite).all()

    # Fallback: nessun match sul nome (o nome vago) → cerca per SOLA società.
    if not risultati and soc:
        risultati = _filtro_societa(base(), soc).limit(limite).all()
    return risultati
