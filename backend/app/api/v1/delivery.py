"""DELIVERY MODULE — owner: warehouse/delivery agent.

Implements (see docs/AGENT_CONTRACTS.md §5):
  GET  /deliveries               paged; filters date, status, driver_id
                                 R: ops/admin/warehouse/driver (driver sees own)
  GET  /deliveries/{id}           → Delivery + lines + pod
  POST /deliveries/generate       {delivery_date} R: ops/admin/warehouse
  POST /deliveries/{id}/assign    {driver_id}     R: ops/admin
  POST /deliveries/{id}/status    {status}        R: warehouse/admin/driver
  POST /deliveries/{id}/complete  R: warehouse/admin/driver
                                  (json OR multipart with photo)
  GET  /deliveries/{id}/photo     → FileResponse (any logged-in role)

On complete, emits "delivery.completed" so finance auto-invoices.
"""
from __future__ import annotations

import json
from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.core.events import emit
from app.core.pagination import clamp_page, page_response
from app.models import Delivery, User
from app.schemas.delivery import (
    DeliveryAssignIn,
    DeliveryGenerateIn,
    DeliveryStatusIn,
)
from app.services.warehouse import delivery as delivery_svc

router = APIRouter(prefix="/api/v1/deliveries", tags=["delivery"])


# --- List / get --------------------------------------------------------------

@router.get("", response_model=None)
def list_deliveries_endpoint(
    date_: date | None = Query(default=None, alias="date"),
    status_filter: str | None = Query(default=None, alias="status"),
    driver_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ops", "warehouse", "driver")),
):
    # Drivers only see their own deliveries.
    eff_driver = driver_id
    if user.role == "driver":
        eff_driver = user.id
    page, page_size = clamp_page(page, page_size)
    items, total = delivery_svc.list_deliveries(
        db, date_=date_, status=status_filter, driver_id=eff_driver
    )
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


@router.get("/{delivery_id}", response_model=None)
def get_delivery_endpoint(
    delivery_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ops", "warehouse", "driver")),
):
    d = delivery_svc.get_delivery(db, delivery_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery not found")
    # Drivers can only see their own.
    if user.role == "driver" and d.get("driver_id") != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery not found")
    return d


# --- Generate ----------------------------------------------------------------

@router.post("/generate", response_model=None, status_code=status.HTTP_201_CREATED)
def generate_deliveries_endpoint(
    payload: DeliveryGenerateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops", "warehouse")),
):
    result = delivery_svc.generate_deliveries(
        db, delivery_date=payload.delivery_date, actor=actor
    )
    db.commit()
    return result


# --- Assign ------------------------------------------------------------------

@router.post("/{delivery_id}/assign", response_model=None)
def assign_driver_endpoint(
    delivery_id: str,
    payload: DeliveryAssignIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    d = db.get(Delivery, delivery_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery not found")
    try:
        delivery_svc.assign_driver(
            db, delivery=d, driver_id=payload.driver_id, actor=actor
        )
    except ValueError as exc:
        # Distinguish "not a driver" (404 per contract) from generic errors.
        msg = str(exc)
        if "not a driver" in msg or "not found" in msg:
            raise HTTPException(status.HTTP_404_NOT_FOUND, msg)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)
    db.commit()
    return delivery_svc.serialize_delivery(db, d)


# --- Status ------------------------------------------------------------------

@router.post("/{delivery_id}/status", response_model=None)
def set_status_endpoint(
    delivery_id: str,
    payload: DeliveryStatusIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("warehouse", "driver")),
):
    d = db.get(Delivery, delivery_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery not found")
    # Drivers can only update their own deliveries.
    if actor.role == "driver" and d.driver_id != actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your delivery")
    try:
        delivery_svc.set_status(db, delivery=d, status=payload.status, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    # Outbound WeCom notification (docs/WECOM_CONTRACTS.md §11) — the ERP's
    # "out_for_delivery" status is the contract's "delivering/in_transit".
    if d.status == "out_for_delivery":
        emit("delivery.dispatched", db=db, delivery=d)
    return delivery_svc.serialize_delivery(db, d)


# --- Complete (json OR multipart) --------------------------------------------

@router.post("/{delivery_id}/complete", response_model=None)
async def complete_delivery_endpoint(
    delivery_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("warehouse", "driver")),
):
    d = db.get(Delivery, delivery_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery not found")
    if actor.role == "driver" and d.driver_id != actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your delivery")

    ctype = (request.headers.get("content-type") or "").lower()
    line_entries: list[dict] = []
    rcv_by: str | None = None
    lat: float | None = None
    lng: float | None = None
    photo_bytes: bytes | None = None
    photo_ext: str | None = None

    if "application/json" in ctype:
        # JSON body path.
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON body")
        if not isinstance(body, dict):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "JSON body must be an object")
        lines_field = body.get("lines")
        if not lines_field:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "lines is required",
            )
        line_entries = list(lines_field)
        rcv_by = body.get("received_by")
        lat = body.get("gps_lat")
        lng = body.get("gps_lng")
    else:
        # Multipart form path.
        form = await request.form()
        lines_field = form.get("lines")
        if lines_field is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "lines is required (json body or multipart 'lines')",
            )
        try:
            parsed = json.loads(str(lines_field))
        except json.JSONDecodeError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "lines must be a JSON array string",
            )
        if not isinstance(parsed, list) or not parsed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "lines must be a non-empty array")
        line_entries = parsed
        rcv_by = form.get("received_by")
        if form.get("gps_lat") not in (None, ""):
            try:
                lat = float(form.get("gps_lat"))
            except (TypeError, ValueError):
                lat = None
        if form.get("gps_lng") not in (None, ""):
            try:
                lng = float(form.get("gps_lng"))
            except (TypeError, ValueError):
                lng = None
        upload = form.get("photo")
        if upload is not None and hasattr(upload, "file"):
            photo_bytes = await upload.read()
            ext = "jpg"
            filename = getattr(upload, "filename", "") or ""
            if filename and "." in filename:
                ext = filename.rsplit(".", 1)[-1]
            content_type = getattr(upload, "content_type", "") or ""
            if "/" in content_type:
                ext = content_type.rsplit("/", 1)[-1]
            photo_ext = ext

    # Validate line entries have the required keys.
    for e in line_entries:
        if "delivery_line_id" not in e or "delivered_quantity" not in e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "each line needs delivery_line_id and delivered_quantity",
            )

    try:
        delivery_svc.complete_delivery(
            db,
            delivery=d,
            line_qty=line_entries,
            received_by=rcv_by,
            gps_lat=lat,
            gps_lng=lng,
            photo_bytes=photo_bytes,
            photo_ext=photo_ext,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()

    # Emit delivery.completed so finance auto-invoices. Re-fetch into the
    # committed session so handlers see the persisted state.
    d2 = db.get(Delivery, d.id)
    emit("delivery.completed", db=db, delivery=d2)
    db.commit()
    return delivery_svc.serialize_delivery(db, d2)


# --- POD photo ---------------------------------------------------------------

@router.get("/{delivery_id}/photo")
def get_delivery_photo_endpoint(
    delivery_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models import ProofOfDelivery

    d = db.get(Delivery, delivery_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery not found")
    pod = (
        db.query(ProofOfDelivery)
        .filter(ProofOfDelivery.delivery_id == delivery_id)
        .first()
    )
    if pod is None or not pod.photo_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No POD photo for this delivery")
    path = pod.photo_path
    if not path or not __import__("os").path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "POD photo file missing")
    return FileResponse(path)
