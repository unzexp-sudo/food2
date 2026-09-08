"""Invoice service.

Generate invoice from delivered quantities:
  - For each delivery_line with delivered_quantity>0 joined to order_line + order,
    create one InvoiceLine (quantity=delivered_quantity, unit_price=order_line.unit_price,
    amount=qty×price, description=product name or product_display).
  - invoice_number "INV" via next_number, status "issued", issued_at=now,
    total_amount=Σ.
  - If the order is currently "fulfilled" → order status → "invoiced".
  - Idempotent: an existing non-void invoice for the order → raises InvoiceExists
    (the router maps that to 409 returning the existing invoice).
  - Audit-logs.

The generate_invoice helper is shared by:
  - POST /invoices/generate  (commits in the router)
  - auto_invoice handler      (commits on the passed db)

Both callers pass an actor (User | None). The handler passes None so the audit
row records a system/auto action.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.numbers import next_number
from app.models import (
    Customer,
    Delivery,
    DeliveryLine,
    Invoice,
    InvoiceLine,
    Order,
    OrderLine,
    Product,
    User,
)


class InvoiceExists(Exception):
    """Raised when a non-void invoice already exists for the order.

    Carries the existing Invoice so the router can return it in the 409 body.
    """

    def __init__(self, invoice: Invoice, db: Session):
        super().__init__(f"Invoice {invoice.invoice_number} already exists for order")
        self.invoice = invoice
        self._db = db

    def serialized(self) -> dict:
        return serialize_invoice(self._db, self.invoice)


# --- Serializers --------------------------------------------------------------


def _invoice_line_out(ln: InvoiceLine) -> dict:
    return {
        "id": ln.id,
        "order_line_id": ln.order_line_id,
        "description": ln.description,
        "quantity": ln.quantity,
        "unit_price": ln.unit_price,
        "amount": ln.amount,
    }


def serialize_invoice(db: Session, inv: Invoice) -> dict:
    customer = db.get(Customer, inv.customer_id) if inv.customer_id else None
    order = db.get(Order, inv.order_id) if inv.order_id else None
    return {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "customer_id": inv.customer_id,
        "customer_name_en": customer.name_en if customer else "",
        "customer_name_zh": customer.name_zh if customer else "",
        "order_id": inv.order_id,
        "order_number": order.order_number if order else None,
        "status": inv.status,
        "total_amount": inv.total_amount,
        "paid_amount": inv.paid_amount,
        "issued_at": inv.issued_at.isoformat() if inv.issued_at else None,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "lines": [_invoice_line_out(ln) for ln in (inv.lines or [])],
    }


# --- List / get ---------------------------------------------------------------


def list_invoices(
    db: Session,
    *,
    status: str | None = None,
    customer_id: str | None = None,
) -> tuple[list[dict], int]:
    q = db.query(Invoice)
    if status:
        q = q.filter(Invoice.status == status)
    if customer_id:
        q = q.filter(Invoice.customer_id == customer_id)
    total = q.count()
    rows = q.order_by(Invoice.created_at.desc()).all()
    return [serialize_invoice(db, r) for r in rows], total


def get_invoice(db: Session, invoice_id: str) -> dict | None:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        return None
    return serialize_invoice(db, inv)


# --- Generate -----------------------------------------------------------------


def _line_description(ln: OrderLine, product: Product | None) -> str:
    """Description per §4: product name_zh/en or product_display."""
    if product is not None:
        # Bilingual: prefer the EN name (per the contract note in §5 margin).
        return product.name_en or product.name_zh or (ln.product_display or "")
    return ln.product_display or ln.raw_text or ""


def _delivered_rows_for_order(db: Session, order_id: str) -> list[tuple[OrderLine, float, Product | None]]:
    """Return [(order_line, delivered_quantity, product)] for each delivered
    line on the order. Sums delivered_quantity across deliveries for the same
    order_line (an order may have multiple deliveries — e.g. partial then
    top-up).
    """
    # Aggregate delivered quantities per order_line across all deliveries for
    # this order. Only lines with delivered_quantity>0 are invoiced.
    rows = (
        db.query(DeliveryLine, OrderLine)
        .join(OrderLine, OrderLine.id == DeliveryLine.order_line_id)
        .join(Delivery, Delivery.id == DeliveryLine.delivery_id)
        .filter(
            Delivery.order_id == order_id,
            Delivery.status.in_(("delivered", "partial")),
            DeliveryLine.delivered_quantity > 0,
        )
        .all()
    )
    if not rows:
        return []
    # Sum per order_line.
    by_ol: dict[str, tuple[OrderLine, float]] = {}
    for dl, ol in rows:
        cur = by_ol.get(ol.id)
        if cur is None:
            by_ol[ol.id] = (ol, float(dl.delivered_quantity or 0))
        else:
            ol_existing, qty = cur
            by_ol[ol.id] = (ol_existing, qty + float(dl.delivered_quantity or 0))

    # Resolve products in one shot.
    product_ids = [ol.product_id for ol, _ in by_ol.values() if ol.product_id]
    products: dict[str, Product] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            products[p.id] = p

    out: list[tuple[OrderLine, float, Product | None]] = []
    for ol, qty in by_ol.values():
        prod = products.get(ol.product_id) if ol.product_id else None
        out.append((ol, qty, prod))
    # Stable ordering by line_no — but order_line.line_no isn't on the indexed
    # query, so sort here.
    out.sort(key=lambda t: t[0].line_no)
    return out


def generate_invoice(
    db: Session,
    *,
    order_id: str,
    actor: User | None = None,
    audit_action: str = "generate_invoice",
) -> Invoice:
    """Build an invoice from delivered quantities on the order.

    Raises:
      ValueError  — order not found, or no delivered lines yet.
      InvoiceExists — a non-void invoice already exists for this order.
    """
    order = db.get(Order, order_id)
    if order is None:
        raise ValueError(f"Order not found: {order_id}")

    # Idempotency: existing non-void invoice for this order.
    existing = (
        db.query(Invoice)
        .filter(
            Invoice.order_id == order_id,
            Invoice.status != "void",
        )
        .first()
    )
    if existing is not None:
        raise InvoiceExists(existing, db)

    delivered = _delivered_rows_for_order(db, order_id)
    if not delivered:
        raise ValueError(
            "No delivered quantities to invoice for this order "
            "(delivery must be completed first)"
        )

    customer = db.get(Customer, order.customer_id)
    invoice_number = next_number(db, Invoice, "invoice_number", "INV")
    invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=order.customer_id,
        order_id=order.id,
        status="issued",
        total_amount=0.0,
        paid_amount=0.0,
        issued_at=datetime.now(timezone.utc),
    )
    db.add(invoice)
    db.flush()

    total = 0.0
    for ol, qty, prod in delivered:
        unit_price = float(ol.unit_price) if ol.unit_price is not None else 0.0
        amount = round(qty * unit_price, 6)
        total += amount
        il = InvoiceLine(
            invoice_id=invoice.id,
            order_line_id=ol.id,
            description=_line_description(ol, prod),
            quantity=qty,
            unit_price=unit_price,
            amount=amount,
        )
        db.add(il)
    invoice.total_amount = round(total, 6)
    db.flush()

    # Order status → "invoiced" if currently fulfilled.
    order_before = {"status": order.status}
    order_transitioned = False
    if order.status == "fulfilled":
        order.status = "invoiced"
        order_transitioned = True
        db.flush()

    log_audit(
        db, actor, "Invoice", invoice.id, audit_action,
        before=None,
        after={
            "invoice_number": invoice.invoice_number,
            "order_id": order.id,
            "customer_id": order.customer_id,
            "status": invoice.status,
            "total_amount": invoice.total_amount,
            "line_count": len(delivered),
        },
        summary=(
            f"Invoice {invoice.invoice_number} generated for order "
            f"{order.order_number} (total {invoice.total_amount})"
        ),
    )
    if order_transitioned:
        log_audit(
            db, actor, "Order", order.id, "invoice",
            before=order_before,
            after={"status": order.status},
            summary=(
                f"Order {order.order_number} → {order.status} "
                f"(invoice {invoice.invoice_number})"
            ),
        )
    return invoice
