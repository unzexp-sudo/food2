"""Auto-invoice handler for the "delivery.completed" event.

Registered at module import time so that any module which imports the finance
router (main.py does this) wires the handler up — same pattern as the orders
module's auto-confirm handler.

When a delivery completes with status delivered|partial:
  - Read setting "auto_invoice" {"enabled": true} (default enabled).
  - If disabled → return.
  - Generate the invoice for delivery.order_id (same logic as /invoices/generate).
  - Invoice status "issued".
  - Catch own exceptions; commit on the passed db session. Never raise.
  - Audit action "auto_invoice".

events.emit wraps handlers in try/except too, but we catch here so we can log a
friendlier message and leave the delivery in a sane state.
"""
from __future__ import annotations

import logging
from datetime import timezone

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.events import emit, on
from app.models import Delivery, Order, SystemSetting
from app.services.finance.invoices import (
    InvoiceExists,
    generate_invoice,
)

logger = logging.getLogger("erp.finance.auto_invoice")


def _get_setting(db: Session, key: str) -> dict | str | None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.value if row else None


def _auto_invoice_enabled(db: Session) -> bool:
    val = _get_setting(db, "auto_invoice")
    if isinstance(val, dict):
        return bool(val.get("enabled", True))
    return True  # default enabled


@on("delivery.completed")
def handle_delivery_completed(*, db: Session, delivery: Delivery) -> None:
    """Auto-generate the invoice when a delivery completes."""
    try:
        d = db.get(Delivery, delivery.id) if delivery is not None else None
        if d is None:
            logger.warning("delivery.completed: delivery not found (id=%s)",
                           getattr(delivery, "id", "?"))
            return
        # Only delivered|partial deliveries are billable.
        if d.status not in ("delivered", "partial"):
            return

        if not _auto_invoice_enabled(db):
            # Disabled — leave for manual /invoices/generate.
            return

        try:
            invoice = generate_invoice(
                db,
                order_id=d.order_id,
                actor=None,
                audit_action="auto_invoice",
            )
        except InvoiceExists as exc:
            # An invoice already exists for this order — that's fine, skip.
            logger.info(
                "delivery.completed: invoice already exists for order %s "
                "(invoice %s), skipping",
                d.order_id, exc.invoice.invoice_number,
            )
            return
        except ValueError as exc:
            # No delivered lines / order not found — log and skip.
            logger.info(
                "delivery.completed: cannot generate invoice for order %s: %s",
                d.order_id, exc,
            )
            return

        # Audit summary for the auto-invoice trigger.
        log_audit(
            db, None, "Invoice", invoice.id, "auto_invoice",
            before=None,
            after={
                "invoice_number": invoice.invoice_number,
                "order_id": d.order_id,
                "delivery_id": d.id,
                "delivery_number": d.delivery_number,
                "delivery_status": d.status,
            },
            summary=(
                f"Auto-invoice {invoice.invoice_number} generated for "
                f"order (delivery {d.delivery_number} status={d.status})"
            ),
        )
        db.commit()
        # Outbound WeCom notification (docs/WECOM_CONTRACTS.md §11).
        emit("invoice.created", db=db, invoice=invoice)
    except Exception:  # noqa: BLE001 — handler must never break the emitter
        logger.exception(
            "Auto-invoice handler failed for delivery %s",
            getattr(delivery, "id", "?"),
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
