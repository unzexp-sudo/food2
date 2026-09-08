"""Catalog service functions: products, categories, units."""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.models import (
    ContractPrice,
    CustomerProductAlias,
    OrderLine,
    Product,
    ProductCategory,
    ProductWholesalerMapping,
    SupplierRule,
    Unit,
    User,
)


# --- Categories ---------------------------------------------------------------
def _category_to_dict(c: ProductCategory) -> dict[str, Any]:
    return {
        "id": c.id,
        "name_en": c.name_en,
        "name_zh": c.name_zh,
        "is_active": c.is_active,
    }


def list_categories(
    db: Session, *, q: str | None = None
) -> tuple[list[ProductCategory], int]:
    query = db.query(ProductCategory)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(ProductCategory.name_en.ilike(like), ProductCategory.name_zh.ilike(like))
        )
    total = query.count()
    return query.order_by(ProductCategory.name_en).all(), total


def create_category(
    db: Session,
    *,
    name_en: str,
    name_zh: str,
    is_active: bool = True,
    actor: User | None = None,
    commit: bool = False,
) -> ProductCategory:
    cat = ProductCategory(name_en=name_en, name_zh=name_zh, is_active=is_active)
    db.add(cat)
    db.flush()
    log_audit(
        db, actor, "ProductCategory", cat.id, "create",
        before=None, after=_category_to_dict(cat),
        summary=f"Created category {cat.name_en}",
    )
    if commit:
        db.commit()
    return cat


def get_category(db: Session, cat_id: str) -> ProductCategory | None:
    return db.get(ProductCategory, cat_id)


