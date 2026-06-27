"""Inoltro chiamata: rubrica delle persone a cui l'assistente può passare la chiamata, con le
regole (testo libero) di quando inoltrare. La rubrica viene iniettata nel prompt; il bridge vero
della chiamata lo fa la telefonia (ElevenLabs "Transfer to number")."""

from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import Inoltro


def tutti(db: Session) -> list[Inoltro]:
    return db.query(Inoltro).order_by(Inoltro.created_at.desc()).all()


def blocco_prompt(db: Session) -> str:
    """Blocco da iniettare nel prompt: chi sono i destinatari di inoltro e QUANDO inoltrare."""
    lst = tutti(db)
    if not lst:
        return ""
    righe = []
    for i in lst:
        ruolo = f" ({i.ruolo})" if i.ruolo else ""
        regole = (i.regole or "").strip().replace("\n", " ")
        righe.append(f"- {i.nome_completo}{ruolo} — tel {i.telefono}. Inoltra quando: {regole or '—'}")
    return (
        "\n\n=== INOLTRO CHIAMATA (a chi passare la chiamata e quando) ===\n"
        "Se la richiesta del cliente rientra in una delle regole qui sotto, proponi di passarlo alla "
        "persona giusta e usa lo strumento inoltra_chiamata. NON inoltrare se non rientra nelle regole.\n"
        + "\n".join(righe)
    )


def trova(db: Session, nome: str = "", ruolo: str = "") -> list[Inoltro]:
    """Cerca il destinatario di inoltro per nome o ruolo (per il tool inoltra_chiamata)."""
    q = db.query(Inoltro)
    nome = (nome or "").strip()
    ruolo = (ruolo or "").strip()
    if nome:
        like = f"%{nome}%"
        q = q.filter(or_(Inoltro.nome.ilike(like), Inoltro.cognome.ilike(like),
                         (Inoltro.nome + " " + Inoltro.cognome).ilike(like)))
    if ruolo:
        q = q.filter(Inoltro.ruolo.ilike(f"%{ruolo}%"))
    return q.limit(5).all()
