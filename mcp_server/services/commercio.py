"""Catalogo & ordini generici (riusabili su più verticali).

Le tabelle hanno nomi neutri (catalog_items/orders/order_items); le label mostrate in GUI arrivano
dalla config del tenant (azienda.commercio_labels). Qui c'è la logica di creazione ordine.

Regola di design: `unit_price` viene COPIATO dal catalogo al momento della creazione della riga,
non referenziato live — così modificare un prezzo a listino NON altera gli ordini passati.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from database import CatalogItem, Order, OrderItem, OrderStatus, Contatto

logger = logging.getLogger(__name__)

# Rilevanza minima (similarità coseno) perché un risultato semantico sia considerato PERTINENTE.
# Misurato sul catalogo bevande: match reali 0.45-0.75; rumore (prodotto inesistente) 0.23-0.29.
# Sotto soglia si preferisce "non l'abbiamo" a proporre un articolo a caso.
RILEVANZA_MIN = float(os.getenv("CATALOGO_RILEVANZA_MIN", "0.35"))


def _is_postgres(db: Session) -> bool:
    try:
        return db.bind.dialect.name == "postgresql"
    except Exception:
        return False


# --- helper JSON (le colonne aliases/attributes/category_path sono Text con dentro JSON) ---

def _json(raw, default):
    if raw in (None, ""):
        return default
    try:
        v = json.loads(raw)
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


def _attrs(item) -> dict:
    return _json(getattr(item, "attributes", None), {})


def _cat(item) -> list:
    return _json(getattr(item, "category_path", None), [])


def _aliases(item) -> list:
    return _json(getattr(item, "aliases", None), [])


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _item_dict(item) -> dict:
    """Rappresentazione compatta di un prodotto per l'assistente."""
    attrs = _attrs(item)
    return {
        "catalog_item_id": item.id,
        "nome": item.name,
        "marca": item.brand,
        "categoria": _cat(item),
        "unita_vendita": item.unit_of_sale,
        "formato": attrs.get("formato"),
        "prezzo_unitario": _to_float(item.price),
        "prezzo_confezione": _to_float(attrs.get("prezzo_confezione")),
        "pezzi_per_confezione": attrs.get("pezzi_per_confezione"),
        "sku": item.sku,
        "descrizione": item.description,
    }


def _passa_filtri_json(it, cat_l: str, fmt_l: str,
                       prezzo_conf_min, prezzo_conf_max) -> bool:
    """Filtri che vivono nel JSON (categoria in category_path, formato/prezzo_confezione in attributes)."""
    if cat_l and cat_l not in " ".join(str(c).lower() for c in _cat(it)):
        return False
    attrs = _attrs(it)
    if fmt_l and fmt_l not in str(attrs.get("formato", "")).lower():
        return False
    pc = _to_float(attrs.get("prezzo_confezione"))
    if prezzo_conf_min is not None and (pc is None or pc < prezzo_conf_min):
        return False
    if prezzo_conf_max is not None and (pc is None or pc > prezzo_conf_max):
        return False
    return True


