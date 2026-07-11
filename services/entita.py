"""Entità generiche customizzabili collegate ai contatti.

Ogni tenant definisce UN tipo di entità (EntitaTipo) con i suoi campi; i contatti si collegano a
0..N istanze (Entita) tramite ContattoEntita (N:N, chiavi surrogate → niente omonimie).
Es: vet → «Animale» {specie*, nome, razza}; onoranze → «Deceduto»; horeca → «Società».

Questo modulo: legge la config, valida i campi obbligatori in registrazione, applica la cardinalità
massima per contatto, e produce il blocco di prompt che dice all'assistente cosa raccogliere.
"""

import json
import logging

from sqlalchemy.orm import Session

from database import EntitaTipo, Entita, ContattoEntita, Contatto

logger = logging.getLogger(__name__)


def tipo_attivo(db: Session, azienda_id: int | None) -> EntitaTipo | None:
    """Il tipo di entità attivo del tenant (uno solo). None se il tenant non ne ha configurato uno
    (es. resta in modalità HORECA/Società)."""
    q = db.query(EntitaTipo).filter(EntitaTipo.attivo.is_(True))
    if azienda_id:
        q = q.filter(EntitaTipo.azienda_id == azienda_id)
    return q.order_by(EntitaTipo.id.desc()).first()


def campi(tipo: EntitaTipo) -> list[dict]:
    """Definizioni dei campi del tipo: [{chiave,label,tipo,obbligatorio,opzioni}]."""
    try:
        val = json.loads(tipo.campi) if tipo and tipo.campi else []
        return val if isinstance(val, list) else []
    except Exception:
        return []


def _valori(ent: Entita) -> dict:
    try:
        v = json.loads(ent.valori) if ent.valori else {}
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _etichetta(tipo: EntitaTipo, valori: dict) -> str:
    """Nome visualizzato dell'istanza: valore del campo_etichetta, o primo campo valorizzato."""
    if tipo.campo_etichetta and (valori.get(tipo.campo_etichetta) or "").strip():
        return str(valori[tipo.campo_etichetta]).strip()[:200]
    for c in campi(tipo):
        v = (valori.get(c.get("chiave")) or "").strip() if isinstance(valori.get(c.get("chiave")), str) else valori.get(c.get("chiave"))
        if v:
            return str(v).strip()[:200]
    return f"{tipo.nome_singolare} #—"


def campi_mancanti(tipo: EntitaTipo, valori: dict) -> list[str]:
    """Label dei campi OBBLIGATORI in registrazione non ancora valorizzati."""
    mancanti = []
    for c in campi(tipo):
        if c.get("obbligatorio"):
            v = valori.get(c.get("chiave"))
            if v is None or (isinstance(v, str) and not v.strip()):
                mancanti.append(c.get("label") or c.get("chiave"))
    return mancanti


def entita_di_contatto(db: Session, contatto_id: int) -> list[dict]:
    """Entità collegate al contatto, coi valori già parsati (per scheda contatto / contesto)."""
    legami = (db.query(ContattoEntita).filter(ContattoEntita.contatto_id == contatto_id)
              .order_by(ContattoEntita.id).all())
    out = []
    for l in legami:
        e = l.entita
        if not e:
            continue
        out.append({"entita_id": e.id, "etichetta": e.etichetta, "ruolo": l.ruolo,
                    "valori": _valori(e), "legame_id": l.id})
    return out


def _n_entita_del_contatto(db: Session, contatto_id: int, tipo_id: int) -> list[ContattoEntita]:
    return (db.query(ContattoEntita).join(Entita, ContattoEntita.entita_id == Entita.id)
            .filter(ContattoEntita.contatto_id == contatto_id, Entita.tipo_id == tipo_id).all())


def registra(db: Session, azienda_id: int | None, contatto_id: int, valori: dict,
             ruolo: str = "") -> dict:
    """Crea (o aggiorna, se cardinalità 1) un'entità del tipo attivo e la collega al contatto.
    Rispetta max_per_contatto. Ritorna {ok, entita_id, etichetta, mancanti} o {ok: False, errore}.
    Non solleva."""
    try:
        tipo = tipo_attivo(db, azienda_id)
        if not tipo:
            return {"ok": False, "errore": "Nessun tipo di entità configurato per questo tenant."}
        c = db.get(Contatto, contatto_id)
        if not c:
            return {"ok": False, "errore": "Contatto inesistente."}

        valori = {k: v for k, v in (valori or {}).items() if v not in (None, "")}
        esistenti = _n_entita_del_contatto(db, contatto_id, tipo.id)

        # Cardinalità 1: aggiorna l'entità già collegata invece di crearne un'altra.
        if tipo.max_per_contatto == 1 and esistenti:
            e = esistenti[0].entita
            v = _valori(e)
            v.update(valori)
            e.valori = json.dumps(v, ensure_ascii=False)
            e.etichetta = _etichetta(tipo, v)
            db.commit()
            return {"ok": True, "entita_id": e.id, "etichetta": e.etichetta,
                    "aggiornato": True, "mancanti": campi_mancanti(tipo, v)}

        # Cardinalità N (o prima entità): crea nuova istanza + legame.
        e = Entita(azienda_id=azienda_id or c.azienda_id, tipo_id=tipo.id,
                   etichetta=_etichetta(tipo, valori), valori=json.dumps(valori, ensure_ascii=False))
        db.add(e)
        db.flush()
        db.add(ContattoEntita(azienda_id=azienda_id or c.azienda_id, contatto_id=contatto_id,
                              entita_id=e.id, ruolo=(ruolo or "").strip() or None))
        db.commit()
        return {"ok": True, "entita_id": e.id, "etichetta": e.etichetta,
                "aggiornato": False, "mancanti": campi_mancanti(tipo, valori)}
    except Exception as ex:
        logger.error("Registrazione entità fallita (contatto %s): %s", contatto_id, ex)
        db.rollback()
        return {"ok": False, "errore": "Errore interno nella registrazione dell'entità."}


def blocco_prompt(db: Session, azienda_id: int | None) -> str:
    """Blocco da iniettare nel prompt: descrive il tipo di entità e i campi da raccogliere
    (evidenziando gli OBBLIGATORI). Vuoto se il tenant non ha configurato un tipo."""
    tipo = tipo_attivo(db, azienda_id)
    if not tipo:
        return ""
    defs = campi(tipo)
    if not defs:
        return ""
    obb = [c.get("label") or c.get("chiave") for c in defs if c.get("obbligatorio")]
    opz = [c.get("label") or c.get("chiave") for c in defs if not c.get("obbligatorio")]
    card = ("una sola" if tipo.max_per_contatto == 1 else "più di una (chiedi se ce ne sono altre)")
    righe = [f"\n\n=== {tipo.nome_singolare.upper()} DEL CLIENTE (da raccogliere e registrare) ==="]
    righe.append(f"Ogni cliente può avere {card} «{tipo.nome_singolare}».")
    if obb:
        righe.append("Chiedi SEMPRE (obbligatori): " + ", ".join(obb) + ".")
    if opz:
        righe.append("Raccogli se emergono (opzionali): " + ", ".join(opz) + ".")
    righe.append(f"Registra i dati con lo strumento dedicato, un record per «{tipo.nome_singolare}».")
    return "\n".join(righe)
