"""Order management service: list/get/create, line patch, confirm/reject/clarify/resubmit.

All state-changing helpers add audit rows via `app.core.audit.log_audit`; the caller
commits the session. The contract-price lock logic is shared with the auto-confirm
handler via `lock_contract_prices`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.numbers import next_number
from app.models import (
    ContractPrice,
    Customer,
    Order,
    OrderLine,
    Product,
    Unit,
    User,
)

# Statuses that allow transitioning to "confirmed" (manual confirm path).
CONFIRMABLE = {"draft", "pending_confirmation", "needs_clarification"}
# Statuses that allow editing order lines.
LINE_EDITABLE = {"draft", "pending_confirmation", "needs_clarification"}


# --- Serializers --------------------------------------------------------------

def _order_line_out(ln: OrderLine, *, product: Product | None = None,
                     unit: Unit | None = None) -> dict:
    return {
        "id": ln.id,
        "order_id": ln.order_id,
        "line_no": ln.line_no,
        "raw_text": ln.raw_text,
        "product_id": ln.product_id,
        "product_display": ln.product_display,
        "quantity": ln.quantity,
        "unit_id": ln.unit_id,
        "unit_code": unit.code if unit else (ln.unit.code if ln.unit else None),
        "unit_price": ln.unit_price,
        "confidence": ln.confidence,
        "match_method": ln.match_method,
    }


def _order_out(db: Session, o: Order, *, include_lines: bool = True) -> dict:
    customer = db.get(Customer, o.customer_id)
    lines_out: list[dict] = []
    if include_lines:
        # Eager-load related rows for the lines
        lines = (
            db.query(OrderLine)
            .filter(OrderLine.order_id == o.id)
            .order_by(OrderLine.line_no)
            .all()
        )
        product_ids = [ln.product_id for ln in lines if ln.product_id]
        unit_ids = [ln.unit_id for ln in lines if ln.unit_id]
        products: dict[str, Product] = {}
        units: dict[str, Unit] = {}
        if product_ids:
            for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
                products[p.id] = p
        if unit_ids:
            for u in db.query(Unit).filter(Unit.id.in_(unit_ids)).all():
                units[u.id] = u
        for ln in lines:
            lines_out.append(
                _order_line_out(
                    ln,
                    product=products.get(ln.product_id) if ln.product_id else None,
                    unit=units.get(ln.unit_id) if ln.unit_id else None,
                )
            )
    return {
        "id": o.id,
        "order_number": o.order_number,
        "customer_id": o.customer_id,
        "customer_name_en": customer.name_en if customer else "",
        "customer_name_zh": customer.name_zh if customer else "",
        "status": o.status,
        "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
        "source_type": o.source_type,
        "intake_document_id": o.intake_document_id,
        "standing_template_id": o.standing_template_id,
        "overall_confidence": o.overall_confidence,
        "confirmed_by": o.confirmed_by,
        "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
        "notes": o.notes,
        "line_count": len(lines_out) if include_lines else _count_lines(db, o.id),
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "lines": lines_out,
    }


def _count_lines(db: Session, order_id: str) -> int:
    return db.query(OrderLine).filter(OrderLine.order_id == order_id).count()


# --- List / get ---------------------------------------------------------------

def list_orders(
    db: Session,
    *,
    status: str | None = None,
    customer_id: str | None = None,
    delivery_date: date | None = None,
    q: str | None = None,
) -> tuple[list[dict], int]:
    q_ = db.query(Order)
    if status:
        q_ = q_.filter(Order.status == status)
    if customer_id:
        q_ = q_.filter(Order.customer_id == customer_id)
    if delivery_date:
        q_ = q_.filter(Order.delivery_date == delivery_date)
    if q:
        like = f"%{q}%"
        q_ = q_.filter(Order.order_number.ilike(like))
    total = q_.count()
    orders = q_.order_by(Order.created_at.desc()).all()
    items = [_order_out(db, o, include_lines=False) for o in orders]
    return items, total


def get_order(db: Session, order_id: str) -> Order | None:
    return db.get(Order, order_id)


def serialize_order(db: Session, o: Order) -> dict:
    return _order_out(db, o, include_lines=True)


# --- Create -------------------------------------------------------------------

def create_order(
    db: Session,
    *,
    customer_id: str,
    delivery_date: date,
    lines: list[dict],
    notes: str | None = None,
    actor: User | None = None,
) -> Order:
    """Manual order create — source_type "manual", status "draft", confidence 1.0."""
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise ValueError(f"Customer not found: {customer_id}")
    if not lines:
        raise ValueError("Order must have at least one line")

    # Pre-fetch products & units for the supplied lines.
    product_ids = [ln.get("product_id") for ln in lines if ln.get("product_id")]
    unit_ids = [ln.get("unit_id") for ln in lines if ln.get("unit_id")]
    products: dict[str, Product] = {}
    units: dict[str, Unit] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            products[p.id] = p
    if unit_ids:
        for u in db.query(Unit).filter(Unit.id.in_(unit_ids)).all():
            units[u.id] = u

    order_number = next_number(db, Order, "order_number", "ORD")
    order = Order(
        order_number=order_number,
        customer_id=customer_id,
        status="draft",
        delivery_date=delivery_date,
        source_type="manual",
        overall_confidence=1.0,
        created_by=actor.id if actor else None,
        notes=notes,
    )
    db.add(order)
    db.flush()

    for idx, ln in enumerate(lines, start=1):
        prod = products.get(ln.get("product_id")) if ln.get("product_id") else None
        unit = units.get(ln.get("unit_id")) if ln.get("unit_id") else None
        product_display = ln.get("product_display")
        if not product_display and prod is not None:
            product_display = f"{prod.name_en} / {prod.name_zh}"
        ol = OrderLine(
            order_id=order.id,
            line_no=idx,
            raw_text=ln.get("raw_text") or product_display,
            product_id=ln.get("product_id"),
            product_display=product_display,
            quantity=float(ln["quantity"]),
            unit_id=ln.get("unit_id"),
            unit_price=ln.get("unit_price"),
            confidence=1.0,
            match_method="manual" if ln.get("product_id") else "unmatched",
        )
        db.add(ol)
    db.flush()

    log_audit(
        db, actor, "Order", order.id, "create",
        before=None,
        after={
            "order_number": order.order_number,
            "customer_id": customer_id,
            "status": order.status,
            "delivery_date": delivery_date.isoformat(),
            "source_type": "manual",
            "line_count": len(lines),
        },
        summary=f"Manual order {order.order_number} created ({len(lines)} lines)",
    )
    return order


# --- Bulk replace lines -------------------------------------------------------

def replace_lines(
    db: Session,
    order: Order,
    *,
    lines: list[dict],
    actor: User | None = None,
) -> Order:
    if order.status not in LINE_EDITABLE:
        raise ValueError(
            f"Cannot edit lines of order in status '{order.status}'"
        )
    if not lines:
        raise ValueError("Order must have at least one line")

    # Capture before state for audit.
    before = _order_out(db, order, include_lines=True)

    # Pre-fetch related rows for the new lines.
    product_ids = [ln.get("product_id") for ln in lines if ln.get("product_id")]
    unit_ids = [ln.get("unit_id") for ln in lines if ln.get("unit_id")]
    products: dict[str, Product] = {}
    units: dict[str, Unit] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            products[p.id] = p
    if unit_ids:
        for u in db.query(Unit).filter(Unit.id.in_(unit_ids)).all():
            units[u.id] = u

    # Remove existing lines.
    db.query(OrderLine).filter(OrderLine.order_id == order.id).delete(
        synchronize_session=False
    )
    db.flush()

    # Insert new lines.
    for idx, ln in enumerate(lines, start=1):
        prod = products.get(ln.get("product_id")) if ln.get("product_id") else None
        unit = units.get(ln.get("unit_id")) if ln.get("unit_id") else None
        product_display = ln.get("product_display")
        if not product_display and prod is not None:
            product_display = f"{prod.name_en} / {prod.name_zh}"
        ol = OrderLine(
            order_id=order.id,
            line_no=idx,
            raw_text=ln.get("raw_text") or product_display,
            product_id=ln.get("product_id"),
            product_display=product_display,
            quantity=float(ln["quantity"]),
            unit_id=ln.get("unit_id"),
            unit_price=ln.get("unit_price"),
            confidence=1.0,
            match_method="manual" if ln.get("product_id") else "unmatched",
        )
        db.add(ol)
    db.flush()

    # Recompute overall_confidence as mean of line confidences.
    line_count = db.query(OrderLine).filter(OrderLine.order_id == order.id).count()
    if line_count:
        from sqlalchemy import func as _func
        avg = db.query(_func.avg(OrderLine.confidence)).filter(
            OrderLine.order_id == order.id
        ).scalar() or 1.0
        order.overall_confidence = float(avg)
    else:
        order.overall_confidence = 1.0
    db.flush()

    after = _order_out(db, order, include_lines=True)
    log_audit(
        db, actor, "Order", order.id, "update_lines",
        before=before, after=after,
        summary=f"Order {order.order_number} lines replaced ({len(lines)} lines)",
    )
    return order


# --- Contract price lock (shared by /confirm and auto-confirm) ----------------

def lock_contract_prices(db: Session, order: Order) -> int:
    """For each line with product_id, look up the active ContractPrice
    (customer_id, product_id, unit_id) valid today; set unit_price if found.
    Lines without a contract price keep their existing/manual price.

    Returns the number of lines updated with a contract price.
    """
    if not order.lines:
        # Ensure the lines relationship is loaded.
        order.lines = (
            db.query(OrderLine)
            .filter(OrderLine.order_id == order.id)
            .order_by(OrderLine.line_no)
            .all()
        )
    today = date.today()
    updated = 0
    for ln in order.lines:
        if not ln.product_id:
            continue
        cp = (
            db.query(ContractPrice)
            .filter(
                ContractPrice.customer_id == order.customer_id,
                ContractPrice.product_id == ln.product_id,
                ContractPrice.valid_from <= today,
            )
            .filter(
                or_(
                    ContractPrice.valid_until.is_(None),
                    ContractPrice.valid_until >= today,
                )
            )
            .filter(ContractPrice.unit_id == ln.unit_id if ln.unit_id else True)
            .order_by(ContractPrice.valid_from.desc())
            .first()
        )
        if cp is not None:
            ln.unit_price = cp.price
            updated += 1
    db.flush()
    return updated


# --- Confirm / reject / clarification / resubmit ------------------------------

def confirm_order(
    db: Session,
    order: Order,
    *,
    notes: str | None = None,
    actor: User | None = None,
) -> Order:
    if order.status not in CONFIRMABLE:
        raise ValueError(f"Cannot confirm order in status '{order.status}'")
    before = {"status": order.status, "confirmed_by": order.confirmed_by}
    lock_contract_prices(db, order)
    order.status = "confirmed"
    order.confirmed_by = actor.id if actor else None
    order.confirmed_at = datetime.now(timezone.utc)
    if notes is not None:
        order.notes = notes if not order.notes else f"{order.notes}\n{notes}"
    db.flush()
    log_audit(
        db, actor, "Order", order.id, "confirm",
        before=before,
        after={"status": order.status, "confirmed_by": order.confirmed_by,
               "confirmed_at": order.confirmed_at.isoformat() if order.confirmed_at else None},
        summary=f"Order {order.order_number} confirmed",
    )
    return order


def reject_order(
    db: Session,
    order: Order,
    *,
    reason: str,
    actor: User | None = None,
) -> Order:
    # Allow rejecting only from pre-fulfilment statuses.
    if order.status in {"consolidated", "fulfilled", "invoiced"}:
        raise ValueError(f"Cannot reject order in status '{order.status}'")
    before = {"status": order.status, "notes": order.notes}
    order.status = "rejected"
    order.notes = reason if not order.notes else f"{order.notes}\n[reject] {reason}"
    db.flush()
    log_audit(
        db, actor, "Order", order.id, "reject",
        before=before, after={"status": order.status, "notes": order.notes},
        summary=f"Order {order.order_number} rejected: {reason}",
    )
    return order


def request_clarification(
    db: Session,
    order: Order,
    *,
    note: str,
    actor: User | None = None,
) -> Order:
    before = {"status": order.status, "notes": order.notes}
    order.status = "needs_clarification"
    order.notes = note if not order.notes else f"{order.notes}\n[clarify] {note}"
    db.flush()
    log_audit(
        db, actor, "Order", order.id, "request_clarification",
        before=before, after={"status": order.status, "notes": order.notes},
        summary=f"Order {order.order_number} needs clarification: {note}",
    )
    return order


def resubmit_order(
    db: Session,
    order: Order,
    *,
    actor: User | None = None,
) -> Order:
    if order.status != "needs_clarification":
        raise ValueError(
            f"Cannot resubmit order in status '{order.status}' "
            "(only needs_clarification may resubmit)"
        )
    before = {"status": order.status}
    order.status = "pending_confirmation"
    db.flush()
    log_audit(
        db, actor, "Order", order.id, "resubmit",
        before=before, after={"status": order.status},
        summary=f"Order {order.order_number} resubmitted to pending_confirmation",
    )
    return order
