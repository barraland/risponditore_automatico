"""Helper di dominio HORECA: società (locali), ordini, attribuzione agente.

Logica condivisa tra la GUI (router horeca) e i risponditori (WhatsApp/voce), così
la cattura di un ordine dalla conversazione e l'inserimento manuale usano lo stesso
percorso. L'ordine è sempre ancorato a una SOCIETÀ; la persona e l'agente sono il
"chi/come".
"""

import logging

from sqlalchemy.orm import Session

from database import (
    Societa, Agente, Ordine, RigaOrdine, Contatto,
    StatoRelazione, TipoAttivita, CanaleOrdine, StatoOrdine, OrigineOrdine,
)

logger = logging.getLogger(__name__)


# ---------- Società ----------

def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def trova_societa(db: Session, insegna: str | None = None,
                  ragione_sociale: str | None = None) -> Societa | None:
    """Cerca una società per insegna o ragione sociale (case-insensitive, match esatto)."""
    chiavi = {_norm(insegna), _norm(ragione_sociale)} - {""}
    if not chiavi:
        return None
    for soc in db.query(Societa).all():
        if _norm(soc.insegna) in chiavi or _norm(soc.ragione_sociale) in chiavi:
            return soc
    return None


def trova_o_crea_societa(db: Session, insegna: str | None = None,
                         ragione_sociale: str | None = None,
                         citta: str | None = None,
                         tipo: TipoAttivita = TipoAttivita.RISTORANTE) -> Societa:
    """Ritorna la società corrispondente o ne crea una nuova (prospect)."""
    soc = trova_societa(db, insegna, ragione_sociale)
    if soc:
        return soc
    nome = (insegna or ragione_sociale or "Nuova società").strip()
    soc = Societa(
        insegna=nome, ragione_sociale=(ragione_sociale or "").strip() or None,
        citta=(citta or "").strip() or None, tipo=tipo,
        stato_relazione=StatoRelazione.PROSPECT,
    )
    db.add(soc)
    db.commit()
    db.refresh(soc)
    logger.info("Nuova società creata: %s (id %s)", soc.nome, soc.id)
    return soc


def societa_di_contatto(db: Session, contatto: Contatto) -> Societa | None:
    """Società associata a un contatto; se manca ma il contatto ha una ragione sociale,
    crea/collega una società al volo (usato dai risponditori in fase di ordine)."""
    if contatto.societa_id:
        return db.get(Societa, contatto.societa_id)
    nome = (contatto.ragione_sociale or "").strip()
    if not nome:
        return None
    soc = trova_o_crea_societa(db, insegna=nome, ragione_sociale=contatto.ragione_sociale,
                               citta=contatto.sede)
    contatto.societa_id = soc.id
    if not soc.contatti:
        contatto.is_primario = True
    db.commit()
    return soc


def aggiorna_stato_relazione(db: Session, societa: Societa) -> None:
    """Promuove la società a 'cliente' se ha almeno un ordine confermato/evaso."""
    if societa.stato_relazione == StatoRelazione.CLIENTE:
        return
    attivi = [o for o in societa.ordini if o.stato in (StatoOrdine.CONFERMATO, StatoOrdine.EVASO)]
    if attivi:
        societa.stato_relazione = StatoRelazione.CLIENTE
        db.commit()


# ---------- Ordini ----------

def crea_ordine(db: Session, societa_id: int, righe: list[dict],
                contatto_id: int | None = None, agente_id: int | None = None,
                origine: OrigineOrdine = OrigineOrdine.CLIENTE,
                canale: CanaleOrdine = CanaleOrdine.MANUALE,
                stato: StatoOrdine = StatoOrdine.BOZZA,
                note: str | None = None,
                descrizione_agente: str | None = None) -> Ordine | None:
    """Crea un ordine con le sue righe. `righe` = lista di dict
    {descrizione, quantita, unita, prezzo_unitario}. Non solleva."""
    try:
        ordine = Ordine(
            societa_id=societa_id, contatto_id=contatto_id, agente_id=agente_id,
            origine=origine, canale=canale, stato=stato,
            note=(note or "").strip() or None,
            descrizione_agente=(descrizione_agente or "").strip() or None,
        )
        db.add(ordine)
        db.flush()
        for r in (righe or []):
            riga = _riga_da_dict(ordine.id, r)
            if riga is not None:
                db.add(riga)
        db.commit()
        db.refresh(ordine)
        # Se l'ordine nasce già confermato, aggiorna lo stato commerciale della società.
        if ordine.stato in (StatoOrdine.CONFERMATO, StatoOrdine.EVASO):
            aggiorna_stato_relazione(db, ordine.societa)
        logger.info("Ordine #%s creato (società=%s, %d righe, %s)",
                    ordine.id, societa_id, len(ordine.righe), canale.value)
        return ordine
    except Exception as e:
        logger.error("Creazione ordine fallita: %s", e)
        db.rollback()
        return None


