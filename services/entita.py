"""Entità generiche customizzabili collegate ai contatti.

Ogni tenant definisce UN tipo di entità (EntitaTipo) con i suoi campi; i contatti si collegano a
0..N istanze (Entita) tramite ContattoEntita (N:N, chiavi surrogate → niente omonimie).
Es: vet → «Animale» {specie*, nome, razza}; onoranze → «Deceduto»; horeca → «Società».

Questo modulo: legge la config, valida i campi obbligatori in registrazione, applica la cardinalità
massima per contatto, e produce il blocco di prompt che dice all'assistente cosa raccogliere.
"""

import json
import logging
import re

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


def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    return re.sub(r"[^0-9a-zà-ù]+", "_", s).strip("_")


def _normalizza_valori(tipo: EntitaTipo, valori: dict) -> dict:
    """Rimappa le chiavi di `valori` (come le manda l'LLM: 'nome', 'specie', 'Nome'…) alle chiavi
    ESATTE dei campi configurati. L'LLM non conosce sempre lo slug interno (es. 's' per «Specie
    animale»): riconosciamo la chiave esatta, il suo slug, la label, lo slug della label o il PRIMO
    token dello slug della label (es. 'specie' → «Specie animale»). Le chiavi non riconosciute
    restano invariate (nessuna perdita di dati)."""
    defs = campi(tipo)
    if not defs:
        return dict(valori or {})
    alias: dict[str, str] = {}
    for c in defs:
        ch = (c.get("chiave") or "").strip()
        lab = (c.get("label") or "").strip()
        if not ch:
            continue
        for a in (ch, _slug(ch), lab.lower(), _slug(lab)):
            if a:
                alias.setdefault(a, ch)
        toks = _slug(lab).split("_")
        if toks and toks[0]:
            alias.setdefault(toks[0], ch)     # primo token della label (es. 'specie_animale' → 'specie')
    out: dict = {}
    for k, v in (valori or {}).items():
        target = alias.get(k) or alias.get((k or "").strip().lower()) or alias.get(_slug(k or "")) or k
        if target in out and v in (None, ""):
            continue
        out[target] = v
    return out


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


def _collega_se_serve(db: Session, azienda_id: int | None, contatto_id: int, entita_id: int,
                      ruolo: str = "") -> None:
    esiste = (db.query(ContattoEntita)
              .filter(ContattoEntita.contatto_id == contatto_id, ContattoEntita.entita_id == entita_id).first())
    if not esiste:
        db.add(ContattoEntita(azienda_id=azienda_id, contatto_id=contatto_id,
                              entita_id=entita_id, ruolo=(ruolo or "").strip() or None))


def registra(db: Session, azienda_id: int | None, contatto_id: int, valori: dict,
             ruolo: str = "", entita_id: int | None = None) -> dict:
    """Primitivo FLUIDO (la decisione crea/aggiorna/chiedi la prende l'LLM, non il backend):
    - se `entita_id` è passato → AGGIORNA quella istanza (l'LLM ha deciso che è la stessa);
    - altrimenti, se la cardinalità è 1 e ce n'è già una → aggiorna l'unica (vincolo, non policy);
    - altrimenti → CREA una nuova istanza.
    NESSUN matching per nome nel codice: l'omonimia la disambigua l'LLM col contesto + prompt.
    Ritorna {ok, entita_id, etichetta, aggiornato, mancanti} o {ok: False, errore}. Non solleva."""
    try:
        tipo = tipo_attivo(db, azienda_id)
        if not tipo:
            return {"ok": False, "errore": "Nessun tipo di entità configurato per questo tenant."}
        c = db.get(Contatto, contatto_id)
        if not c:
            return {"ok": False, "errore": "Contatto inesistente."}
        aid = azienda_id or c.azienda_id
        valori = _normalizza_valori(tipo, valori or {})   # chiavi LLM → chiavi esatte dei campi
        valori = {k: v for k, v in valori.items() if v not in (None, "")}

        def _aggiorna(e: Entita) -> dict:
            v = _valori(e)
            v.update(valori)
            e.valori = json.dumps(v, ensure_ascii=False)
            e.etichetta = _etichetta(tipo, v)
            _collega_se_serve(db, aid, contatto_id, e.id, ruolo)
            db.commit()
            return {"ok": True, "entita_id": e.id, "etichetta": e.etichetta,
                    "aggiornato": True, "mancanti": campi_mancanti(tipo, v)}

        # 1) L'LLM indica un'istanza esistente → aggiorna QUELLA (sua decisione).
        if entita_id:
            e = db.get(Entita, entita_id)
            if e and e.tipo_id == tipo.id and e.azienda_id in (None, aid):
                return _aggiorna(e)
            # id non valido: prosegue come nuova

        # 2) Cardinalità 1: un solo slot → aggiorna l'esistente (vincolo strutturale).
        esistenti = _n_entita_del_contatto(db, contatto_id, tipo.id)
        if tipo.max_per_contatto == 1 and esistenti:
            return _aggiorna(esistenti[0].entita)

        # 3) Crea nuova istanza + legame.
        e = Entita(azienda_id=aid, tipo_id=tipo.id, etichetta=_etichetta(tipo, valori),
                   valori=json.dumps(valori, ensure_ascii=False))
        db.add(e)
        db.flush()
        db.add(ContattoEntita(azienda_id=aid, contatto_id=contatto_id, entita_id=e.id,
                              ruolo=(ruolo or "").strip() or None))
        db.commit()
        return {"ok": True, "entita_id": e.id, "etichetta": e.etichetta,
                "aggiornato": False, "mancanti": campi_mancanti(tipo, valori)}
    except Exception as ex:
        logger.error("Registrazione entità fallita (contatto %s): %s", contatto_id, ex)
        db.rollback()
        return {"ok": False, "errore": "Errore interno nella registrazione dell'entità."}


