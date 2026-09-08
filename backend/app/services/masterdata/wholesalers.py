"""Wholesaler service functions: wholesalers, mappings, supplier rules."""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.models import (
    Product,
    ProductWholesalerMapping,
    SupplierRule,
    User,
    Wholesaler,
)


# --- Wholesalers --------------------------------------------------------------
def _wholesaler_to_dict(w: Wholesaler) -> dict[str, Any]:
    return {
        "id": w.id,
        "code": w.code,
        "name_en": w.name_en,
        "name_zh": w.name_zh,
        "contact_name": w.contact_name,
        "contact_phone": w.contact_phone,
        "is_active": w.is_active,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


def list_wholesalers(
    db: Session, *, q: str | None = None, is_active: bool | None = None
) -> tuple[list[Wholesaler], int]:
    query = db.query(Wholesaler)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(
                Wholesaler.code.ilike(like),
                Wholesaler.name_en.ilike(like),
                Wholesaler.name_zh.ilike(like),
            )
        )
    if is_active is not None:
        query = query.filter(Wholesaler.is_active.is_(is_active))
    total = query.count()
    return query.order_by(Wholesaler.created_at).all(), total


def get_wholesaler(db: Session, w_id: str) -> Wholesaler | None:
    return db.get(Wholesaler, w_id)


def create_wholesaler(
    db: Session,
    *,
    code: str,
    name_en: str,
    name_zh: str,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    is_active: bool = True,
    actor: User | None = None,
    commit: bool = False,
) -> Wholesaler:
    if db.query(Wholesaler).filter(Wholesaler.code == code).first() is not None:
        raise ValueError(f"Wholesaler code already exists: {code}")
    w = Wholesaler(
        code=code,
        name_en=name_en,
        name_zh=name_zh,
        contact_name=contact_name,
        contact_phone=contact_phone,
        is_active=is_active,
    )
    db.add(w)
    db.flush()
    log_audit(
        db, actor, "Wholesaler", w.id, "create",
        before=None, after=_wholesaler_to_dict(w),
        summary=f"Created wholesaler {w.code} ({w.name_en})",
    )
    if commit:
        db.commit()
    return w


