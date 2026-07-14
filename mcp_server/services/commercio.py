"""Catalogo & ordini generici (riusabili su più verticali).

Le tabelle hanno nomi neutri (catalog_items/orders/order_items); le label mostrate in GUI arrivano
dalla config del tenant (azienda.commercio_labels). Qui c'è la logica di creazione ordine.

Regola di design: `unit_price` viene COPIATO dal catalogo al momento della creazione della riga,
non referenziato live — così modificare un prezzo a listino NON altera gli ordini passati.
"""

import json
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from database import CatalogItem, Order, OrderItem, OrderStatus, Contatto

logger = logging.getLogger(__name__)


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


def cerca(db: Session, azienda_id: int | None, testo: str = "", marca: str = "",
          categoria: str = "", unita_vendita: str = "", formato: str = "",
          prezzo_min: float | None = None, prezzo_max: float | None = None,
          prezzo_conf_min: float | None = None, prezzo_conf_max: float | None = None,
          limite: int = 30) -> dict:
    """Cerca nel catalogo del tenant. Filtri colonna (marca/unità/prezzo unitario) in SQL,
    filtri su JSON (categoria/formato/prezzo confezione) e testo libero in Python.

    Ritorna {ok, n, troncato, prodotti:[...]}. Non solleva.
    """
    try:
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

        cat_l = categoria.strip().lower()
        fmt_l = formato.strip().lower()
        txt_l = testo.strip().lower()

        out = []
        for it in q.order_by(CatalogItem.name).all():
            if cat_l and cat_l not in " ".join(str(c).lower() for c in _cat(it)):
                continue
            attrs = _attrs(it)
            if fmt_l and fmt_l not in str(attrs.get("formato", "")).lower():
                continue
            pc = _to_float(attrs.get("prezzo_confezione"))
            if prezzo_conf_min is not None and (pc is None or pc < prezzo_conf_min):
                continue
            if prezzo_conf_max is not None and (pc is None or pc > prezzo_conf_max):
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
                return {"ok": True, "n": len(out), "troncato": True, "prodotti": out}
        return {"ok": True, "n": len(out), "troncato": False, "prodotti": out}
    except Exception as ex:
        logger.error("cerca catalogo fallita (tenant %s): %s", azienda_id, ex)
        return {"ok": False, "errore": "Errore interno nella ricerca del catalogo.", "prodotti": []}


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
        n = 0
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
            if up is not None:
                total += Decimal(str(up)) * qty
            n += 1

        if n == 0:
            db.rollback()
            return {"ok": False, "errore": "Nessuna riga valida (catalog_item_id inesistente)."}

        ordine.total = total
        db.commit()
        logger.info("Ordine %s creato per contatto %s (%d righe, tot %s)", ordine.id, contatto_id, n, total)
        return {"ok": True, "ordine_id": ordine.id, "total": float(total), "n_righe": n}
    except Exception as ex:
        logger.error("crea_ordine fallito (contatto %s): %s", contatto_id, ex)
        db.rollback()
        return {"ok": False, "errore": "Errore interno nella creazione dell'ordine."}
