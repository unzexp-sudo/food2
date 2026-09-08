"""Payment service.

POST /invoices/{id}/payments:
  - Create Payment (number "PAY" via next_number, direction "inbound",
    counterparty=customer name, paid_at=now).
  - invoice.paid_amount += amount; status → "paid" (full) / "partial".
  - Audit-log.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.numbers import next_number
from app.models import Customer, Invoice, Payment, User

from app.services.finance.invoices import serialize_invoice


def _payment_out(p: Payment) -> dict:
    return {
        "id": p.id,
        "number": p.number,
        "invoice_id": p.invoice_id,
        "direction": p.direction,
        "counterparty": p.counterparty,
        "amount": p.amount,
        "method": p.method,
        "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        "note": p.note,
    }


def serialize_payment(p: Payment) -> dict:
    return _payment_out(p)


def list_payments(
    db: Session,
    *,
    direction: str | None = None,
    invoice_id: str | None = None,
) -> tuple[list[dict], int]:
    q = db.query(Payment)
    if direction:
        q = q.filter(Payment.direction == direction)
    if invoice_id:
        q = q.filter(Payment.invoice_id == invoice_id)
    total = q.count()
    rows = q.order_by(Payment.paid_at.desc()).all()
    return [_payment_out(p) for p in rows], total


def create_payment(
    db: Session,
    *,
    invoice: Invoice,
    amount: float,
    method: str | None = None,
    note: str | None = None,
    actor: User | None = None,
) -> Payment:
    if amount <= 0:
        raise ValueError("Payment amount must be positive")
    if invoice.status == "void":
        raise ValueError("Cannot pay a void invoice")

    customer = db.get(Customer, invoice.customer_id) if invoice.customer_id else None
    counterparty = customer.name_en if customer else None

    number = next_number(db, Payment, "number", "PAY")
    payment = Payment(
        number=number,
        invoice_id=invoice.id,
        direction="inbound",
        counterparty=counterparty,
        amount=float(amount),
        method=method,
        paid_at=datetime.now(timezone.utc),
        note=note,
    )
    db.add(payment)

    inv_before = {
        "paid_amount": invoice.paid_amount,
        "status": invoice.status,
    }
    invoice.paid_amount = (invoice.paid_amount or 0.0) + float(amount)
    # status → "paid" (full) / "partial".
    if invoice.paid_amount + 1e-9 >= (invoice.total_amount or 0.0):
        invoice.status = "paid"
    else:
        invoice.status = "partial"
    db.flush()

    log_audit(
        db, actor, "Payment", payment.id, "create",
        before=None,
        after={
            "number": payment.number,
            "invoice_id": invoice.id,
            "amount": payment.amount,
            "direction": "inbound",
            "counterparty": counterparty,
        },
        summary=(
            f"Payment {payment.number} for {payment.amount} against invoice "
            f"{invoice.invoice_number} (status → {invoice.status})"
        ),
    )
    log_audit(
        db, actor, "Invoice", invoice.id, "payment",
        before=inv_before,
        after={
            "paid_amount": invoice.paid_amount,
            "status": invoice.status,
        },
        summary=(
            f"Invoice {invoice.invoice_number} paid_amount → {invoice.paid_amount}; "
            f"status → {invoice.status}"
        ),
    )
    return payment


def invoice_after_payment(db: Session, invoice: Invoice) -> dict:
    """Return the refreshed invoice serialization (used by the router after a
    payment so the caller sees the new paid_amount/status + the new payment
    is recorded).
    """
    return serialize_invoice(db, invoice)
