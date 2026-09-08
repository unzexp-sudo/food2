"""Customer service functions."""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.models import Customer, CustomerContact, CustomerProductAlias, Product, User


def _customer_to_dict(c: Customer) -> dict[str, Any]:
    return {
        "id": c.id,
        "code": c.code,
        "name_en": c.name_en,
        "name_zh": c.name_zh,
        "type": c.type,
        "contact_name": c.contact_name,
        "contact_phone": c.contact_phone,
        "address": c.address,
        "delivery_zone": c.delivery_zone,
        "notes": c.notes,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def list_customers(
    db: Session,
    *,
    q: str | None = None,
    type: str | None = None,
    status: str | None = None,
) -> tuple[list[Customer], int]:
    query = db.query(Customer)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(
                Customer.code.ilike(like),
                Customer.name_en.ilike(like),
                Customer.name_zh.ilike(like),
            )
        )
    if type:
        query = query.filter(Customer.type == type)
    if status:
        query = query.filter(Customer.status == status)
    total = query.count()
    return query.order_by(Customer.created_at).all(), total


def get_customer(db: Session, customer_id: str) -> Customer | None:
    return db.get(Customer, customer_id)


def create_customer(
    db: Session,
    *,
    code: str,
    name_en: str,
    name_zh: str,
    type: str = "other",
    contact_name: str | None = None,
    contact_phone: str | None = None,
    address: str | None = None,
    delivery_zone: str | None = None,
    notes: str | None = None,
    status: str = "active",
    actor: User | None = None,
    commit: bool = False,
) -> Customer:
    if db.query(Customer).filter(Customer.code == code).first() is not None:
        raise ValueError(f"Customer code already exists: {code}")
    customer = Customer(
        code=code,
        name_en=name_en,
        name_zh=name_zh,
        type=type,
        contact_name=contact_name,
        contact_phone=contact_phone,
        address=address,
        delivery_zone=delivery_zone,
        notes=notes,
        status=status,
    )
    db.add(customer)
    db.flush()
    log_audit(
        db, actor, "Customer", customer.id, "create",
        before=None, after=_customer_to_dict(customer),
        summary=f"Created customer {customer.code} ({customer.name_en})",
    )
    if commit:
        db.commit()
    return customer


def update_customer(
    db: Session,
    customer: Customer,
    *,
    code: str | None = None,
    name_en: str | None = None,
    name_zh: str | None = None,
    type: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    address: str | None = None,
    delivery_zone: str | None = None,
    notes: str | None = None,
    status: str | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> Customer:
    before = _customer_to_dict(customer)
    if code is not None:
        existing = db.query(Customer).filter(Customer.code == code, Customer.id != customer.id).first()
        if existing is not None:
            raise ValueError(f"Customer code already exists: {code}")
        customer.code = code
    if name_en is not None:
        customer.name_en = name_en
    if name_zh is not None:
        customer.name_zh = name_zh
    if type is not None:
        customer.type = type
    if contact_name is not None:
        customer.contact_name = contact_name
    if contact_phone is not None:
        customer.contact_phone = contact_phone
    if address is not None:
        customer.address = address
    if delivery_zone is not None:
        customer.delivery_zone = delivery_zone
    if notes is not None:
        customer.notes = notes
    if status is not None:
        customer.status = status
    db.flush()
    log_audit(
        db, actor, "Customer", customer.id, "update",
        before=before, after=_customer_to_dict(customer),
        summary=f"Updated customer {customer.code}",
    )
    if commit:
        db.commit()
    return customer


def soft_delete_customer(
    db: Session,
    customer: Customer,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> Customer:
    before = _customer_to_dict(customer)
    customer.status = "inactive"
    db.flush()
    log_audit(
        db, actor, "Customer", customer.id, "delete",
        before=before, after=_customer_to_dict(customer),
        summary=f"Deactivated customer {customer.code}",
    )
    if commit:
        db.commit()
    return customer


# --- Contacts -----------------------------------------------------------------
def list_contacts(db: Session, customer_id: str) -> list[CustomerContact]:
    return (
        db.query(CustomerContact)
        .filter(CustomerContact.customer_id == customer_id)
        .all()
    )


def create_contact(
    db: Session,
    customer_id: str,
    *,
    name: str,
    phone: str | None = None,
    role: str | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> CustomerContact:
    contact = CustomerContact(
        customer_id=customer_id, name=name, phone=phone, role=role,
    )
    db.add(contact)
    db.flush()
    log_audit(
        db, actor, "CustomerContact", contact.id, "create",
        before=None,
        after={"customer_id": customer_id, "name": name, "phone": phone, "role": role},
        summary=f"Added contact {name} to customer {customer_id}",
    )
    if commit:
        db.commit()
    return contact


def get_contact(db: Session, contact_id: str) -> CustomerContact | None:
    return db.get(CustomerContact, contact_id)


def delete_contact(
    db: Session,
    contact: CustomerContact,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> None:
    log_audit(
        db, actor, "CustomerContact", contact.id, "delete",
        before={"customer_id": contact.customer_id, "name": contact.name},
        after=None,
        summary=f"Deleted contact {contact.name}",
    )
    db.delete(contact)
    db.flush()
    if commit:
        db.commit()


# --- Aliases (upsert per customer) ---------------------------------------------
def list_aliases(db: Session, customer_id: str) -> list[CustomerProductAlias]:
    return (
        db.query(CustomerProductAlias)
        .filter(CustomerProductAlias.customer_id == customer_id)
        .all()
    )


def upsert_alias(
    db: Session,
    customer_id: str,
    *,
    alias: str,
    product_id: str,
    actor: User | None = None,
    commit: bool = False,
) -> CustomerProductAlias:
    if db.get(Product, product_id) is None:
        raise ValueError(f"Product not found: {product_id}")
    existing = (
        db.query(CustomerProductAlias)
        .filter(
            CustomerProductAlias.customer_id == customer_id,
            CustomerProductAlias.alias == alias,
        )
        .first()
    )
    if existing is not None:
        before = {"alias": existing.alias, "product_id": existing.product_id}
        existing.product_id = product_id
        db.flush()
        log_audit(
            db, actor, "CustomerProductAlias", existing.id, "update",
            before=before, after={"alias": alias, "product_id": product_id},
            summary=f"Replaced alias '{alias}' for customer {customer_id}",
        )
        result = existing
    else:
        row = CustomerProductAlias(
            customer_id=customer_id, alias=alias, product_id=product_id,
        )
        db.add(row)
        db.flush()
        log_audit(
            db, actor, "CustomerProductAlias", row.id, "create",
            before=None, after={"alias": alias, "product_id": product_id},
            summary=f"Added alias '{alias}' for customer {customer_id}",
        )
        result = row
    if commit:
        db.commit()
    return result


def get_alias(db: Session, alias_id: str) -> CustomerProductAlias | None:
    return db.get(CustomerProductAlias, alias_id)


def delete_alias(
    db: Session,
    alias: CustomerProductAlias,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> None:
    log_audit(
        db, actor, "CustomerProductAlias", alias.id, "delete",
        before={"customer_id": alias.customer_id, "alias": alias.alias},
        after=None,
        summary=f"Deleted alias '{alias.alias}'",
    )
    db.delete(alias)
    db.flush()
    if commit:
        db.commit()
