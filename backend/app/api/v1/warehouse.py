"""WAREHOUSE MODULE — owner: warehouse/delivery agent.

Implements (see docs/AGENT_CONTRACTS.md §5):
  POST /inbound-receipts          R: warehouse/admin
  GET  /inbound-receipts          paged; filter po_id
  GET  /purchase-orders/{id}/received → receipts summary for a PO
  GET  /pick-lists                paged; filters delivery_date, status
  GET  /pick-lists/{id}
  POST /pick-lists/generate       {delivery_date} R: warehouse/admin
  POST /pick-lists/{id}/lines/{line_id}/pick  R: warehouse/admin
  GET  /inventory                 paged; filter product_id R: warehouse/admin
  POST /inventory/loss            {product_id, quantity, reason} R: warehouse/admin

Multi-router pattern: inbound_router (/api/v1/inbound-receipts),
pick_router (/api/v1/pick-lists), inv_router (/api/v1/inventory),
plus the PO-received sub-resource lives on `router` with prefix /api/v1.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.pagination import clamp_page, page_response
from app.models import PurchaseOrder, User
from app.schemas.warehouse import (
    InboundReceiptCreate,
    InventoryAdjustIn,
    InventoryLossIn,
    PickLinePickIn,
    PickListGenerateIn,
)
from app.services.warehouse import inbound as inbound_svc
from app.services.warehouse import inventory as inv_svc
from app.services.warehouse import picklists as pick_svc

inbound_router = APIRouter(prefix="/api/v1/inbound-receipts", tags=["warehouse"])
pick_router = APIRouter(prefix="/api/v1/pick-lists", tags=["warehouse"])
inv_router = APIRouter(prefix="/api/v1/inventory", tags=["warehouse"])
# The PO-received sub-resource shares the /api/v1/purchase-orders prefix.
po_router = APIRouter(prefix="/api/v1/purchase-orders", tags=["warehouse"])
# `router` is what main.py registers. It has no prefix of its own; the
# sub-routers carry their full `/api/v1/<resource>` prefixes.
router = APIRouter(tags=["warehouse"])


# --- Inbound receipts --------------------------------------------------------

@inbound_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_inbound_receipt_endpoint(
    payload: InboundReceiptCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("warehouse")),
):
    po = db.get(PurchaseOrder, payload.po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    try:
        receipt = inbound_svc.create_inbound_receipt(
            db,
            po=po,
            lines=[ln.model_dump() for ln in payload.lines],
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    # Regenerate pick lists for the PO's batch delivery_date (if any).
    delivery_date = inbound_svc.po_batch_delivery_date(db, po)
    if delivery_date is not None:
        try:
            pick_svc.regenerate_for_date(db, delivery_date=delivery_date, actor=actor)
        except Exception:  # noqa: BLE001 — regeneration must not break the receipt
            pass
    db.commit()
    return inbound_svc.serialize_receipt(db, receipt)


@inbound_router.get("", response_model=None)
def list_inbound_receipts_endpoint(
    po_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("warehouse")),
):
    page, page_size = clamp_page(page, page_size)
    items, total = inbound_svc.list_receipts(db, po_id=po_id)
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


# --- PO received summary -----------------------------------------------------

@po_router.get("/{po_id}/received", response_model=None)
def po_received_endpoint(
    po_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("warehouse", "ops", "finance")),
):
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found")
    return inbound_svc.po_received_summary(db, po)


# --- Pick lists --------------------------------------------------------------

@pick_router.get("", response_model=None)
def list_pick_lists_endpoint(
    delivery_date: date | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("warehouse", "ops")),
):
    page, page_size = clamp_page(page, page_size)
    items, total = pick_svc.list_pick_lists(
        db, delivery_date=delivery_date, status=status_filter
    )
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


@pick_router.get("/{pick_list_id}", response_model=None)
def get_pick_list_endpoint(
    pick_list_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("warehouse", "ops")),
):
    pk = pick_svc.get_pick_list(db, pick_list_id)
    if pk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pick list not found")
    return pk


@pick_router.post("/generate", response_model=None, status_code=status.HTTP_201_CREATED)
def generate_pick_lists_endpoint(
    payload: PickListGenerateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("warehouse")),
):
    result = pick_svc.generate_pick_lists(db, delivery_date=payload.delivery_date, actor=actor)
    db.commit()
    return result


@pick_router.post("/{pick_list_id}/lines/{line_id}/pick", response_model=None)
def pick_line_endpoint(
    pick_list_id: str,
    line_id: str,
    payload: PickLinePickIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("warehouse")),
):
    from app.models import PickLine, PickList
    pk = db.get(PickList, pick_list_id)
    if pk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pick list not found")
    line = db.get(PickLine, line_id)
    if line is None or line.pick_list_id != pk.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pick line not found on this list")
    try:
        pick_svc.pick_line(
            db,
            pick_list=pk,
            line=line,
            picked_quantity=payload.picked_quantity,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return pick_svc.serialize_pick_list(db, pk)


# --- Inventory ---------------------------------------------------------------

@inv_router.get("", response_model=None)
def list_inventory_endpoint(
    product_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("warehouse")),
):
    page, page_size = clamp_page(page, page_size)
    items, total = inv_svc.list_movements(db, product_id=product_id)
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


@inv_router.post("/loss", response_model=None, status_code=status.HTTP_201_CREATED)
def record_loss_endpoint(
    payload: InventoryLossIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("warehouse")),
):
    mv = inv_svc.record_loss(
        db,
        product_id=payload.product_id,
        quantity=payload.quantity,
        reason=payload.reason,
        actor=actor,
    )
    db.commit()
    return inv_svc._movement_out(db, mv)


@inv_router.post("/adjust", response_model=None, status_code=status.HTTP_201_CREATED)
def record_adjustment_endpoint(
    payload: InventoryAdjustIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("warehouse")),
):
    mv = inv_svc.record_adjustment(
        db,
        product_id=payload.product_id,
        quantity_delta=payload.quantity_delta,
        reason=payload.reason,
        actor=actor,
    )
    db.commit()
    return inv_svc._movement_out(db, mv)


# Merge sub-routers into the exported `router` (must come AFTER route decorators).
router.include_router(inbound_router)
router.include_router(pick_router)
router.include_router(inv_router)
router.include_router(po_router)