def contesto_contatto(db: Session, contatto_id: int, azienda_id: int | None = None) -> str:
    """Riga di contesto per il prompt: entità GIÀ NOTE del cliente con i loro id surrogati, così
    l'LLM può decidere se una è nuova o riferirsi a una esistente (anti-omonimia). Vuoto se nessun
    tipo configurato."""
    tipo = tipo_attivo(db, azienda_id)
    if not tipo:
        return ""
    righe = []
    for l in (db.query(ContattoEntita).filter(ContattoEntita.contatto_id == contatto_id)
              .order_by(ContattoEntita.id).all()):
        e = l.entita
        if not e or e.tipo_id != tipo.id:
            continue
        extra = ", ".join(f"{k}: {vv}" for k, vv in _valori(e).items() if vv)[:140]
        righe.append(f"#{e.id} {e.etichetta or '—'}" + (f" ({extra})" if extra else ""))
    etich = tipo.nome_plurale or tipo.nome_singolare
    if not righe:
        return f"{etich} già noti di questo cliente: nessuno."
    return (f"{etich} già noti di questo cliente (usa l'id per aggiornarne uno, ometti l'id per "
            f"crearne uno nuovo): " + "; ".join(righe))


def blocco_prompt(db: Session, azienda_id: int | None) -> str:
    """Blocco da iniettare nel prompt: descrive il tipo di entità e i campi da raccogliere
    (evidenziando gli OBBLIGATORI). Vuoto se il tenant non ha configurato un tipo."""
    tipo = tipo_attivo(db, azienda_id)
    if not tipo:
        return ""
    defs = campi(tipo)
    if not defs:
        return ""
    def _f(c: dict) -> str:
        lab = c.get("label") or c.get("chiave")
        opts = c.get("opzioni") or []
        suff = f" [valori ammessi: {', '.join(opts)}]" if opts else ""
        return f'{lab} → chiave "{c.get("chiave")}"{suff}'

    obb = [_f(c) for c in defs if c.get("obbligatorio")]
    opz = [_f(c) for c in defs if not c.get("obbligatorio")]
    card = ("una sola" if tipo.max_per_contatto == 1 else "più di una (chiedi se ce ne sono altre)")
    righe = [f"\n\n=== {tipo.nome_singolare.upper()} DEL CLIENTE (da raccogliere e registrare) ==="]
    righe.append(f"Ogni cliente può avere {card} «{tipo.nome_singolare}».")
    if obb:
        righe.append("Chiedi SEMPRE (obbligatori): " + "; ".join(obb) + ".")
    if opz:
        righe.append("Raccogli se emergono (opzionali): " + "; ".join(opz) + ".")
    righe.append(f"Registra i dati con lo strumento dedicato (un record per «{tipo.nome_singolare}»), "
                 f"passando in `valori` ESATTAMENTE le chiavi indicate qui sopra (tra virgolette).")
    return "\n".join(righe)
