"""WeCom outbound event handlers (docs/WECOM_CONTRACTS.md §7 / §11, owner [C]).

Each handler is registered with `@on(...)`, builds a small bilingual payload and
hands it to `notify()`. Every handler:

  - is wrapped in try/except so it can never break the emitter,
  - builds its payload defensively (missing related rows must not raise),
  - keeps DB access light (these run inside request handlers).

Imported last from `app/main.py` so the `order.draft_created` handler runs after
the orders module's auto-confirm handler and sees the settled order status.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.events import on
from app.models import (
    Delivery,
    DeliveryLine,
    IntakeDocument,
    IntakeJob,
    Invoice,
    Order,
    OrderLine,
    User,
)
from app.services.notify.service import _customer_locale, notify

logger = logging.getLogger("erp.notify.wecom")

# Statuses that still need the customer (or ops) to look at the order.
_PENDING_STATUSES = ("draft", "pending_confirmation")


# --- payload helpers ----------------------------------------------------------

def _product_display(ln: OrderLine | None) -> str | None:
    if ln is None:
        return None
    if ln.product_display:
        return ln.product_display
    product = getattr(ln, "product", None)
    if product is not None:
        return getattr(product, "name_zh", None) or getattr(product, "name_en", None)
    return ln.raw_text


def _unit_code(ln: OrderLine | None) -> str | None:
    unit = getattr(ln, "unit", None) if ln is not None else None
    return getattr(unit, "code", None) if unit is not None else None


def _unit_display(ln: OrderLine | None, locale: str = "zh") -> str | None:
    """The unit as the reader should see it — `斤`, not `jin`.

    The gateway renders one locale per message, so pick the name that matches
    it here rather than shipping every variant and hoping the template guesses.
    """
    unit = getattr(ln, "unit", None) if ln is not None else None
    if unit is None:
        return None
    first, second = (
        ("name_en", "name_zh")
        if str(locale).lower().startswith("en")
        else ("name_zh", "name_en")
    )
    return (
        getattr(unit, first, None)
        or getattr(unit, second, None)
        or getattr(unit, "code", None)
    )


def _order_lines(
    db: Session, order: Order, locale: str = "zh"
) -> list[dict[str, Any]]:
    """[{product_display, quantity, unit, unit_code}] — never raises."""
    try:
        lines = (
            db.query(OrderLine)
            .filter(OrderLine.order_id == order.id)
            .order_by(OrderLine.line_no)
            .all()
        )
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "product_display": _product_display(ln),
            "quantity": ln.quantity,
            "unit": _unit_display(ln, locale),
            "unit_code": _unit_code(ln),
        }
        for ln in lines
    ]


def _order_total(db: Session, order: Order) -> float:
    """Sum of quantity * unit_price on the order lines (0.0 when unpriced)."""
    try:
        lines = db.query(OrderLine).filter(OrderLine.order_id == order.id).all()
        total = sum(
            (ln.quantity or 0.0) * (ln.unit_price or 0.0)
            for ln in lines
            if ln.unit_price is not None
        )
        return round(float(total), 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _needs_confirm_reason(db: Session, order: Order) -> str:
    """Why this order was left for manual confirmation (bilingual-agnostic key)."""
    try:
        lines = db.query(OrderLine).filter(OrderLine.order_id == order.id).all()
        if not lines:
            return "no_lines"
        if any(ln.product_id is None for ln in lines):
            return "unmatched_lines"
        if any((ln.quantity or 0) <= 0 for ln in lines):
            return "invalid_quantity"
    except Exception:  # noqa: BLE001
        pass
    return "low_confidence"


def _delivered_lines(
    db: Session, delivery: Delivery, locale: str = "zh"
) -> list[dict[str, Any]]:
    """[{product_display, delivered_quantity, quantity, unit, unit_code}]

    `delivered_quantity` comes first because that is what the customer should
    read — the gateway template prefers it over the ordered `quantity`, so a
    partial delivery is never reported as complete.
    """
    try:
        dlines = (
            db.query(DeliveryLine)
            .filter(DeliveryLine.delivery_id == delivery.id)
            .all()
        )
    except Exception:  # noqa: BLE001
        return []

    order_line_ids = {dl.order_line_id for dl in dlines if dl.order_line_id}
    order_lines = {}
    if order_line_ids:
        try:
            rows = (
                db.query(OrderLine)
                .filter(OrderLine.id.in_(list(order_line_ids)))
                .all()
            )
            order_lines = {r.id: r for r in rows}
        except Exception:  # noqa: BLE001
            order_lines = {}

    out = []
    for dl in dlines:
        ol = order_lines.get(dl.order_line_id)
        out.append({
            "product_display": _product_display(ol),
            "quantity": dl.quantity,
            "delivered_quantity": dl.delivered_quantity,
            "unit": _unit_display(ol, locale),
            "unit_code": _unit_code(ol),
        })
    return out


def _order_of(db: Session, order_id: str | None) -> Order | None:
    if not order_id:
        return None
    try:
        return db.get(Order, order_id)
    except Exception:  # noqa: BLE001
        return None


# --- event handlers -----------------------------------------------------------

@on("order.confirmed")
def handle_order_confirmed(*, db: Session, order: Order, **_: Any) -> None:
    """Tell the customer their order was confirmed."""
    try:
        o = db.get(Order, order.id) if order is not None else None
        if o is None:
            return
        notify(
            db,
            template="order_confirmed",
            customer_id=o.customer_id,
            order_id=o.id,
            locale=(loc := _customer_locale(db, o.customer_id)),
            payload={
                "order_number": o.order_number,
                "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
                "lines": _order_lines(db, o, loc),
                # An unpriced order has no total. Sending 0.0 would tell the
                # customer their order is free; None renders as "-".
                "total": _order_total(db, o) or None,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "order.confirmed notification failed for order %s",
            getattr(order, "id", "?"),
        )


@on("order.draft_created")
def handle_draft_created(*, db: Session, order: Order, **_: Any) -> None:
    """Only fires when auto-confirm did NOT settle the order.

    Runs after the orders module's auto-confirm handler (main.py imports this
    module last), so we re-read the order: if it is still `draft` or
    `pending_confirmation` the customer has to confirm it manually.
    """
    try:
        o = db.get(Order, order.id) if order is not None else None
        if o is None or o.status not in _PENDING_STATUSES:
            return
        notify(
            db,
            template="needs_customer_confirm",
            customer_id=o.customer_id,
            order_id=o.id,
            locale=(loc := _customer_locale(db, o.customer_id)),
            payload={
                "order_number": o.order_number,
                "lines": _order_lines(db, o, loc),
                "reason": _needs_confirm_reason(db, o),
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "order.draft_created notification failed for order %s",
            getattr(order, "id", "?"),
        )


@on("intake.job_failed")
def handle_intake_job_failed(
    *, db: Session, job: IntakeJob, document: IntakeDocument | None = None, **_: Any
) -> None:
    """Tell the customer we could not understand their message."""
    try:
        j = db.get(IntakeJob, job.id) if job is not None else None
        if j is None:
            return

        doc = document
        if doc is None and j.document_id:
            doc = db.get(IntakeDocument, j.document_id)
        meta = getattr(doc, "document_meta", None) or {}
        wecom = meta.get("wecom") if isinstance(meta, dict) else None
        msgid = wecom.get("msgid") if isinstance(wecom, dict) else None

        if not msgid:
            # Not a WeCom message (UI/API upload). We have no chat to reply
            # into, and messaging the customer about a job they never sent
            # would be worse than staying silent.
            return

        notify(
            db,
            template="parse_failed",
            customer_id=getattr(doc, "customer_id", None),
            locale=_customer_locale(db, getattr(doc, "customer_id", None)),
            payload={"msgid": msgid, "error": j.error},
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "intake.job_failed notification failed for job %s",
            getattr(job, "id", "?"),
        )


@on("delivery.dispatched")
def handle_delivery_dispatched(*, db: Session, delivery: Delivery, **_: Any) -> None:
    """Driver is on the way."""
    try:
        d = db.get(Delivery, delivery.id) if delivery is not None else None
        if d is None:
            return
        order = _order_of(db, d.order_id)
        driver = db.get(User, d.driver_id) if d.driver_id else None
        notify(
            db,
            template="out_for_delivery",
            customer_id=order.customer_id if order else None,
            order_id=d.order_id,
            locale=_customer_locale(db, order.customer_id if order else None),
            payload={
                "order_number": getattr(order, "order_number", None),
                "driver": driver.name if driver else None,
                "eta": d.scheduled_date.isoformat() if d.scheduled_date else None,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "delivery.dispatched notification failed for delivery %s",
            getattr(delivery, "id", "?"),
        )


@on("delivery.completed")
def handle_delivery_completed(*, db: Session, delivery: Delivery, **_: Any) -> None:
    """Goods delivered."""
    try:
        d = db.get(Delivery, delivery.id) if delivery is not None else None
        if d is None:
            return
        order = _order_of(db, d.order_id)
        notify(
            db,
            template="delivered",
            customer_id=order.customer_id if order else None,
            order_id=d.order_id,
            locale=(loc := _customer_locale(db, order.customer_id if order else None)),
            payload={
                "order_number": getattr(order, "order_number", None),
                "delivered_lines": _delivered_lines(db, d, loc),
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "delivery.completed notification failed for delivery %s",
            getattr(delivery, "id", "?"),
        )


@on("invoice.created")
def handle_invoice_created(*, db: Session, invoice: Invoice, **_: Any) -> None:
    """Invoice is ready."""
    try:
        inv = db.get(Invoice, invoice.id) if invoice is not None else None
        if inv is None:
            return
        order = _order_of(db, inv.order_id)
        notify(
            db,
            template="invoice_ready",
            customer_id=inv.customer_id,
            order_id=inv.order_id,
            locale=_customer_locale(db, inv.customer_id),
            payload={
                "invoice_number": inv.invoice_number,
                "order_number": getattr(order, "order_number", None),
                # Same rule as orders: never tell a customer the amount is 0.
                "total": round(inv.total_amount, 2) if inv.total_amount else None,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "invoice.created notification failed for invoice %s",
            getattr(invoice, "id", "?"),
        )