def cerca(db: Session, azienda_id: int | None, testo: str = "", marca: str = "",
          categoria: str = "", unita_vendita: str = "", formato: str = "",
          prezzo_min: float | None = None, prezzo_max: float | None = None,
          prezzo_conf_min: float | None = None, prezzo_conf_max: float | None = None,
          limite: int = 30) -> dict:
    """Ricerca IBRIDA nel catalogo del tenant. I filtri strutturati (marca/unità/prezzo in SQL,
    categoria/formato/prezzo confezione su JSON) sono vincoli hard; se c'è `testo` i risultati sono
    ORDINATI per similarità semantica (embedding) invece che per nome — così "una bionda leggera"
    trova le birre giuste anche senza parole esatte. Senza `testo`, ordine per nome.

    Ritorna {ok, n, altri_disponibili, prodotti:[...]}. Non solleva.
    """
    try:
        cat_l = categoria.strip().lower()
        fmt_l = formato.strip().lower()

        # Ramo SEMANTICO: testo libero + Postgres/pgvector. Se non rende risultati (es. catalogo non
        # ancora indicizzato) si ricade sul ramo testuale sotto.
        if testo.strip() and _is_postgres(db):
            try:
                sem = _cerca_semantica(db, azienda_id, testo, marca, unita_vendita,
                                       prezzo_min, prezzo_max, cat_l, fmt_l,
                                       prezzo_conf_min, prezzo_conf_max, limite)
                if sem is not None and sem["n"] > 0:
                    return sem
            except Exception as e:
                logger.warning("ricerca semantica catalogo fallita, fallback testuale: %s", e)

        # Ramo TESTUALE/strutturato (fallback e caso senza testo): substring su nome/marca/alias.
        q = db.query(CatalogItem)
        if azienda_id:
            q = q.filter(CatalogItem.azienda_id == azienda_id)
        if marca.strip():
            q = q.filter(CatalogItem.brand.ilike(f"%{marca.strip()}%"))
        if unita_vendita.strip():
            q = q.filter(CatalogItem.unit_of_sale.ilike(f"%{unita_vendita.strip()}%"))
        if prezzo_min is not None:
            q = q.filter(CatalogItem.price >= prezzo_min)
        if prezzo_max is not None:
            q = q.filter(CatalogItem.price <= prezzo_max)

        txt_l = testo.strip().lower()
        out = []
        for it in q.order_by(CatalogItem.name).all():
            if not _passa_filtri_json(it, cat_l, fmt_l, prezzo_conf_min, prezzo_conf_max):
                continue
            if txt_l:
                hay = " ".join([
                    it.name or "", it.brand or "", it.sku or "",
                    " ".join(str(a) for a in _aliases(it)),
                ]).lower()
                if txt_l not in hay:
                    continue
            out.append(_item_dict(it))
            if len(out) >= limite:
                return {"ok": True, "n": len(out), "altri_disponibili": True, "prodotti": out}
        return {"ok": True, "n": len(out), "altri_disponibili": False, "prodotti": out}
    except Exception as ex:
        logger.error("cerca catalogo fallita (tenant %s): %s", azienda_id, ex)
        return {"ok": False, "errore": "Errore interno nella ricerca del catalogo.", "prodotti": []}


# --- ricerca semantica (embedding + pgvector), riusa l'infra dei documenti (services.vettore) ---

def _embed_text(item) -> str:
    """Testo da embeddare per un prodotto: nome, marca, categorie, alias, formato, unità, descrizione."""
    a = _attrs(item)
    parti = [item.name, item.brand]
    parti += [str(c) for c in _cat(item)]
    parti += [str(x) for x in _aliases(item)]
    if a.get("formato"):
        parti.append(f"formato {a['formato']}")
    if item.unit_of_sale:
        parti.append(item.unit_of_sale)
    if item.description:
        parti.append(item.description)
    return " | ".join(p for p in (str(x).strip() for x in parti if x) if p)


