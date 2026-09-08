"""QUOTATIONS MODULE — sales quotations (Guanmai "merchandise / in-sale").

Endpoints:
  GET    /quotations            paged; filters: status, customer_id, service_time, q (code|name)
  POST   /quotations            create (with lines)
  GET    /quotations/{id}       -> Quotation + lines
  PATCH  /quotations/{id}       partial update (status, external_name, service_time, ...)
  PATCH  /quotations/{id}/lines bulk replace lines
  DELETE /quotations/{id}       delete (cascades lines)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.schemas.quotations import (
    QuotationCreate,
    QuotationLineReplace,
    QuotationUpdate,
)
from app.services.quotations import quotations as svc

router = APIRouter(prefix="/api/v1/quotations", tags=["quotations"])


# --- List + create ------------------------------------------------------------

@router.get("", response_model=None)
def list_quotations_endpoint(
    status: str | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    service_time: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    items, total = svc.list_quotations(
        db,
        status=status,
        customer_id=customer_id,
        service_time=service_time,
        q=q,
    )
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_quotation_endpoint(
    payload: QuotationCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    try:
        q = svc.create_quotation(
            db,
            customer_id=payload.customer_id,
            external_name=payload.external_name,
            service_time=payload.service_time,
            pricing_cycle=payload.pricing_cycle,
            tags=payload.tags,
            description=payload.description,
            lines=[ln.model_dump() for ln in payload.lines],
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return svc.serialize_quotation(db, q)


# --- Single quotation ---------------------------------------------------------

@router.get("/{quotation_id}", response_model=None)
def get_quotation_endpoint(
    quotation_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    q = svc.get_quotation(db, quotation_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quotation not found")
    return svc.serialize_quotation(db, q)


@router.patch("/{quotation_id}", response_model=None)
def update_quotation_endpoint(
    quotation_id: str,
    payload: QuotationUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    q = svc.get_quotation(db, quotation_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quotation not found")
    fields = payload.model_dump(exclude_unset=True)
    try:
        q = svc.update_quotation(db, q, fields=fields, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return svc.serialize_quotation(db, q)


@router.patch("/{quotation_id}/lines", response_model=None)
def replace_lines_endpoint(
    quotation_id: str,
    payload: QuotationLineReplace,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    q = svc.get_quotation(db, quotation_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quotation not found")
    try:
        q = svc.replace_lines(
            db, q,
            lines=[ln.model_dump() for ln in payload.lines],
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return svc.serialize_quotation(db, q)


@router.delete("/{quotation_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_quotation_endpoint(
    quotation_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    q = svc.get_quotation(db, quotation_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quotation not found")
    svc.delete_quotation(db, q, actor=actor)
    db.commit()
    return None
