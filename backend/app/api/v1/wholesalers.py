"""WHOLESALERS MODULE — owner: master data agent.

Three resources merged into one router (prefix /api/v1):
  GET/POST /wholesalers, GET/PATCH/DELETE /wholesalers/{id}
  GET/POST /product-wholesaler-mappings, DELETE /product-wholesaler-mappings/{id}
  GET/POST /supplier-rules, PATCH/DELETE /supplier-rules/{id}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.schemas.wholesalers import (
    MappingCreate,
    MappingOut,
    SupplierRuleCreate,
    SupplierRuleOut,
    SupplierRuleUpdate,
    WholesalerCreate,
    WholesalerOut,
    WholesalerUpdate,
)
from app.services.masterdata.wholesalers import (
    create_rule,
    create_wholesaler,
    delete_mapping,
    delete_rule,
    delete_wholesaler,
    get_mapping,
    get_rule,
    get_wholesaler,
    list_mappings,
    list_rules,
    list_wholesalers,
    update_rule,
    update_wholesaler,
    upsert_mapping,
)

wholesalers_router = APIRouter(prefix="/wholesalers", tags=["wholesalers"])
mappings_router = APIRouter(prefix="/product-wholesaler-mappings", tags=["product-wholesaler-mappings"])
rules_router = APIRouter(prefix="/supplier-rules", tags=["supplier-rules"])
router = APIRouter(prefix="/api/v1", tags=["wholesalers"])
router.include_router(wholesalers_router)
router.include_router(mappings_router)
router.include_router(rules_router)


# --- Wholesalers --------------------------------------------------------------
def _wholesaler_out(w) -> dict:
    return WholesalerOut.model_validate(
        {
            "id": w.id,
            "code": w.code,
            "name_en": w.name_en,
            "name_zh": w.name_zh,
            "contact_name": w.contact_name,
            "contact_phone": w.contact_phone,
            "is_active": w.is_active,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
    ).model_dump(mode="json")


@wholesalers_router.get("", response_model=None)
def list_wholesalers_endpoint(
    q: str | None = Query(default=None, description="Search code/name_en/name_zh"),
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    wholesalers, total = list_wholesalers(db, q=q, is_active=is_active)
    start = (page - 1) * page_size
    items = [_wholesaler_out(w) for w in wholesalers[start : start + page_size]]
    return page_response(items, total, page, page_size)


@wholesalers_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_wholesaler_endpoint(
    payload: WholesalerCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    try:
        w = create_wholesaler(
            db,
            code=payload.code,
            name_en=payload.name_en,
            name_zh=payload.name_zh,
            contact_name=payload.contact_name,
            contact_phone=payload.contact_phone,
            is_active=payload.is_active,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _wholesaler_out(w)


@wholesalers_router.get("/{wholesaler_id}", response_model=None)
def get_wholesaler_endpoint(
    wholesaler_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    w = get_wholesaler(db, wholesaler_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wholesaler not found")
    return _wholesaler_out(w)


@wholesalers_router.patch("/{wholesaler_id}", response_model=None)
def update_wholesaler_endpoint(
    wholesaler_id: str,
    payload: WholesalerUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    w = get_wholesaler(db, wholesaler_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wholesaler not found")
    try:
        update_wholesaler(
            db, w,
            code=payload.code,
            name_en=payload.name_en,
            name_zh=payload.name_zh,
            contact_name=payload.contact_name,
            contact_phone=payload.contact_phone,
            is_active=payload.is_active,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _wholesaler_out(w)


@wholesalers_router.delete("/{wholesaler_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_wholesaler_endpoint(
    wholesaler_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    w = get_wholesaler(db, wholesaler_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wholesaler not found")
    try:
        delete_wholesaler(db, w, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return None


# --- Product-Wholesaler Mappings ----------------------------------------------
def _mapping_out(m) -> dict:
    return MappingOut.model_validate(m).model_dump(mode="json")


@mappings_router.get("", response_model=None)
def list_mappings_endpoint(
    product_id: str | None = Query(default=None),
    wholesaler_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    mappings, total = list_mappings(db, product_id=product_id, wholesaler_id=wholesaler_id)
    start = (page - 1) * page_size
    items = [_mapping_out(m) for m in mappings[start : start + page_size]]
    return page_response(items, total, page, page_size)


@mappings_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_mapping_endpoint(
    payload: MappingCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    """Upsert: one mapping per (product_id, wholesaler_id)."""
    try:
        m = upsert_mapping(
            db,
            product_id=payload.product_id,
            wholesaler_id=payload.wholesaler_id,
            supplier_sku=payload.supplier_sku,
            cost_price=payload.cost_price,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _mapping_out(m)


@mappings_router.delete("/{mapping_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_mapping_endpoint(
    mapping_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    m = get_mapping(db, mapping_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mapping not found")
    delete_mapping(db, m, actor=actor)
    db.commit()
    return None


# --- Supplier Rules ----------------------------------------------------------
def _rule_out(r) -> dict:
    return SupplierRuleOut.model_validate(r).model_dump(mode="json")


@rules_router.get("", response_model=None)
def list_rules_endpoint(
    category_id: str | None = Query(default=None),
    product_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    rules, total = list_rules(db, category_id=category_id, product_id=product_id)
    start = (page - 1) * page_size
    items = [_rule_out(r) for r in rules[start : start + page_size]]
    return page_response(items, total, page, page_size)


@rules_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_rule_endpoint(
    payload: SupplierRuleCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    if not payload.category_id and not payload.product_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "At least one of category_id or product_id must be set",
        )
    try:
        rule = create_rule(
            db,
            category_id=payload.category_id,
            product_id=payload.product_id,
            wholesaler_id=payload.wholesaler_id,
            priority=payload.priority,
            moq=payload.moq,
            lead_time_days=payload.lead_time_days,
            is_default=payload.is_default,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _rule_out(rule)


@rules_router.patch("/{rule_id}", response_model=None)
def update_rule_endpoint(
    rule_id: str,
    payload: SupplierRuleUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    rule = get_rule(db, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier rule not found")
    try:
        update_rule(
            db, rule,
            category_id=payload.category_id,
            product_id=payload.product_id,
            wholesaler_id=payload.wholesaler_id,
            priority=payload.priority,
            moq=payload.moq,
            lead_time_days=payload.lead_time_days,
            is_default=payload.is_default,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _rule_out(rule)


@rules_router.delete("/{rule_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_rule_endpoint(
    rule_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    rule = get_rule(db, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier rule not found")
    delete_rule(db, rule, actor=actor)
    db.commit()
    return None
