"""Catalogo & ordini generici (riusabili su più verticali).

Le tabelle hanno nomi neutri (catalog_items/orders/order_items); le label mostrate in GUI arrivano
dalla config del tenant (azienda.commercio_labels). Qui c'è la logica di creazione ordine.

Regola di design: `unit_price` viene COPIATO dal catalogo al momento della creazione della riga,
non referenziato live — così modificare un prezzo a listino NON altera gli ordini passati.
"""

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from database import CatalogItem, Order, OrderItem, OrderStatus, Contatto

logger = logging.getLogger(__name__)


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
