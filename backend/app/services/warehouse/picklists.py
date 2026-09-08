"""Pick list service.

Pick list generation:
  - For a given delivery_date, find orders with status "consolidated" (or
    "fulfilled") on that date.
  - For each order line that has a linked ConsolidationBatchLine →
    PurchaseOrderLine, compute the planned quantity:

    Allocation rule (proportional allocation of received stock):
      For each product on a given delivery date, total received stock =
      Σ quantity_received across all PO lines for that product on that
      date's batch (minus damaged, but we use the net already recorded on
      the movement ledger).  Total ordered across order lines for that
      product on that date = Σ order_line.quantity.

      If total_received >= total_ordered: every order line gets its full
      quantity (planned = order qty).

      If total_received < total_ordered: allocate proportionally by order
      line quantity — planned_qty = order_line.qty × (total_received /
      total_ordered). Rounding errors are absorbed by the last order line
      (gets the remainder so the total equals total_received exactly).

      planned_qty is then also capped at min(order_qty, allocated) so a
      single line never exceeds its ordered quantity.

  - Idempotent: deletes & recreates open pick lists for that date.
  - One PickList per delivery_date (shared), with one PickLine per order line.

Picking:
  - line.picked_quantity set; status "picked" (full) / "short" (<planned).
  - List → "picking" on first pick, "picked" when all lines picked/short.
  - When a pick list is fully picked: set its orders to "fulfilled" ONLY
    when ALL their lines across ALL pick lists are picked.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.numbers import next_number
from app.models import (
    ConsolidationBatch,
    ConsolidationBatchLine,
    Customer,
    InventoryMovement,
    Order,
    OrderLine,
    PickLine,
    PickList,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    User,
)


# --- Serializers -------------------------------------------------------------

def _pick_line_out(db: Session, pl: PickLine) -> dict:
    order_line = db.get(OrderLine, pl.order_line_id) if pl.order_line_id else None
    order = db.get(Order, order_line.order_id) if order_line else None
    customer = db.get(Customer, order.customer_id) if order else None
    prod = db.get(Product, pl.product_id) if pl.product_id else None
    return {
        "id": pl.id,
        "order_id": order.id if order else None,
        "order_number": order.order_number if order else None,
        "customer_name_en": customer.name_en if customer else "",
        "customer_name_zh": customer.name_zh if customer else "",
        "product_id": pl.product_id,
        "product_name_en": prod.name_en if prod else "",
        "product_name_zh": prod.name_zh if prod else "",
        "quantity": pl.quantity,
        "picked_quantity": pl.picked_quantity,
        "status": pl.status,
    }


def serialize_pick_list(db: Session, pk: PickList) -> dict:
    return {
        "id": pk.id,
        "pick_number": pk.pick_number,
        "delivery_date": pk.delivery_date.isoformat() if pk.delivery_date else None,
        "status": pk.status,
        "lines": [_pick_line_out(db, ln) for ln in (pk.lines or [])],
    }


# --- List / get ---------------------------------------------------------------

def list_pick_lists(
    db: Session,
    *,
    delivery_date: date | None = None,
    status: str | None = None,
) -> tuple[list[dict], int]:
    q = db.query(PickList)
    if delivery_date:
        q = q.filter(PickList.delivery_date == delivery_date)
    if status:
        q = q.filter(PickList.status == status)
    total = q.count()
    rows = q.order_by(PickList.created_at.desc()).all()
    return [serialize_pick_list(db, r) for r in rows], total


def get_pick_list(db: Session, pick_list_id: str) -> dict | None:
    pk = db.get(PickList, pick_list_id)
    if pk is None:
        return None
    return serialize_pick_list(db, pk)


# --- Generation ---------------------------------------------------------------

def _received_stock_for_product_on_date(
    db: Session, product_id: str, delivery_date: date
) -> float:
    """Total net received stock (received − damaged) for a product on a
    delivery date, summed across all POs for that date's batch.

    Walks: InventoryMovement where ref_type='inbound_receipt' and product_id
    matches and the receipt belongs to a PO whose batch.delivery_date ==
    delivery_date. The movement ledger already stores net (+received −
    damaged), so we sum quantity_delta.
    """
    # POs for the date's batch.
    po_ids_subq = (
        db.query(PurchaseOrder.id)
        .join(ConsolidationBatch, ConsolidationBatch.id == PurchaseOrder.batch_id)
        .filter(ConsolidationBatch.delivery_date == delivery_date)
    )
    po_ids = [row[0] for row in po_ids_subq.all()]
    if not po_ids:
        return 0.0
    # Inbound receipts for those POs.
    from app.models import InboundReceipt, InboundReceiptLine
    receipt_ids = [
        r[0] for r in db.query(InboundReceipt.id).filter(InboundReceipt.po_id.in_(po_ids)).all()
    ]
    if not receipt_ids:
        return 0.0
    # Sum net movements ref those receipts for this product.
    total = (
        db.query(func.coalesce(func.sum(InventoryMovement.quantity_delta), 0.0))
        .filter(
            InventoryMovement.product_id == product_id,
            InventoryMovement.ref_type == "inbound_receipt",
            InventoryMovement.ref_id.in_(receipt_ids),
        )
        .scalar()
    )
    return float(total or 0.0)


def generate_pick_lists(
    db: Session,
    *,
    delivery_date: date,
    actor: User | None = None,
) -> dict:
    """Generate (or regenerate) pick lists for a delivery date.

    Idempotent: any existing open pick lists for the date are deleted and
    recreated. Returns {"pick_list": {...}, "order_count": int, "line_count": int}.
    """
    # Idempotency: delete existing open (non-picked) pick lists for the date.
    existing = (
        db.query(PickList)
        .filter(
            PickList.delivery_date == delivery_date,
            PickList.status.in_(("open", "picking")),
        )
        .all()
    )
    for pk in existing:
        db.delete(pk)
    db.flush()

    # Orders consolidated (or fulfilled) on that date.
    orders = (
        db.query(Order)
        .filter(
            Order.delivery_date == delivery_date,
            Order.status.in_(("consolidated", "fulfilled")),
        )
        .order_by(Order.order_number)
        .all()
    )
    pick_number = next_number(db, PickList, "pick_number", "PCK", on=delivery_date)
    pick_list = PickList(
        pick_number=pick_number,
        delivery_date=delivery_date,
        status="open",
    )
    db.add(pick_list)
    db.flush()

    line_count = 0
    # Group order lines by product for proportional allocation.
    # product_id -> list of (order_line, qty)
    by_product: dict[str, list[tuple[OrderLine, float]]] = {}
    order_line_to_po_line: dict[str, str] = {}

    order_ids = [o.id for o in orders]
    all_lines: list[OrderLine] = []
    if order_ids:
        all_lines = (
            db.query(OrderLine)
            .filter(OrderLine.order_id.in_(order_ids))
            .order_by(OrderLine.order_id, OrderLine.line_no)
            .all()
        )
    # Batch lines link order lines to PO lines.
    ol_ids = [ln.id for ln in all_lines]
    batch_links = {}
    if ol_ids:
        for bl in db.query(ConsolidationBatchLine).filter(
            ConsolidationBatchLine.order_line_id.in_(ol_ids)
        ).all():
            if bl.purchase_order_line_id:
                batch_links[bl.order_line_id] = bl.purchase_order_line_id

    for ln in all_lines:
        pol_id = batch_links.get(ln.id)
        if not pol_id:
            # No linked PO line — skip (exception line).
            continue
        if not ln.product_id:
            continue
        by_product.setdefault(ln.product_id, []).append((ln, float(ln.quantity)))
        order_line_to_po_line[ln.id] = pol_id

    # Allocate per product.
    planned_by_line: dict[str, float] = {}
    for product_id, entries in by_product.items():
        total_ordered = sum(q for _, q in entries)
        received = _received_stock_for_product_on_date(db, product_id, delivery_date)
        if total_ordered <= 0:
            continue
        if received >= total_ordered:
            for ln, qty in entries:
                planned_by_line[ln.id] = qty
        else:
            ratio = received / total_ordered if total_ordered else 0.0
            allocated = 0.0
            for idx, (ln, qty) in enumerate(entries):
                if idx == len(entries) - 1:
                    # Last line absorbs rounding remainder.
                    planned = max(0.0, received - allocated)
                else:
                    planned = qty * ratio
                planned = min(planned, qty)
                planned_by_line[ln.id] = round(planned, 6)
                allocated += planned

    # Create pick lines in order/line order.
    for ln in all_lines:
        if ln.id not in planned_by_line:
            continue
        planned = planned_by_line[ln.id]
        pl = PickLine(
            pick_list_id=pick_list.id,
            order_line_id=ln.id,
            product_id=ln.product_id,
            quantity=planned,
            picked_quantity=0.0,
            status="open",
        )
        db.add(pl)
        line_count += 1
    db.flush()

    log_audit(
        db, actor, "PickList", pick_list.id, "generate",
        before=None,
        after={
            "pick_number": pick_list.pick_number,
            "delivery_date": delivery_date.isoformat(),
            "order_count": len(orders),
            "line_count": line_count,
        },
        summary=(
            f"Pick list {pick_list.pick_number} generated for "
            f"{delivery_date.isoformat()} ({len(orders)} orders, {line_count} lines)"
        ),
    )
    return {
        "pick_list": serialize_pick_list(db, pick_list),
        "order_count": len(orders),
        "line_count": line_count,
    }


def regenerate_for_date(
    db: Session,
    *,
    delivery_date: date,
    actor: User | None = None,
) -> dict:
    """Convenience used by the inbound flow after posting a receipt."""
    return generate_pick_lists(db, delivery_date=delivery_date, actor=actor)


# --- Picking -----------------------------------------------------------------

def pick_line(
    db: Session,
    *,
    pick_list: PickList,
    line: PickLine,
    picked_quantity: float,
    actor: User | None = None,
) -> PickLine:
    if pick_list.status == "cancelled":
        raise ValueError("Cannot pick on a cancelled pick list")
    before = {"picked_quantity": line.picked_quantity, "status": line.status}
    line.picked_quantity = float(picked_quantity)
    # Full → picked; short → short; zero → short (treat as short of full plan).
    if line.picked_quantity >= (line.quantity or 0.0) - 1e-9:
        line.status = "picked"
    else:
        line.status = "short"
    db.flush()

    # Update pick list status: open → picking on first pick; → picked when done.
    if pick_list.status == "open":
        pick_list.status = "picking"
    all_done = all(ln.status in ("picked", "short") for ln in pick_list.lines)
    if all_done:
        pick_list.status = "picked"
    db.flush()

    # Inventory movement: negative for the picked quantity (stock leaves).
    mv = InventoryMovement(
        product_id=line.product_id,
        quantity_delta=-abs(line.picked_quantity),
        ref_type="pick",
        ref_id=line.id,
        note=f"Pick line {line.id} on pick list {pick_list.pick_number}",
    )
    db.add(mv)
    db.flush()

    after = {
        "picked_quantity": line.picked_quantity,
        "status": line.status,
        "pick_list_status": pick_list.status,
    }
    log_audit(
        db, actor, "PickLine", line.id, "pick",
        before=before, after=after,
        summary=(
            f"Picked {line.picked_quantity} for line {line.id}; "
            f"list {pick_list.pick_number} → {pick_list.status}"
        ),
    )

    # When a pick list is fully picked, mark its orders fulfilled ONLY when
    # ALL their lines across pick lists are picked/short.
    _maybe_fulfill_orders(db, pick_list)
    db.flush()
    return line


def _maybe_fulfill_orders(db: Session, pick_list: PickList) -> None:
    """For each order represented on this pick list, set status to
    'fulfilled' only if all of its order lines have a pick line with status
    picked|short across all pick lists. Otherwise leave the order as-is
    (consolidated).
    """
    # Resolve the order_id for each pick line via the OrderLine.
    order_line_ids = {ln.order_line_id for ln in pick_list.lines if ln.order_line_id}
    if not order_line_ids:
        return
    order_ids: set[str] = set()
    for ol in db.query(OrderLine).filter(OrderLine.id.in_(order_line_ids)).all():
        order_ids.add(ol.order_id)
    for order_id in order_ids:
        order = db.get(Order, order_id)
        if order is None or order.status not in ("consolidated", "fulfilled"):
            continue
        # All order lines must have a picked/short pick line.
        ol_ids = [
            ol.id for ol in db.query(OrderLine).filter(OrderLine.order_id == order_id).all()
        ]
        if not ol_ids:
            continue
        picked_lines = (
            db.query(PickLine)
            .filter(
                PickLine.order_line_id.in_(ol_ids),
                PickLine.status.in_(("picked", "short")),
            )
            .all()
        )
        picked_ol_ids = {pl.order_line_id for pl in picked_lines}
        if set(ol_ids).issubset(picked_ol_ids):
            if order.status != "fulfilled":
                order.status = "fulfilled"
