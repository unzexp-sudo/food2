"""ORDERS MODULE — owner: orders/procurement agent.

Endpoints (see docs/AGENT_CONTRACTS.md §5):
  GET    /orders            paged; filters: status, customer_id, delivery_date, q (order_number)
  POST   /orders            manual create
  GET    /orders/{id}       → Order + lines
  PATCH  /orders/{id}/lines bulk replace lines
  POST   /orders/{id}/confirm, /reject, /request-clarification, /resubmit
  GET    /orders/{id}/lineage → full lineage per §6

Auto-confirm handler is registered at import of app.services.orders package
(via app/services/orders/__init__.py) — main.py importing this router wires it up.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.events import emit
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.schemas.orders import (
    OrderClarification,
    OrderConfirm,
    OrderCreate,
    OrderLineReplace,
    OrderReject,
)
from app.services.orders import orders as svc
from app.services.orders.lineage import build_lineage

# Importing the services package registers the auto-confirm event handler.
import app.services.orders  # noqa: F401

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


# --- List + create ------------------------------------------------------------

@router.get("", response_model=None)
def list_orders_endpoint(
    status_filter: str | None = Query(default=None, alias="status"),
    customer_id: str | None = Query(default=None),
    delivery_date: date | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    items, total = svc.list_orders(
        db,
        status=status_filter,
        customer_id=customer_id,
        delivery_date=delivery_date,
        q=q,
    )
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_order_endpoint(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    try:
        order = svc.create_order(
            db,
            customer_id=payload.customer_id,
            delivery_date=payload.delivery_date,
            notes=payload.notes,
            lines=[ln.model_dump() for ln in payload.lines],
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    # Emit the draft_created event so the auto-confirm handler runs on the
    # same committed DB. Re-fetch the order into the (now-closed) request
    # session — handlers open their own session state.
    # We emit AFTER commit so the handler sees the persisted row.
    emit("order.draft_created", db=db, order=order)
    db.commit()
    return svc.serialize_order(db, order)


# --- Single order -------------------------------------------------------------

@router.get("/{order_id}", response_model=None)
def get_order_endpoint(
    order_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    order = svc.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return svc.serialize_order(db, order)


# --- Bulk replace lines -------------------------------------------------------

@router.patch("/{order_id}/lines", response_model=None)
def replace_lines_endpoint(
    order_id: str,
    payload: OrderLineReplace,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    order = svc.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    try:
        order = svc.replace_lines(
            db, order,
            lines=[ln.model_dump() for ln in payload.lines],
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return svc.serialize_order(db, order)


# --- Confirm / reject / clarification / resubmit ------------------------------

@router.post("/{order_id}/confirm", response_model=None)
def confirm_order_endpoint(
    order_id: str,
    payload: OrderConfirm,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    order = svc.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    try:
        order = svc.confirm_order(db, order, notes=payload.notes, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    # Outbound WeCom notification (docs/WECOM_CONTRACTS.md §11).
    emit("order.confirmed", db=db, order=order)
    return svc.serialize_order(db, order)


@router.post("/{order_id}/reject", response_model=None)
def reject_order_endpoint(
    order_id: str,
    payload: OrderReject,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    order = svc.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    try:
        order = svc.reject_order(db, order, reason=payload.reason, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return svc.serialize_order(db, order)


@router.post("/{order_id}/request-clarification", response_model=None)
def request_clarification_endpoint(
    order_id: str,
    payload: OrderClarification,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    order = svc.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    try:
        order = svc.request_clarification(db, order, note=payload.note, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return svc.serialize_order(db, order)


@router.post("/{order_id}/resubmit", response_model=None)
def resubmit_order_endpoint(
    order_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    order = svc.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    try:
        order = svc.resubmit_order(db, order, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return svc.serialize_order(db, order)


# --- Lineage ------------------------------------------------------------------

@router.get("/{order_id}/lineage", response_model=None)
def lineage_endpoint(
    order_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    order = svc.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return build_lineage(db, order)
