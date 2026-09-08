"""CATALOG MODULE — owner: master data agent.

Three resources merged into one router (prefix /api/v1):
  GET/POST /products, GET/PATCH/DELETE /products/{id}, GET /products/{id}/wholesalers
  GET/POST /product-categories, PATCH/DELETE /product-categories/{id}
  GET/POST /units, PATCH/DELETE /units/{id}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.schemas.catalog import (
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    UnitCreate,
    UnitOut,
    UnitUpdate,
)
from app.services.masterdata.catalog import (
    create_category,
    create_product,
    create_unit,
    delete_category,
    delete_unit,
    get_category,
    get_product,
    get_unit,
    get_product_mappings,
    list_categories,
    list_products,
    list_units,
    soft_delete_product,
    update_category,
    update_product,
    update_unit,
)

# Sub-routers — merged into `router` at the bottom. Parent router has prefix
# /api/v1, so sub-router prefixes are relative to that.
products_router = APIRouter(prefix="/products", tags=["products"])
categories_router = APIRouter(prefix="/product-categories", tags=["product-categories"])
units_router = APIRouter(prefix="/units", tags=["units"])
router = APIRouter(prefix="/api/v1", tags=["catalog"])
router.include_router(products_router)
router.include_router(categories_router)
router.include_router(units_router)


# --- Products -----------------------------------------------------------------
def _product_out(p) -> dict:
    cat_en = p.category.name_en if p.category else None
    cat_zh = p.category.name_zh if p.category else None
    unit_code = p.default_unit.code if p.default_unit else None
    return ProductOut.model_validate(
        {
            "id": p.id,
            "sku": p.sku,
            "name_en": p.name_en,
            "name_zh": p.name_zh,
            "category_id": p.category_id,
            "category_name_en": cat_en,
            "category_name_zh": cat_zh,
            "default_unit_id": p.default_unit_id,
            "default_unit_code": unit_code,
            "shelf_life_days": p.shelf_life_days,
            "is_active": p.is_active,
        }
    ).model_dump(mode="json")


@products_router.get("", response_model=None)
def list_products_endpoint(
    q: str | None = Query(default=None, description="Search sku/name_en/name_zh"),
    category_id: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    page, page_size = clamp_page(page, page_size)
    products, total = list_products(db, q=q, category_id=category_id, is_active=is_active)
    start = (page - 1) * page_size
    items = [_product_out(p) for p in products[start : start + page_size]]
    return page_response(items, total, page, page_size)


@products_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_product_endpoint(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    try:
        product = create_product(
            db,
            sku=payload.sku,
            name_en=payload.name_en,
            name_zh=payload.name_zh,
            category_id=payload.category_id,
            default_unit_id=payload.default_unit_id,
            shelf_life_days=payload.shelf_life_days,
            is_active=payload.is_active,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _product_out(product)


@products_router.get("/{product_id}", response_model=None)
def get_product_endpoint(
    product_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    product = get_product(db, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return _product_out(product)


@products_router.patch("/{product_id}", response_model=None)
def update_product_endpoint(
    product_id: str,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    product = get_product(db, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    try:
        update_product(
            db,
            product,
            sku=payload.sku,
            name_en=payload.name_en,
            name_zh=payload.name_zh,
            category_id=payload.category_id,
            default_unit_id=payload.default_unit_id,
            shelf_life_days=payload.shelf_life_days,
            is_active=payload.is_active,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _product_out(product)


@products_router.delete("/{product_id}", response_model=None)
def delete_product_endpoint(
    product_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    """Soft delete — is_active=False."""
    product = get_product(db, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    soft_delete_product(db, product, actor=actor)
    db.commit()
    return _product_out(product)


@products_router.get("/{product_id}/wholesalers", response_model=None)
def get_product_wholesalers_endpoint(
    product_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops")),
):
    product = get_product(db, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    mappings = get_product_mappings(db, product_id)
    return [
        {
            "id": m.id,
            "product_id": m.product_id,
            "wholesaler_id": m.wholesaler_id,
            "supplier_sku": m.supplier_sku,
            "cost_price": m.cost_price,
        }
        for m in mappings
    ]


# --- Categories ---------------------------------------------------------------
def _category_out(c) -> dict:
    return CategoryOut.model_validate(c).model_dump(mode="json")


@categories_router.get("", response_model=None)
def list_categories_endpoint(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    page, page_size = clamp_page(page, page_size)
    cats, total = list_categories(db)
    start = (page - 1) * page_size
    items = [_category_out(c) for c in cats[start : start + page_size]]
    return page_response(items, total, page, page_size)


@categories_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_category_endpoint(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    cat = create_category(
        db,
        name_en=payload.name_en,
        name_zh=payload.name_zh,
        is_active=payload.is_active,
        actor=actor,
    )
    db.commit()
    return _category_out(cat)


@categories_router.patch("/{category_id}", response_model=None)
def update_category_endpoint(
    category_id: str,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    cat = get_category(db, category_id)
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    update_category(
        db, cat,
        name_en=payload.name_en, name_zh=payload.name_zh, is_active=payload.is_active,
        actor=actor,
    )
    db.commit()
    return _category_out(cat)


@categories_router.delete("/{category_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_category_endpoint(
    category_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    cat = get_category(db, category_id)
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    try:
        delete_category(db, cat, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return None


# --- Units --------------------------------------------------------------------
def _unit_out(u) -> dict:
    return UnitOut.model_validate(u).model_dump(mode="json")


@units_router.get("", response_model=None)
def list_units_endpoint(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    page, page_size = clamp_page(page, page_size)
    units, total = list_units(db)
    start = (page - 1) * page_size
    items = [_unit_out(u) for u in units[start : start + page_size]]
    return page_response(items, total, page, page_size)


@units_router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_unit_endpoint(
    payload: UnitCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    try:
        unit = create_unit(
            db, code=payload.code, name_en=payload.name_en, name_zh=payload.name_zh,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _unit_out(unit)


@units_router.patch("/{unit_id}", response_model=None)
def update_unit_endpoint(
    unit_id: str,
    payload: UnitUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    unit = get_unit(db, unit_id)
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found")
    try:
        update_unit(
            db, unit,
            code=payload.code, name_en=payload.name_en, name_zh=payload.name_zh,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _unit_out(unit)


@units_router.delete("/{unit_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_unit_endpoint(
    unit_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    unit = get_unit(db, unit_id)
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found")
    try:
        delete_unit(db, unit, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return None