def update_wholesaler(
    db: Session,
    w: Wholesaler,
    *,
    code: str | None = None,
    name_en: str | None = None,
    name_zh: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    is_active: bool | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> Wholesaler:
    before = _wholesaler_to_dict(w)
    if code is not None:
        existing = db.query(Wholesaler).filter(Wholesaler.code == code, Wholesaler.id != w.id).first()
        if existing is not None:
            raise ValueError(f"Wholesaler code already exists: {code}")
        w.code = code
    if name_en is not None:
        w.name_en = name_en
    if name_zh is not None:
        w.name_zh = name_zh
    if contact_name is not None:
        w.contact_name = contact_name
    if contact_phone is not None:
        w.contact_phone = contact_phone
    if is_active is not None:
        w.is_active = is_active
    db.flush()
    log_audit(
        db, actor, "Wholesaler", w.id, "update",
        before=before, after=_wholesaler_to_dict(w),
        summary=f"Updated wholesaler {w.code}",
    )
    if commit:
        db.commit()
    return w


def delete_wholesaler(
    db: Session,
    w: Wholesaler,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> None:
    # Check in-use: mappings or supplier rules referencing this wholesaler.
    mapping_count = (
        db.query(ProductWholesalerMapping)
        .filter(ProductWholesalerMapping.wholesaler_id == w.id)
        .count()
    )
    rule_count = (
        db.query(SupplierRule).filter(SupplierRule.wholesaler_id == w.id).count()
    )
    if mapping_count > 0 or rule_count > 0:
        raise ValueError(
            f"Wholesaler '{w.code}' is in use "
            f"(mappings: {mapping_count}, rules: {rule_count})"
        )
    log_audit(
        db, actor, "Wholesaler", w.id, "delete",
        before=_wholesaler_to_dict(w), after=None,
        summary=f"Deleted wholesaler {w.code}",
    )
    db.delete(w)
    db.flush()
    if commit:
        db.commit()


# --- Product-Wholesaler Mappings (upsert per product+wholesaler) ---------------
def _mapping_to_dict(m: ProductWholesalerMapping) -> dict[str, Any]:
    return {
        "id": m.id,
        "product_id": m.product_id,
        "wholesaler_id": m.wholesaler_id,
        "supplier_sku": m.supplier_sku,
        "cost_price": m.cost_price,
    }


def list_mappings(
    db: Session,
    *,
    product_id: str | None = None,
    wholesaler_id: str | None = None,
) -> tuple[list[ProductWholesalerMapping], int]:
    query = db.query(ProductWholesalerMapping)
    if product_id:
        query = query.filter(ProductWholesalerMapping.product_id == product_id)
    if wholesaler_id:
        query = query.filter(ProductWholesalerMapping.wholesaler_id == wholesaler_id)
    total = query.count()
    return query.all(), total


def upsert_mapping(
    db: Session,
    *,
    product_id: str,
    wholesaler_id: str,
    supplier_sku: str | None = None,
    cost_price: float | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> ProductWholesalerMapping:
    if db.get(Product, product_id) is None:
        raise ValueError(f"Product not found: {product_id}")
    if db.get(Wholesaler, wholesaler_id) is None:
        raise ValueError(f"Wholesaler not found: {wholesaler_id}")
    existing = (
        db.query(ProductWholesalerMapping)
        .filter(
            ProductWholesalerMapping.product_id == product_id,
            ProductWholesalerMapping.wholesaler_id == wholesaler_id,
        )
        .first()
    )
    if existing is not None:
        before = _mapping_to_dict(existing)
        existing.supplier_sku = supplier_sku
        existing.cost_price = cost_price
        db.flush()
        log_audit(
            db, actor, "ProductWholesalerMapping", existing.id, "update",
            before=before, after=_mapping_to_dict(existing),
            summary=f"Updated mapping product={product_id} wholesaler={wholesaler_id}",
        )
        result = existing
    else:
        row = ProductWholesalerMapping(
            product_id=product_id,
            wholesaler_id=wholesaler_id,
            supplier_sku=supplier_sku,
            cost_price=cost_price,
        )
        db.add(row)
        db.flush()
        log_audit(
            db, actor, "ProductWholesalerMapping", row.id, "create",
            before=None, after=_mapping_to_dict(row),
            summary=f"Created mapping product={product_id} wholesaler={wholesaler_id}",
        )
        result = row
    if commit:
        db.commit()
    return result


def get_mapping(db: Session, mapping_id: str) -> ProductWholesalerMapping | None:
    return db.get(ProductWholesalerMapping, mapping_id)


def delete_mapping(
    db: Session,
    m: ProductWholesalerMapping,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> None:
    log_audit(
        db, actor, "ProductWholesalerMapping", m.id, "delete",
        before=_mapping_to_dict(m), after=None,
        summary=f"Deleted mapping product={m.product_id} wholesaler={m.wholesaler_id}",
    )
    db.delete(m)
    db.flush()
    if commit:
        db.commit()


# --- Supplier Rules -----------------------------------------------------------
def _rule_to_dict(r: SupplierRule) -> dict[str, Any]:
    return {
        "id": r.id,
        "category_id": r.category_id,
        "product_id": r.product_id,
        "wholesaler_id": r.wholesaler_id,
        "priority": r.priority,
        "moq": r.moq,
        "lead_time_days": r.lead_time_days,
        "is_default": r.is_default,
    }


def list_rules(
    db: Session,
    *,
    category_id: str | None = None,
    product_id: str | None = None,
) -> tuple[list[SupplierRule], int]:
    query = db.query(SupplierRule)
    if category_id:
        query = query.filter(SupplierRule.category_id == category_id)
    if product_id:
        query = query.filter(SupplierRule.product_id == product_id)
    total = query.count()
    return query.order_by(SupplierRule.priority.desc()).all(), total


def create_rule(
    db: Session,
    *,
    category_id: str | None = None,
    product_id: str | None = None,
    wholesaler_id: str,
    priority: int = 0,
    moq: float | None = None,
    lead_time_days: int = 1,
    is_default: bool = False,
    actor: User | None = None,
    commit: bool = False,
) -> SupplierRule:
    if not category_id and not product_id:
        raise ValueError("At least one of category_id or product_id must be set")
    if db.get(Wholesaler, wholesaler_id) is None:
        raise ValueError(f"Wholesaler not found: {wholesaler_id}")
    if category_id is not None:
        from app.models import ProductCategory
        if db.get(ProductCategory, category_id) is None:
            raise ValueError(f"Category not found: {category_id}")
    if product_id is not None and db.get(Product, product_id) is None:
        raise ValueError(f"Product not found: {product_id}")
    rule = SupplierRule(
        category_id=category_id,
        product_id=product_id,
        wholesaler_id=wholesaler_id,
        priority=priority,
        moq=moq,
        lead_time_days=lead_time_days,
        is_default=is_default,
    )
    db.add(rule)
    db.flush()
    log_audit(
        db, actor, "SupplierRule", rule.id, "create",
        before=None, after=_rule_to_dict(rule),
        summary=f"Created supplier rule (wholesaler={wholesaler_id})",
    )
    if commit:
        db.commit()
    return rule


def get_rule(db: Session, rule_id: str) -> SupplierRule | None:
    return db.get(SupplierRule, rule_id)


def update_rule(
    db: Session,
    rule: SupplierRule,
    *,
    category_id: str | None = None,
    product_id: str | None = None,
    wholesaler_id: str | None = None,
    priority: int | None = None,
    moq: float | None = None,
    lead_time_days: int | None = None,
    is_default: bool | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> SupplierRule:
    before = _rule_to_dict(rule)
    if category_id is not None:
        rule.category_id = category_id
    if product_id is not None:
        rule.product_id = product_id
    if wholesaler_id is not None:
        if db.get(Wholesaler, wholesaler_id) is None:
            raise ValueError(f"Wholesaler not found: {wholesaler_id}")
        rule.wholesaler_id = wholesaler_id
    if priority is not None:
        rule.priority = priority
    if moq is not None:
        rule.moq = moq
    if lead_time_days is not None:
        rule.lead_time_days = lead_time_days
    if is_default is not None:
        rule.is_default = is_default
    db.flush()
    # Validate: at least one of category_id or product_id must be set after update.
    if not rule.category_id and not rule.product_id:
        raise ValueError("At least one of category_id or product_id must be set")
    log_audit(
        db, actor, "SupplierRule", rule.id, "update",
        before=before, after=_rule_to_dict(rule),
        summary=f"Updated supplier rule {rule.id}",
    )
    if commit:
        db.commit()
    return rule


def delete_rule(
    db: Session,
    rule: SupplierRule,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> None:
    log_audit(
        db, actor, "SupplierRule", rule.id, "delete",
        before=_rule_to_dict(rule), after=None,
        summary=f"Deleted supplier rule {rule.id}",
    )
    db.delete(rule)
    db.flush()
    if commit:
        db.commit()