def _cerca_semantica(db, azienda_id, testo, marca, unita_vendita,
                     prezzo_min, prezzo_max, cat_l, fmt_l,
                     prezzo_conf_min, prezzo_conf_max, limite) -> dict | None:
    """Top-K per similarità coseno (pgvector) DENTRO ai vincoli strutturati. Ritorna None se il ramo
    non è applicabile (nessun embedding), così il chiamante ripiega sul testuale."""
    from services import vettore
    qemb = vettore.embed_uno(testo.strip())

    where = ["embedding_vec is not null"]
    params = {"q": vettore._vec_literal(qemb), "k": max(limite * 4, 40),
              # Distanza coseno = 1 - similarità: taglia i risultati sotto la rilevanza minima,
              # così una richiesta senza corrispondenze NON restituisce articoli a caso.
              "maxdist": 1.0 - RILEVANZA_MIN}
    where.append("(embedding_vec <=> cast(:q as vector)) <= :maxdist")
    if azienda_id:
        where.append("azienda_id = :aid"); params["aid"] = azienda_id
    if marca.strip():
        where.append("brand ilike :marca"); params["marca"] = f"%{marca.strip()}%"
    if unita_vendita.strip():
        where.append("unit_of_sale ilike :uv"); params["uv"] = f"%{unita_vendita.strip()}%"
    # Categoria come filtro SQL (non post-filtro): altrimenti il troncamento del top-K vettoriale
    # può perdere articoli validi della categoria (es. 3 succhi su 4).
    if cat_l:
        where.append("category_path ilike :cat"); params["cat"] = f"%{cat_l}%"
    if prezzo_min is not None:
        where.append("price >= :pmin"); params["pmin"] = prezzo_min
    if prezzo_max is not None:
        where.append("price <= :pmax"); params["pmax"] = prezzo_max

    sql = text(
        "select id, 1 - (embedding_vec <=> cast(:q as vector)) as score "
        "from catalog_items where " + " and ".join(where) +
        " order by embedding_vec <=> cast(:q as vector) limit :k"
    )
    rows = db.execute(sql, params).mappings().all()
    if not rows:
        return None   # catalogo non indicizzato o nessun match nei vincoli → prova il testuale

    score_by = {r["id"]: round(float(r["score"]), 4) for r in rows}
    ids = [r["id"] for r in rows]
    by_id = {it.id: it for it in db.query(CatalogItem).filter(CatalogItem.id.in_(ids)).all()}

    out = []
    for iid in ids:   # preserva l'ordine di rilevanza restituito da pgvector
        it = by_id.get(iid)
        if not it or not _passa_filtri_json(it, cat_l, fmt_l, prezzo_conf_min, prezzo_conf_max):
            continue
        d = _item_dict(it)
        d["rilevanza"] = score_by.get(iid)
        out.append(d)
        if len(out) >= limite:
            return {"ok": True, "n": len(out), "altri_disponibili": True, "prodotti": out}
    return {"ok": True, "n": len(out), "altri_disponibili": False, "prodotti": out}


def _conta(db, azienda_id, solo_indicizzati: bool) -> int:
    sql = "select count(*) from catalog_items where 1=1"
    params = {}
    if azienda_id:
        sql += " and azienda_id = :aid"; params["aid"] = azienda_id
    if solo_indicizzati:
        sql += " and embedding_vec is not null"
    return int(db.execute(text(sql), params).scalar() or 0)


def indicizza(db: Session, azienda_id: int | None = None, full: bool = False) -> dict:
    """(Ri)calcola gli embedding dei prodotti e li salva in `embedding_vec` (pgvector).
    `full=False` indicizza solo i non ancora indicizzati; `full=True` rifà tutto. Idempotente."""
    if not _is_postgres(db):
        return {"ok": False, "errore": "Indicizzazione disponibile solo su Postgres/Supabase."}
    try:
        from services import vettore
        # Auto-provisioning: crea la colonna pgvector se manca (l'estensione 'vector' è già attiva
        # per i documenti). Idempotente → niente migrazione manuale.
        db.execute(text("alter table catalog_items add column if not exists embedding_vec vector(1536)"))
        db.commit()

        base = "select id from catalog_items where 1=1"
        params = {}
        if azienda_id:
            base += " and azienda_id = :aid"; params["aid"] = azienda_id
        if not full:
            base += " and embedding_vec is null"
        ids = [r[0] for r in db.execute(text(base), params).all()]

        n = 0
        if ids:
            for i in range(0, len(ids), 200):     # a blocchi, per non tenere troppo in RAM
                lotto = ids[i:i + 200]
                items = db.query(CatalogItem).filter(CatalogItem.id.in_(lotto)).all()
                embs = vettore.embed_batch([_embed_text(it) for it in items])
                for it, emb in zip(items, embs):
                    db.execute(
                        text("update catalog_items set embedding_vec = cast(:v as vector) where id = :id"),
                        {"v": vettore._vec_literal(emb), "id": it.id})
                    n += 1
                db.commit()
        totali = _conta(db, azienda_id, solo_indicizzati=False)
        indicizzati = _conta(db, azienda_id, solo_indicizzati=True)
        logger.info("Catalogo indicizzato (tenant %s): +%d, ora %d/%d", azienda_id, n, indicizzati, totali)
        return {"ok": True, "nuovi": n, "indicizzati": indicizzati, "totali": totali}
    except Exception as ex:
        logger.error("indicizza catalogo fallita (tenant %s): %s", azienda_id, ex)
        db.rollback()
        return {"ok": False, "errore": "Errore interno nell'indicizzazione del catalogo."}


