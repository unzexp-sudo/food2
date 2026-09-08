"""Consolidation run service.

Gathers confirmed orders for a delivery date, creates a ConsolidationBatch,
resolves wholesaler per order line (supplier rule product → category →
product-wholesaler mapping), groups linked lines by (wholesaler_id,
product.category_id), and creates one PurchaseOrder per group with
ConsolidationBatchLine rows linking order lines to PO lines.

Idempotency: 409 if an open batch already exists for the delivery_date
unless all its POs are cancelled.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.numbers import next_number
from app.models import (
    ConsolidationBatch,
    ConsolidationBatchLine,
    Customer,
    Order,
    OrderLine,
    Product,
    ProductCategory,
    ProductWholesalerMapping,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierRule,
    Unit,
    User,
    Wholesaler,
)

EXCEPTION_NO_SUPPLIER = "no_supplier"


def _open_batch_exists(db: Session, delivery_date: date) -> ConsolidationBatch | None:
    """Return an open batch for the date, or None.

    A batch is 'reusable' (won't 409) only if ALL its POs are cancelled —
    in that case the caller may create a fresh batch.
    """
    batches = (
        db.query(ConsolidationBatch)
        .filter(
            ConsolidationBatch.delivery_date == delivery_date,
            ConsolidationBatch.status == "open",
        )
        .all()
    )
    for b in batches:
        # If any PO is non-cancelled, the batch is still "in flight".
        pos = db.query(PurchaseOrder).filter(PurchaseOrder.batch_id == b.id).all()
        if not pos:
            # Open batch with no POs — treat as in-flight.
            return b
        if any(po.status != "cancelled" for po in pos):
            return b
    return None


def _resolve_wholesaler(
    db: Session,
    *,
    product: Product | None,
    product_id: str | None,
    category_id: str | None,
) -> tuple[str | None, float | None]:
    """Resolve (wholesaler_id, cost_price) for a line.

    Order:
      1. SupplierRule with product_id match (priority desc).
      2. SupplierRule with category_id match (priority desc, is_default preferred).
      3. ProductWholesalerMapping for the product (any).
    Returns (None, None) if no supplier can be resolved.
    """
    if product_id:
        rules = (
            db.query(SupplierRule)
            .filter(SupplierRule.product_id == product_id)
            .order_by(SupplierRule.priority.desc(), SupplierRule.is_default.desc())
            .all()
        )
        if rules:
            wid = rules[0].wholesaler_id
            cost = _cost_for(db, product_id, wid)
            return wid, cost
    if category_id:
        rules = (
            db.query(SupplierRule)
            .filter(SupplierRule.category_id == category_id)
            .order_by(SupplierRule.priority.desc(), SupplierRule.is_default.desc())
            .all()
        )
        if rules:
            wid = rules[0].wholesaler_id
            cost = _cost_for(db, product_id, wid) if product_id else None
            return wid, cost
    if product_id:
        mapping = (
            db.query(ProductWholesalerMapping)
            .filter(ProductWholesalerMapping.product_id == product_id)
            .first()
        )
        if mapping:
            return mapping.wholesaler_id, mapping.cost_price
    return None, None


def _cost_for(db: Session, product_id: str, wholesaler_id: str) -> float | None:
    m = (
        db.query(ProductWholesalerMapping)
        .filter(
            ProductWholesalerMapping.product_id == product_id,
            ProductWholesalerMapping.wholesaler_id == wholesaler_id,
        )
        .first()
    )
    return m.cost_price if m else None


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


def _batch_summary(db: Session, b: ConsolidationBatch) -> dict:
    order_count = (
        db.query(Order)
        .join(OrderLine, OrderLine.order_id == Order.id)
        .join(ConsolidationBatchLine, ConsolidationBatchLine.order_line_id == OrderLine.id)
        .filter(ConsolidationBatchLine.batch_id == b.id)
        .distinct()
        .count()
    )
    exception_count = (
        db.query(ConsolidationBatchLine)
        .filter(
            ConsolidationBatchLine.batch_id == b.id,
            ConsolidationBatchLine.purchase_order_line_id.is_(None),
        )
        .count()
    )
    return {
        "id": b.id,
        "batch_number": b.batch_number,
        "delivery_date": b.delivery_date.isoformat() if b.delivery_date else None,
        "cutoff_at": b.cutoff_at.isoformat() if b.cutoff_at else None,
        "status": b.status,
        "order_count": order_count,
        "exception_count": exception_count,
        "created_by": b.created_by,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


def _batch_detail(db: Session, b: ConsolidationBatch) -> dict:
    summary = _batch_summary(db, b)
    pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.batch_id == b.id)
        .order_by(PurchaseOrder.created_at)
        .all()
    )
    summary["purchase_orders"] = [_po_summary(db, p) for p in pos]

    # Exceptions: batch lines without a PO link.
    exc_lines = (
        db.query(ConsolidationBatchLine)
        .filter(
            ConsolidationBatchLine.batch_id == b.id,
            ConsolidationBatchLine.purchase_order_line_id.is_(None),
        )
        .all()
    )
    exceptions: list[dict] = []
    for bl in exc_lines:
        ol = db.get(OrderLine, bl.order_line_id)
        order = db.get(Order, ol.order_id) if ol else None
        prod = db.get(Product, ol.product_id) if ol and ol.product_id else None
        exceptions.append({
            "order_id": ol.order_id if ol else None,
            "order_number": order.order_number if order else None,
            "order_line_id": bl.order_line_id,
            "product_display": (
                (f"{prod.name_en} / {prod.name_zh}" if prod else None)
                or (ol.product_display if ol else None)
            ),
            "reason": EXCEPTION_NO_SUPPLIER,
        })
    summary["exceptions"] = exceptions
    return summary


# --- Run consolidation --------------------------------------------------------

def run_consolidation(
    db: Session,
    *,
    delivery_date: date,
    actor: User | None = None,
) -> dict:
    """Returns {"batch": {...}, "purchase_orders": [...], "exceptions": [...]}.

    Raises ValueError on:
      - no confirmed orders for the date
      - an open batch already exists for the date (with non-cancelled POs)
    """
    # Idempotency check.
    existing = _open_batch_exists(db, delivery_date)
    if existing is not None:
        raise ValueError(
            f"An open consolidation batch already exists for {delivery_date.isoformat()} "
            f"({existing.batch_number})"
        )

    # Gather confirmed orders for the delivery_date.
    orders = (
        db.query(Order)
        .filter(
            Order.delivery_date == delivery_date,
            Order.status == "confirmed",
        )
        .order_by(Order.order_number)
        .all()
    )
    if not orders:
        raise ValueError(f"No confirmed orders for delivery date {delivery_date.isoformat()}")

    # Pre-load related products/units for the order lines.
    order_ids = [o.id for o in orders]
    all_lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id.in_(order_ids))
        .order_by(OrderLine.order_id, OrderLine.line_no)
        .all()
    )
    product_ids = [ln.product_id for ln in all_lines if ln.product_id]
    products: dict[str, Product] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            products[p.id] = p

    # Create the batch.
    batch_number = next_number(db, ConsolidationBatch, "batch_number", "CON", on=delivery_date)
    batch = ConsolidationBatch(
        batch_number=batch_number,
        delivery_date=delivery_date,
        cutoff_at=datetime.now(timezone.utc),
        status="open",
        created_by=actor.id if actor else None,
    )
    db.add(batch)
    db.flush()

    # Walk order lines, resolve wholesaler, and bucket linked lines.
    exceptions: list[dict] = []
    # group_key -> {product_id: {"qty": float, "unit_id": str, "lines": [(order_line_id, qty)]}}
    groups: dict[tuple[str, str | None], dict] = {}
    # Track batch-line rows so we can link them to PO lines after PO creation.
    pending_links: list[tuple] = []  # (order_line_id, group_key)

    for o in orders:
        for ln in all_lines:
            if ln.order_id != o.id:
                continue
            prod = products.get(ln.product_id) if ln.product_id else None
            category_id = prod.category_id if prod else None
            wid, cost = _resolve_wholesaler(
                db,
                product=prod,
                product_id=ln.product_id,
                category_id=category_id,
            )
            # Create the batch line regardless (so we have a record even if no supplier).
            bl = ConsolidationBatchLine(
                batch_id=batch.id,
                order_line_id=ln.id,
                purchase_order_line_id=None,
            )
            db.add(bl)
            db.flush()
            if wid is None:
                exceptions.append({
                    "order_id": o.id,
                    "order_number": o.order_number,
                    "order_line_id": ln.id,
                    "product_display": (
                        (f"{prod.name_en} / {prod.name_zh}" if prod else None)
                        or ln.product_display
                    ),
                    "reason": EXCEPTION_NO_SUPPLIER,
                })
                continue
            group_key = (wid, category_id)
            grp = groups.setdefault(group_key, {})
            entry = grp.setdefault(ln.product_id, {
                "quantity": 0.0,
                "unit_id": ln.unit_id,
                "cost_price": cost,
                "order_line_ids": [],
            })
            entry["quantity"] += float(ln.quantity)
            if cost is not None and entry["cost_price"] is None:
                entry["cost_price"] = cost
            entry["order_line_ids"].append(ln.id)
            pending_links.append((ln.id, group_key))

    # Create one PO per group.
    created_pos: list[PurchaseOrder] = []
    for (wid, category_id), grp in groups.items():
        po_number = next_number(db, PurchaseOrder, "po_number", "PO", on=delivery_date)
        po = PurchaseOrder(
            po_number=po_number,
            wholesaler_id=wid,
            category_id=category_id,
            batch_id=batch.id,
            status="draft",
            total_amount=0.0,
            created_by=actor.id if actor else None,
        )
        db.add(po)
        db.flush()
        total_amount = 0.0
        for product_id, entry in grp.items():
            qty = float(entry["quantity"])
            cost = entry["cost_price"]
            unit_id = entry["unit_id"]
            pol = PurchaseOrderLine(
                po_id=po.id,
                product_id=product_id,
                quantity_ordered=qty,
                unit_id=unit_id,
                cost_price=cost,
                quantity_received=0.0,
            )
            db.add(pol)
            db.flush()
            if cost is not None:
                total_amount += qty * float(cost)
            # Link the pending batch lines to this PO line.
            for ol_id in entry["order_line_ids"]:
                bl = (
                    db.query(ConsolidationBatchLine)
                    .filter(
                        ConsolidationBatchLine.batch_id == batch.id,
                        ConsolidationBatchLine.order_line_id == ol_id,
                    )
                    .first()
                )
                if bl is not None:
                    bl.purchase_order_line_id = pol.id
        po.total_amount = total_amount
        db.flush()
        created_pos.append(po)

    # Mark orders as consolidated.
    for o in orders:
        o.status = "consolidated"
    db.flush()

    log_audit(
        db, actor, "ConsolidationBatch", batch.id, "create",
        before=None,
        after={
            "batch_number": batch.batch_number,
            "delivery_date": delivery_date.isoformat(),
            "order_count": len(orders),
            "po_count": len(created_pos),
            "exception_count": len(exceptions),
        },
        summary=(
            f"Consolidation {batch.batch_number} ran for {delivery_date.isoformat()}: "
            f"{len(orders)} orders, {len(created_pos)} POs, {len(exceptions)} exceptions"
        ),
    )

    return {
        "batch": _batch_summary(db, batch),
        "purchase_orders": [_po_summary(db, p) for p in created_pos],
        "exceptions": exceptions,
    }


# --- List / get batches -------------------------------------------------------

def list_batches(
    db: Session,
    *,
    delivery_date: date | None = None,
    status: str | None = None,
) -> tuple[list[dict], int]:
    q = db.query(ConsolidationBatch)
    if delivery_date:
        q = q.filter(ConsolidationBatch.delivery_date == delivery_date)
    if status:
        q = q.filter(ConsolidationBatch.status == status)
    total = q.count()
    batches = q.order_by(ConsolidationBatch.created_at.desc()).all()
    return [_batch_summary(db, b) for b in batches], total


def get_batch(db: Session, batch_id: str) -> dict | None:
    b = db.get(ConsolidationBatch, batch_id)
    if b is None:
        return None
    return _batch_detail(db, b)
