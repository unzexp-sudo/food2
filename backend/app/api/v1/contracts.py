"""CONTRACTS MODULE — owner: master data agent.

Two resources merged into one router (prefix /api/v1):
  GET/POST /contract-prices, DELETE /contract-prices/{id}
  GET/POST /standing-order-templates, GET/PATCH/DELETE /standing-order-templates/{id}
  POST /standing-order-templates/{id}/create-order
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.schemas.contracts import (
    ContractPriceCreate,
    ContractPriceOut,
    CreateOrderRequest,
    OrderOut,
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
)
from app.services.masterdata.contracts import (
    create_contract_price,
    create_order_from_template,
    create_template,
    delete_contract_price,
    delete_template,
    get_contract_price,
    get_template,
    list_contract_prices,
    list_templates,
    update_template,
)

contract_prices_router = APIRouter(prefix="/contract-prices", tags=["contract-prices"])
templates_router = APIRouter(prefix="/standing-order-templates", tags=["standing-order-templates"])
router = APIRouter(prefix="/api/v1", tags=["contracts"])
router.include_router(contract_prices_router)
router.include_router(templates_router)


# --- Contract Prices ---------------------------------------------------------
def _price_out(p) -> dict:
    return ContractPriceOut.model_validate(p).model_dump(mode="json")


@contract_prices_router.get("", response_model=None)
def list_contract_prices_endpoint(
    customer_id: str | None = Query(default=None),
    product_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    prices, total = list_contract_prices(db, customer_id=customer_id, product_id=product_id)
    start = (page - 1) * page_size
    items = [_price_out(p) for p in prices[start : start + page_size]]
    return page_response(items, total, page, page_size)


@contract_prices_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_contract_price_endpoint(
    payload: ContractPriceCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    try:
        price = create_contract_price(
            db,
            customer_id=payload.customer_id,
            product_id=payload.product_id,
            unit_id=payload.unit_id,
            price=payload.price,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _price_out(price)


@contract_prices_router.delete("/{price_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_contract_price_endpoint(
    price_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    price = get_contract_price(db, price_id)
    if price is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract price not found")
    delete_contract_price(db, price, actor=actor)
    db.commit()
    return None


# --- Standing Order Templates ------------------------------------------------
def _template_out(t) -> dict:
    return TemplateOut.model_validate(
        {
            "id": t.id,
            "customer_id": t.customer_id,
            "name": t.name,
            "delivery_days": t.delivery_days or [],
            "is_active": t.is_active,
            "lines": [
                {
                    "id": ln.id,
                    "product_id": ln.product_id,
                    "quantity": ln.quantity,
                    "unit_id": ln.unit_id,
                }
                for ln in (t.lines or [])
            ],
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
    ).model_dump(mode="json")


@templates_router.get("", response_model=None)
def list_templates_endpoint(
    customer_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    templates, total = list_templates(db, customer_id=customer_id)
    start = (page - 1) * page_size
    items = [_template_out(t) for t in templates[start : start + page_size]]
    return page_response(items, total, page, page_size)


@templates_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_template_endpoint(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    try:
        template = create_template(
            db,
            customer_id=payload.customer_id,
            name=payload.name,
            delivery_days=payload.delivery_days,
            is_active=payload.is_active,
            lines=[
                {"product_id": ln.product_id, "quantity": ln.quantity, "unit_id": ln.unit_id}
                for ln in payload.lines
            ],
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _template_out(template)


@templates_router.get("/{template_id}", response_model=None)
def get_template_endpoint(
    template_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    t = get_template(db, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    return _template_out(t)


@templates_router.patch("/{template_id}", response_model=None)
def update_template_endpoint(
    template_id: str,
    payload: TemplateUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    t = get_template(db, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    try:
        update_template(
            db, t,
            customer_id=payload.customer_id,
            name=payload.name,
            delivery_days=payload.delivery_days,
            is_active=payload.is_active,
            lines=(
                [
                    {"product_id": ln.product_id, "quantity": ln.quantity, "unit_id": ln.unit_id}
                    for ln in payload.lines
                ]
                if payload.lines is not None
                else None
            ),
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _template_out(t)


@templates_router.delete("/{template_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_template_endpoint(
    template_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    """Soft delete — is_active=False."""
    t = get_template(db, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    delete_template(db, t, actor=actor)
    db.commit()
    return None


# --- Create draft order from template ----------------------------------------
def _order_out(order) -> dict:
    from app.models import OrderLine
    lines = (
        db_lines if (db_lines := getattr(order, "lines", None)) else []
    )
    return OrderOut.model_validate(
        {
            "id": order.id,
            "order_number": order.order_number,
            "customer_id": order.customer_id,
            "status": order.status,
            "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
            "source_type": order.source_type,
            "intake_document_id": order.intake_document_id,
            "standing_template_id": order.standing_template_id,
            "overall_confidence": order.overall_confidence,
            "confirmed_by": order.confirmed_by,
            "confirmed_at": order.confirmed_at.isoformat() if order.confirmed_at else None,
            "notes": order.notes,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "lines": [
                {
                    "id": ln.id,
                    "order_id": ln.order_id,
                    "line_no": ln.line_no,
                    "raw_text": ln.raw_text,
                    "product_id": ln.product_id,
                    "product_display": ln.product_display,
                    "quantity": ln.quantity,
                    "unit_id": ln.unit_id,
                    "unit_price": ln.unit_price,
                    "confidence": ln.confidence,
                    "match_method": ln.match_method,
                }
                for ln in lines
            ],
        }
    ).model_dump(mode="json")


@templates_router.post("/{template_id}/create-order", response_model=None, status_code=status.HTTP_201_CREATED)
def create_order_from_template_endpoint(
    template_id: str,
    payload: CreateOrderRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    """Create a draft order from a standing order template.

    The order has status "draft", source_type "standing", standing_template_id
    set, one OrderLine per template line, and emits "order.draft_created" so
    auto-confirm evaluation can run.
    """
    t = get_template(db, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    order = create_order_from_template(
        db, t, delivery_date=payload.delivery_date, actor=actor,
    )
    db.commit()
    # Re-fetch the order with lines loaded (emit may have run in a handler).
    db.refresh(order)
    return _order_out(order)