def _valori_distinti(db: Session, azienda_id: int | None):
    """(marche, categorie, unita, formati) distinti e ordinati per il tenant.
    Se azienda_id è None si affida a RLS (canale WhatsApp, come entita.blocco_prompt)."""
    q = db.query(CatalogItem)
    if azienda_id:
        q = q.filter(CatalogItem.azienda_id == azienda_id)
    items = q.all()
    marche, categorie, unita, formati = set(), set(), set(), set()
    for it in items:
        if it.brand:
            marche.add(it.brand.strip())
        if it.unit_of_sale:
            unita.add(it.unit_of_sale.strip())
        for c in _cat(it):
            if str(c).strip():
                categorie.add(str(c).strip())
        f = _attrs(it).get("formato")
        if f and str(f).strip():
            formati.add(str(f).strip())
    return len(items), sorted(marche), sorted(categorie), sorted(unita), sorted(formati)


def blocco_prompt(db: Session, azienda_id: int | None) -> str:
    """Blocco dinamico iniettato nella configurazione: elenca i valori distinti del catalogo
    (marca/categoria/unità/formato) e spiega come cercare. Vuoto se il tenant non ha catalogo."""
    n, marche, categorie, unita, formati = _valori_distinti(db, azienda_id)
    if n == 0:
        return ""

    def _lista(vals):
        return ", ".join(vals) if vals else "(nessuno)"

    return (
        "\n\n=== CATALOGO PRODOTTI ===\n"
        f"Il catalogo contiene {n} prodotti. Per trovarli usa lo strumento `cerca_catalogo`; "
        "per registrare un ordine usa `crea_ordine` (con i catalog_item_id trovati).\n"
        "Filtri disponibili in `cerca_catalogo`: `testo` (ricerca libera su nome/marca/sinonimi), "
        "`marca`, `categoria`, `unita_vendita`, `formato`, e per RANGE di prezzo "
        "`prezzo_min`/`prezzo_max` (prezzo unitario) e `prezzo_conf_min`/`prezzo_conf_max` (prezzo confezione).\n"
        "Usa ESATTAMENTE questi valori nei filtri (sono tutti i valori presenti a catalogo):\n"
        f"- Marca: {_lista(marche)}\n"
        f"- Categoria: {_lista(categorie)}\n"
        f"- Unità di vendita: {_lista(unita)}\n"
        f"- Formato: {_lista(formati)}\n"
        "Non inventare prodotti o prezzi: se un prodotto non compare nella ricerca, dillo al cliente."
    )


