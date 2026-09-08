"""PROCUREMENT MODULE — owner: orders/procurement agent.

Two resources:
  /api/v1/consolidation  — run, list batches, get batch detail
  /api/v1/purchase-orders — list, get, edit lines (draft only), send, cancel

Multi-router pattern: consolidation_router + po_router merged into `router`.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.schemas.procurement import (
    ConsolidationRunIn,
    PurchaseOrderLinesPatch,
)
from app.services.orders import consolidation as cons_svc
from app.services.orders import purchase_orders as po_svc

consolidation_router = APIRouter(prefix="/api/v1/consolidation", tags=["consolidation"])
po_router = APIRouter(prefix="/api/v1/purchase-orders", tags=["purchase-orders"])
# `router` is what main.py registers. It has no prefix of its own; the
# sub-routers carry their full `/api/v1/<resource>` prefixes.
router = APIRouter(tags=["procurement"])


# --- Consolidation ------------------------------------------------------------

@consolidation_router.post("/run", response_model=None)
def run_consolidation_endpoint(
    payload: ConsolidationRunIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    try:
        result = cons_svc.run_consolidation(
            db, delivery_date=payload.delivery_date, actor=actor
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return result


@consolidation_router.get("/batches", response_model=None)
def list_batches_endpoint(
    delivery_date: date | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    items, total = cons_svc.list_batches(
        db, delivery_date=delivery_date, status=status_filter
    )
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


@consolidation_router.get("/batches/{batch_id}", response_model=None)
def get_batch_endpoint(
    batch_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    batch = cons_svc.get_batch(db, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    return batch


# --- Purchase orders ----------------------------------------------------------

@po_router.get("", response_model=None)
def list_purchase_orders_endpoint(
    status_filter: str | None = Query(default=None, alias="status"),
    wholesaler_id: str | None = Query(default=None),
    delivery_date: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    items, total = po_svc.list_purchase_orders(
        db,
        status=status_filter,
        wholesaler_id=wholesaler_id,
        delivery_date=delivery_date,
    )
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


@po_router.get("/{po_id}", response_model=None)
def get_purchase_order_endpoint(
    po_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    po = po_svc.get_purchase_order(db, po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    return po


@po_router.patch("/{po_id}/lines", response_model=None)
def patch_po_lines_endpoint(
    po_id: str,
    payload: PurchaseOrderLinesPatch,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    from app.models import PurchaseOrder
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    try:
        po = po_svc.patch_po_lines(
            db, po,
            edits=[ln.model_dump() for ln in payload.lines],
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return po_svc.get_purchase_order(db, po.id)


@po_router.post("/{po_id}/send", response_model=None)
def send_po_endpoint(
    po_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    from app.models import PurchaseOrder
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    try:
        po = po_svc.send_po(db, po, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return po_svc.get_purchase_order(db, po.id)


@po_router.post("/{po_id}/cancel", response_model=None)
def cancel_po_endpoint(
    po_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    from app.models import PurchaseOrder
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    try:
        po = po_svc.cancel_po(db, po, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return po_svc.get_purchase_order(db, po.id)


# Merge sub-routers into the exported `router` (must come AFTER all route
# decorators are registered on the sub-routers — include_router copies the
# route table at call time).
router.include_router(consolidation_router)
router.include_router(po_router)