def update_category(
    db: Session,
    cat: ProductCategory,
    *,
    name_en: str | None = None,
    name_zh: str | None = None,
    is_active: bool | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> ProductCategory:
    before = _category_to_dict(cat)
    if name_en is not None:
        cat.name_en = name_en
    if name_zh is not None:
        cat.name_zh = name_zh
    if is_active is not None:
        cat.is_active = is_active
    db.flush()
    log_audit(
        db, actor, "ProductCategory", cat.id, "update",
        before=before, after=_category_to_dict(cat),
        summary=f"Updated category {cat.name_en}",
    )
    if commit:
        db.commit()
    return cat


def delete_category(
    db: Session,
    cat: ProductCategory,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> None:
    # Check in-use: any product referencing this category, or any supplier rule.
    product_count = (
        db.query(Product).filter(Product.category_id == cat.id).count()
    )
    rule_count = (
        db.query(SupplierRule).filter(SupplierRule.category_id == cat.id).count()
    )
    if product_count > 0 or rule_count > 0:
        raise ValueError(
            f"Category '{cat.name_en}' is in use "
            f"(products: {product_count}, rules: {rule_count})"
        )
    log_audit(
        db, actor, "ProductCategory", cat.id, "delete",
        before=_category_to_dict(cat), after=None,
        summary=f"Deleted category {cat.name_en}",
    )
    db.delete(cat)
    db.flush()
    if commit:
        db.commit()


# --- Units --------------------------------------------------------------------
def _unit_to_dict(u: Unit) -> dict[str, Any]:
    return {"id": u.id, "code": u.code, "name_en": u.name_en, "name_zh": u.name_zh}


def list_units(
    db: Session, *, q: str | None = None
) -> tuple[list[Unit], int]:
    query = db.query(Unit)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(Unit.code.ilike(like), Unit.name_en.ilike(like), Unit.name_zh.ilike(like))
        )
    total = query.count()
    return query.order_by(Unit.code).all(), total


def create_unit(
    db: Session,
    *,
    code: str,
    name_en: str,
    name_zh: str,
    actor: User | None = None,
    commit: bool = False,
) -> Unit:
    if db.query(Unit).filter(Unit.code == code).first() is not None:
        raise ValueError(f"Unit code already exists: {code}")
    unit = Unit(code=code, name_en=name_en, name_zh=name_zh)
    db.add(unit)
    db.flush()
    log_audit(
        db, actor, "Unit", unit.id, "create",
        before=None, after=_unit_to_dict(unit),
        summary=f"Created unit {unit.code}",
    )
    if commit:
        db.commit()
    return unit


def get_unit(db: Session, unit_id: str) -> Unit | None:
    return db.get(Unit, unit_id)


def update_unit(
    db: Session,
    unit: Unit,
    *,
    code: str | None = None,
    name_en: str | None = None,
    name_zh: str | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> Unit:
    before = _unit_to_dict(unit)
    if code is not None:
        existing = db.query(Unit).filter(Unit.code == code, Unit.id != unit.id).first()
        if existing is not None:
            raise ValueError(f"Unit code already exists: {code}")
        unit.code = code
    if name_en is not None:
        unit.name_en = name_en
    if name_zh is not None:
        unit.name_zh = name_zh
    db.flush()
    log_audit(
        db, actor, "Unit", unit.id, "update",
        before=before, after=_unit_to_dict(unit),
        summary=f"Updated unit {unit.code}",
    )
    if commit:
        db.commit()
    return unit


def delete_unit(
    db: Session,
    unit: Unit,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> None:
    # Check in-use: products, contract prices, standing order template lines, order lines.
    product_count = (
        db.query(Product).filter(Product.default_unit_id == unit.id).count()
    )
    contract_count = (
        db.query(ContractPrice).filter(ContractPrice.unit_id == unit.id).count()
    )
    from app.models import StandingOrderTemplateLine
    template_count = (
        db.query(StandingOrderTemplateLine)
        .filter(StandingOrderTemplateLine.unit_id == unit.id)
        .count()
    )
    order_line_count = (
        db.query(OrderLine).filter(OrderLine.unit_id == unit.id).count()
    )
    total_in_use = product_count + contract_count + template_count + order_line_count
    if total_in_use > 0:
        raise ValueError(
            f"Unit '{unit.code}' is in use "
            f"(products: {product_count}, contracts: {contract_count}, "
            f"templates: {template_count}, order_lines: {order_line_count})"
        )
    log_audit(
        db, actor, "Unit", unit.id, "delete",
        before=_unit_to_dict(unit), after=None,
        summary=f"Deleted unit {unit.code}",
    )
    db.delete(unit)
    db.flush()
    if commit:
        db.commit()


# --- Products -----------------------------------------------------------------
def _product_to_dict(p: Product) -> dict[str, Any]:
    return {
        "id": p.id,
        "sku": p.sku,
        "name_en": p.name_en,
        "name_zh": p.name_zh,
        "category_id": p.category_id,
        "default_unit_id": p.default_unit_id,
        "shelf_life_days": p.shelf_life_days,
        "is_active": p.is_active,
    }


def list_products(
    db: Session,
    *,
    q: str | None = None,
    category_id: str | None = None,
    is_active: bool | None = None,
) -> tuple[list[Product], int]:
    query = db.query(Product)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(
                Product.sku.ilike(like),
                Product.name_en.ilike(like),
                Product.name_zh.ilike(like),
            )
        )
    if category_id:
        query = query.filter(Product.category_id == category_id)
    if is_active is not None:
        query = query.filter(Product.is_active.is_(is_active))
    total = query.count()
    return query.order_by(Product.sku).all(), total


def get_product(db: Session, product_id: str) -> Product | None:
    return db.get(Product, product_id)


def create_product(
    db: Session,
    *,
    sku: str,
    name_en: str,
    name_zh: str,
    category_id: str | None = None,
    default_unit_id: str | None = None,
    shelf_life_days: int | None = None,
    is_active: bool = True,
    actor: User | None = None,
    commit: bool = False,
) -> Product:
    if db.query(Product).filter(Product.sku == sku).first() is not None:
        raise ValueError(f"Product SKU already exists: {sku}")
    if category_id is not None and db.get(ProductCategory, category_id) is None:
        raise ValueError(f"Category not found: {category_id}")
    if default_unit_id is not None and db.get(Unit, default_unit_id) is None:
        raise ValueError(f"Unit not found: {default_unit_id}")
    product = Product(
        sku=sku,
        name_en=name_en,
        name_zh=name_zh,
        category_id=category_id,
        default_unit_id=default_unit_id,
        shelf_life_days=shelf_life_days,
        is_active=is_active,
    )
    db.add(product)
    db.flush()
    log_audit(
        db, actor, "Product", product.id, "create",
        before=None, after=_product_to_dict(product),
        summary=f"Created product {product.sku} ({product.name_en})",
    )
    if commit:
        db.commit()
    return product


def update_product(
    db: Session,
    product: Product,
    *,
    sku: str | None = None,
    name_en: str | None = None,
    name_zh: str | None = None,
    category_id: str | None = None,
    default_unit_id: str | None = None,
    shelf_life_days: int | None = None,
    is_active: bool | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> Product:
    before = _product_to_dict(product)
    if sku is not None:
        existing = db.query(Product).filter(Product.sku == sku, Product.id != product.id).first()
        if existing is not None:
            raise ValueError(f"Product SKU already exists: {sku}")
        product.sku = sku
    if name_en is not None:
        product.name_en = name_en
    if name_zh is not None:
        product.name_zh = name_zh
    if category_id is not None:
        if db.get(ProductCategory, category_id) is None:
            raise ValueError(f"Category not found: {category_id}")
        product.category_id = category_id
    if default_unit_id is not None:
        if db.get(Unit, default_unit_id) is None:
            raise ValueError(f"Unit not found: {default_unit_id}")
        product.default_unit_id = default_unit_id
    if shelf_life_days is not None:
        product.shelf_life_days = shelf_life_days
    if is_active is not None:
        product.is_active = is_active
    db.flush()
    log_audit(
        db, actor, "Product", product.id, "update",
        before=before, after=_product_to_dict(product),
        summary=f"Updated product {product.sku}",
    )
    if commit:
        db.commit()
    return product


def soft_delete_product(
    db: Session,
    product: Product,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> Product:
    before = _product_to_dict(product)
    product.is_active = False
    db.flush()
    log_audit(
        db, actor, "Product", product.id, "delete",
        before=before, after=_product_to_dict(product),
        summary=f"Deactivated product {product.sku}",
    )
    if commit:
        db.commit()
    return product


def get_product_mappings(db: Session, product_id: str) -> list[ProductWholesalerMapping]:
    return (
        db.query(ProductWholesalerMapping)
        .filter(ProductWholesalerMapping.product_id == product_id)
        .all()
    )