def _parse_data(s: str):
    """Parsa una data (preferito AAAA-MM-GG; accetta anche GG/MM/AAAA e ISO). None se vuota/illegibile."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def leggi_ordini(db: Session, azienda_id: int | None, contatto_id: int,
                 data_da: str = "", data_a: str = "", catalog_item_ids: list | None = None,
                 limite: int = 5) -> dict:
    """Storico ordini DEL contatto (per riordini e recap). Filtri: periodo (data_da/data_a),
    lista di prodotti (catalog_item_ids), e numero massimo (limite, i più recenti prima).

    Ogni ordine include le righe con `catalog_item_id` (per riordinare) + nome/quantità/prezzo.
    Ritorna {ok, n, ordini:[...]}. Non solleva.
    """
    try:
        q = db.query(Order).filter(Order.contatto_id == contatto_id)
        if azienda_id:
            q = q.filter(Order.azienda_id == azienda_id)
        dd = _parse_data(data_da)
        if dd:
            q = q.filter(Order.created_at >= dd)
        da = _parse_data(data_a)
        if da:
            q = q.filter(Order.created_at < da + timedelta(days=1))   # data_a inclusiva
        ids = [int(x) for x in (catalog_item_ids or []) if str(x).strip().lstrip("-").isdigit()]
        if ids:
            sub = db.query(OrderItem.order_id).filter(OrderItem.catalog_item_id.in_(ids))
            q = q.filter(Order.id.in_(sub))

        q = q.order_by(Order.created_at.desc())
        if limite and limite > 0:
            q = q.limit(limite)
        ordini = q.all()

        out = []
        for o in ordini:
            righe = db.query(OrderItem).filter(OrderItem.order_id == o.id).all()
            catids = [r.catalog_item_id for r in righe]
            nomi = ({it.id: it.name for it in
                     db.query(CatalogItem).filter(CatalogItem.id.in_(catids)).all()} if catids else {})
            out.append({
                "ordine_id": o.id,
                "data": o.created_at.strftime("%Y-%m-%d") if o.created_at else None,
                "stato": o.status.value if o.status else None,
                "totale": _to_float(o.total),
                "note": o.notes,
                "righe": [{
                    "catalog_item_id": r.catalog_item_id,
                    "nome": nomi.get(r.catalog_item_id),
                    "quantita": r.quantity,
                    "prezzo_unitario": _to_float(r.unit_price),
                } for r in righe],
            })
        return {"ok": True, "n": len(out), "ordini": out}
    except Exception as ex:
        logger.error("leggi_ordini fallita (contatto %s): %s", contatto_id, ex)
        return {"ok": False, "errore": "Errore interno nella lettura degli ordini.", "ordini": []}


def crea_ordine(db: Session, azienda_id: int | None, contatto_id: int, righe: list,
                note: str = "", created_by: str | None = None) -> dict:
    """Crea un ordine + le sue righe in UNA transazione, copiando i prezzi dal catalogo.

    `righe` = lista di {catalog_item_id, quantita}. Il totale è la somma delle righe.
    Ritorna {ok, ordine_id, total, n_righe} oppure {ok: False, errore}. Non solleva.
    """
    try:
        c = db.get(Contatto, contatto_id)
        if not c:
            return {"ok": False, "errore": "Contatto inesistente."}
        aid = azienda_id or c.azienda_id

        ordine = Order(azienda_id=aid, contatto_id=contatto_id, status=OrderStatus.DRAFT,
                       notes=(note or "").strip() or None, created_by=created_by)
        db.add(ordine)
        db.flush()   # per avere ordine.id

        total = Decimal("0")
        righe_out = []
        for r in (righe or []):
            cid = r.get("catalog_item_id") or r.get("catalogo_id")
            try:
                qty = int(r.get("quantita") or r.get("quantity") or 1)
            except (TypeError, ValueError):
                qty = 1
            if not cid or qty <= 0:
                continue
            item = db.get(CatalogItem, cid)
            if not item or item.azienda_id not in (None, aid):
                continue      # id non valido o di un altro tenant → salta
            up = item.price   # COPIA il prezzo al momento dell'ordine (storico immutabile)
            db.add(OrderItem(order_id=ordine.id, catalog_item_id=item.id, quantity=qty, unit_price=up))
            subtot = Decimal(str(up)) * qty if up is not None else None
            if subtot is not None:
                total += subtot
            righe_out.append({
                "catalog_item_id": item.id, "nome": item.name, "quantita": qty,
                "prezzo_unitario": _to_float(up),
                "subtotale": float(subtot) if subtot is not None else None,
            })

        if not righe_out:
            db.rollback()
            return {"ok": False, "errore": "Nessuna riga valida (catalog_item_id inesistente)."}

        ordine.total = total
        db.commit()
        logger.info("Ordine %s creato per contatto %s (%d righe, tot %s)",
                    ordine.id, contatto_id, len(righe_out), total)
        return {"ok": True, "ordine_id": ordine.id, "total": float(total),
                "n_righe": len(righe_out), "righe": righe_out}
    except Exception as ex:
        logger.error("crea_ordine fallito (contatto %s): %s", contatto_id, ex)
        db.rollback()
        return {"ok": False, "errore": "Errore interno nella creazione dell'ordine."}