def riepilogo_ordine(ordine: Ordine) -> str:
    """Testo leggibile del riepilogo di un ordine (per email/conferme)."""
    righe = []
    for r in ordine.righe:
        qta = f"{r.quantita:g}" if r.quantita is not None else ""
        prezzo = f" — € {r.prezzo_unitario:.2f}/{r.unita or 'pz'}" if r.prezzo_unitario is not None else ""
        sub = f" = € {r.subtotale:.2f}" if r.subtotale is not None else ""
        righe.append(f"- {r.descrizione}: {qta} {r.unita or ''}".rstrip() + prezzo + sub)
    corpo = "\n".join(righe) or "(nessuna riga)"
    data = ordine.data.strftime("%d/%m/%Y") if ordine.data else ""
    testo = (
        f"Ordine #{ordine.id} — {ordine.societa.nome}\n"
        f"Data: {data}\n"
        f"Stato: {ordine.stato.value}\n\n"
        f"Articoli:\n{corpo}\n\n"
        f"Totale: € {ordine.totale:.2f}"
    )
    if ordine.note:
        testo += f"\n\nNote: {ordine.note}"
    return testo


def _riga_da_dict(ordine_id: int, r: dict) -> "RigaOrdine | None":
    descr = (r.get("descrizione") or "").strip()
    if not descr:
        return None
    return RigaOrdine(
        ordine_id=ordine_id,
        descrizione=descr[:400],
        quantita=_to_float(r.get("quantita"), default=1) or 1,
        unita=(r.get("unita") or "").strip() or None,
        prezzo_unitario=_to_float(r.get("prezzo_unitario"), default=None),
    )


def trova_bozza_aperta(db: Session, societa_id: int, contatto_id: int | None = None) -> Ordine | None:
    """Bozza d'ordine già aperta per la società (e, se dato, lo stesso contatto)."""
    q = db.query(Ordine).filter(Ordine.societa_id == societa_id, Ordine.stato == StatoOrdine.BOZZA)
    if contatto_id:
        q = q.filter(Ordine.contatto_id == contatto_id)
    return q.order_by(Ordine.data.desc()).first()


def registra_ordine_conversazione(db: Session, societa_id: int, righe: list[dict],
                                  contatto_id: int | None = None, agente_id: int | None = None,
                                  origine: OrigineOrdine = OrigineOrdine.CLIENTE,
                                  canale: CanaleOrdine = CanaleOrdine.WHATSAPP,
                                  note: str | None = None,
                                  stato: StatoOrdine = StatoOrdine.BOZZA) -> tuple[Ordine | None, bool]:
    """Registra un ordine catturato in conversazione. Se esiste GIÀ una bozza aperta
    per la stessa società/contatto, la AGGIORNA (sostituisce le righe e aggiorna lo stato)
    invece di crearne una nuova: così messaggi successivi sulla stessa trattativa non
    duplicano l'ordine. Lo `stato` è deciso da chi chiama (es. l'assistente telefonico può
    confermarlo subito). Ritorna (ordine, creato): creato=False se ha aggiornato una bozza."""
    righe = [r for r in (righe or []) if (r.get("descrizione") or "").strip()]
    if not righe:
        return None, False
    esistente = trova_bozza_aperta(db, societa_id, contatto_id)
    if esistente:
        try:
            esistente.righe.clear()       # delete-orphan rimuove le vecchie righe
            db.flush()
            for r in righe:
                riga = _riga_da_dict(esistente.id, r)
                if riga is not None:
                    db.add(riga)
            if (note or "").strip():
                esistente.note = note.strip()
            esistente.stato = stato
            db.commit()
            db.refresh(esistente)
            if stato in (StatoOrdine.CONFERMATO, StatoOrdine.EVASO):
                aggiorna_stato_relazione(db, esistente.societa)
            logger.info("Ordine #%s aggiornato (no duplicato, stato=%s)", esistente.id, stato.value)
            return esistente, False
        except Exception as e:
            logger.error("Aggiornamento ordine fallito: %s", e)
            db.rollback()
            return esistente, False
    ordine = crea_ordine(db, societa_id=societa_id, righe=righe, contatto_id=contatto_id,
                         agente_id=agente_id, origine=origine, canale=canale,
                         stato=stato, note=note)
    return ordine, bool(ordine)


def _to_float(val, default=None):
    if val is None or val == "":
        return default
    try:
        return float(str(val).replace(",", "."))
    except (ValueError, TypeError):
        return default
