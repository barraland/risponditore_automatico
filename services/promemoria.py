"""Promemoria per cliente: note mirate dell'amministratore, iniettate nel contesto
dell'assistente quando quel contatto chiama/scrive. Gestibili da dashboard e via voce."""

import os
import re
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import Promemoria, Contatto, Societa

# Numeri abilitati come amministratore (lasciano promemoria via voce). Csv in ADMIN_TELEFONI.
_ADMIN = {re.sub(r"\D", "", t) for t in os.getenv("ADMIN_TELEFONI", "").split(",") if t.strip()}


def is_admin(telefono: str) -> bool:
    d = re.sub(r"\D", "", telefono or "")
    return bool(d) and d in _ADMIN


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


def trova_target(db: Session, nome: str, societa: str = "", limite: int = 5) -> list[Contatto]:
    """Cerca i contatti che corrispondono a nome (e opzionalmente società) per individuare il
    destinatario di un promemoria lasciato via voce. Ritorna i candidati (0, 1 o più)."""
    nome = (nome or "").strip()
    if not nome:
        return []
    like = f"%{nome}%"
    q = db.query(Contatto).filter(or_(
        Contatto.nome.ilike(like), Contatto.cognome.ilike(like),
        (Contatto.nome + " " + Contatto.cognome).ilike(like),
        Contatto.ragione_sociale.ilike(like),
    ))
    soc = (societa or "").strip()
    if soc:
        q = q.outerjoin(Societa, Contatto.societa_id == Societa.id).filter(or_(
            Societa.insegna.ilike(f"%{soc}%"), Societa.ragione_sociale.ilike(f"%{soc}%"),
            Contatto.ragione_sociale.ilike(f"%{soc}%"),
        ))
    return q.limit(limite).all()
