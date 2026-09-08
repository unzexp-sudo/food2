"""Purchase order service: list/get, line edit (draft only), send, cancel."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.models import (
    ConsolidationBatch,
    ConsolidationBatchLine,
    Order,
    OrderLine,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    Unit,
    User,
    Wholesaler,
    ProductCategory,
)


def _po_summary(db: Session, po: PurchaseOrder) -> dict:
    w = db.get(Wholesaler, po.wholesaler_id) if po.wholesaler_id else None
    cat = db.get(ProductCategory, po.category_id) if po.category_id else None
    return {
        "id": po.id,
        "po_number": po.po_number,
        "wholesaler_id": po.wholesaler_id,
        "wholesaler_name_en": w.name_en if w else "",
        "wholesaler_name_zh": w.name_zh if w else "",
        "category_id": po.category_id,
        "category_name_en": cat.name_en if cat else None,
        "category_name_zh": cat.name_zh if cat else None,
        "batch_id": po.batch_id,
        "status": po.status,
        "total_amount": po.total_amount,
        "sent_at": po.sent_at.isoformat() if po.sent_at else None,
        "notes": po.notes,
        "created_at": po.created_at.isoformat() if po.created_at else None,
    }


def _po_line_out(db: Session, pl: PurchaseOrderLine) -> dict:
    prod = db.get(Product, pl.product_id) if pl.product_id else None
    unit = db.get(Unit, pl.unit_id) if pl.unit_id else None
    # Source order numbers via consolidation_batch_lines → order_lines → orders.
    bls = (
        db.query(ConsolidationBatchLine)
        .filter(ConsolidationBatchLine.purchase_order_line_id == pl.id)
        .all()
    )
    order_line_ids = [bl.order_line_id for bl in bls]
    order_numbers: list[str] = []
    if order_line_ids:
        rows = (
            db.query(OrderLine, Order)
            .join(Order, Order.id == OrderLine.order_id)
            .filter(OrderLine.id.in_(order_line_ids))
            .all()
        )
        seen: set[str] = set()
        for _, o in rows:
            if o.order_number and o.order_number not in seen:
                seen.add(o.order_number)
                order_numbers.append(o.order_number)
    return {
        "id": pl.id,
        "po_id": pl.po_id,
        "product_id": pl.product_id,
        "product_name_en": prod.name_en if prod else "",
        "product_name_zh": prod.name_zh if prod else "",
        "quantity_ordered": pl.quantity_ordered,
        "unit_id": pl.unit_id,
        "unit_code": unit.code if unit else None,
        "cost_price": pl.cost_price,
        "quantity_received": pl.quantity_received,
        "source_orders": order_numbers,
    }


def _po_detail(db: Session, po: PurchaseOrder) -> dict:
    out = _po_summary(db, po)
    lines = (
        db.query(PurchaseOrderLine)
        .filter(PurchaseOrderLine.po_id == po.id)
        .order_by(PurchaseOrderLine.id)
        .all()
    )
    out["lines"] = [_po_line_out(db, pl) for pl in lines]
    return out


# --- List / get ---------------------------------------------------------------

def list_purchase_orders(
    db: Session,
    *,
    status: str | None = None,
    wholesaler_id: str | None = None,
    delivery_date=None,
) -> tuple[list[dict], int]:
    """If delivery_date is given, filter by the batch's delivery_date."""
    q = db.query(PurchaseOrder)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    if wholesaler_id:
        q = q.filter(PurchaseOrder.wholesaler_id == wholesaler_id)
    if delivery_date is not None:
        # Join through ConsolidationBatch to its delivery_date.
        q = (
            q.join(ConsolidationBatch, ConsolidationBatch.id == PurchaseOrder.batch_id)
            .filter(ConsolidationBatch.delivery_date == delivery_date)
        )
    total = q.count()
    pos = q.order_by(PurchaseOrder.created_at.desc()).all()
    return [_po_summary(db, p) for p in pos], total


def get_purchase_order(db: Session, po_id: str) -> dict | None:
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        return None
    return _po_detail(db, po)


# --- Line edit (draft only) ---------------------------------------------------

def patch_po_lines(
    db: Session,
    po: PurchaseOrder,
    *,
    edits: list[dict],
    actor: User | None = None,
) -> PurchaseOrder:
    if po.status != "draft":
        raise ValueError(f"Cannot edit lines of PO in status '{po.status}' (only draft)")
    before = _po_detail(db, po)
    line_ids = [e["id"] for e in edits if e.get("id")]
    lines: dict[str, PurchaseOrderLine] = {}
    if line_ids:
        for pl in db.query(PurchaseOrderLine).filter(
            PurchaseOrderLine.id.in_(line_ids)
        ).all():
            lines[pl.id] = pl
    for e in edits:
        pl = lines.get(e["id"])
        if pl is None or pl.po_id != po.id:
            raise ValueError(f"PO line {e.get('id')} does not belong to PO {po.po_number}")
        if e.get("quantity_ordered") is not None:
            pl.quantity_ordered = float(e["quantity_ordered"])
        if e.get("cost_price") is not None:
            pl.cost_price = float(e["cost_price"])
    db.flush()
    # Recompute total_amount = Σ qty × cost.
    total = 0.0
    for pl in db.query(PurchaseOrderLine).filter(PurchaseOrderLine.po_id == po.id).all():
        if pl.cost_price is not None:
            total += float(pl.quantity_ordered) * float(pl.cost_price)
    po.total_amount = total
    db.flush()
    after = _po_detail(db, po)
    log_audit(
        db, actor, "PurchaseOrder", po.id, "update_lines",
        before=before, after=after,
        summary=f"PO {po.po_number} lines edited; total_amount={po.total_amount}",
    )
    return po


# --- Send / cancel ------------------------------------------------------------

def send_po(
    db: Session,
    po: PurchaseOrder,
    *,
    actor: User | None = None,
) -> PurchaseOrder:
    if po.status != "draft":
        raise ValueError(f"Cannot send PO in status '{po.status}' (only draft)")
    before = {"status": po.status, "sent_at": po.sent_at}
    po.status = "sent"
    po.sent_at = datetime.now(timezone.utc)
    db.flush()
    log_audit(
        db, actor, "PurchaseOrder", po.id, "send",
        before=before, after={"status": po.status, "sent_at": po.sent_at.isoformat() if po.sent_at else None},
        summary=f"PO {po.po_number} sent",
    )
    return po


def cancel_po(
    db: Session,
    po: PurchaseOrder,
    *,
    actor: User | None = None,
) -> PurchaseOrder:
    if po.status not in {"draft", "sent"}:
        raise ValueError(f"Cannot cancel PO in status '{po.status}' (only draft|sent)")
    before = {"status": po.status}
    po.status = "cancelled"
    db.flush()
    log_audit(
        db, actor, "PurchaseOrder", po.id, "cancel",
        before=before, after={"status": po.status},
        summary=f"PO {po.po_number} cancelled",
    )
    return po
