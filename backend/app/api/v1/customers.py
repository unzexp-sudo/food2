"""CUSTOMERS MODULE — owner: master data agent.

Endpoints (see docs/AGENT_CONTRACTS.md §5):
  GET/POST /customers, GET/PATCH/DELETE /customers/{id}
  GET /customers/{id}/contacts, POST /customers/{id}/contacts, DELETE /customers/contacts/{contact_id}
  GET /customers/{id}/aliases, POST /customers/{id}/aliases, DELETE /customers/aliases/{alias_id}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.schemas.customers import (
    AliasCreate,
    AliasOut,
    ContactCreate,
    ContactOut,
    CustomerCreate,
    CustomerOut,
    CustomerUpdate,
)
from app.services.masterdata.customers import (
    create_contact,
    create_customer,
    delete_alias,
    delete_contact,
    get_alias,
    get_contact,
    get_customer,
    list_aliases,
    list_contacts,
    list_customers,
    soft_delete_customer,
    update_customer,
    upsert_alias,
)

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


def _customer_out(c) -> dict:
    return CustomerOut.model_validate(
        {
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
    ).model_dump(mode="json")


def _contact_out(c) -> dict:
    return ContactOut.model_validate(c).model_dump(mode="json")


def _alias_out(a) -> dict:
    return AliasOut.model_validate(a).model_dump(mode="json")


# --- List + create ------------------------------------------------------------
@router.get("", response_model=None)
def list_customers_endpoint(
    q: str | None = Query(default=None, description="Search code/name_en/name_zh"),
    type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    page, page_size = clamp_page(page, page_size)
    customers, total = list_customers(db, q=q, type=type, status=status)
    # Paginate in Python (small dataset); offset/limit also possible.
    start = (page - 1) * page_size
    items = [_customer_out(c) for c in customers[start : start + page_size]]
    return page_response(items, total, page, page_size)


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_customer_endpoint(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    try:
        customer = create_customer(
            db,
            code=payload.code,
            name_en=payload.name_en,
            name_zh=payload.name_zh,
            type=payload.type,
            contact_name=payload.contact_name,
            contact_phone=payload.contact_phone,
            address=payload.address,
            delivery_zone=payload.delivery_zone,
            notes=payload.notes,
            status=payload.status,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _customer_out(customer)


# --- Single customer CRUD -----------------------------------------------------
@router.get("/{customer_id}", response_model=None)
def get_customer_endpoint(
    customer_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    customer = get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return _customer_out(customer)


@router.patch("/{customer_id}", response_model=None)
def update_customer_endpoint(
    customer_id: str,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    customer = get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    try:
        update_customer(
            db,
            customer,
            code=payload.code,
            name_en=payload.name_en,
            name_zh=payload.name_zh,
            type=payload.type,
            contact_name=payload.contact_name,
            contact_phone=payload.contact_phone,
            address=payload.address,
            delivery_zone=payload.delivery_zone,
            notes=payload.notes,
            status=payload.status,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _customer_out(customer)


@router.delete("/{customer_id}", response_model=None)
def delete_customer_endpoint(
    customer_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    """Soft delete — status=inactive."""
    customer = get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    soft_delete_customer(db, customer, actor=actor)
    db.commit()
    return _customer_out(customer)


# --- Contacts -----------------------------------------------------------------
@router.get("/{customer_id}/contacts", response_model=None)
def list_contacts_endpoint(
    customer_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    customer = get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return [_contact_out(c) for c in list_contacts(db, customer_id)]


@router.post("/{customer_id}/contacts", response_model=None, status_code=status.HTTP_201_CREATED)
def create_contact_endpoint(
    customer_id: str,
    payload: ContactCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    customer = get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    contact = create_contact(
        db, customer_id,
        name=payload.name, phone=payload.phone, role=payload.role,
        actor=actor,
    )
    db.commit()
    return _contact_out(contact)


@router.delete("/contacts/{contact_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_contact_endpoint(
    contact_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    contact = get_contact(db, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")
    delete_contact(db, contact, actor=actor)
    db.commit()
    return None


# --- Aliases ------------------------------------------------------------------
@router.get("/{customer_id}/aliases", response_model=None)
def list_aliases_endpoint(
    customer_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance")),
):
    customer = get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return [_alias_out(a) for a in list_aliases(db, customer_id)]


@router.post("/{customer_id}/aliases", response_model=None, status_code=status.HTTP_201_CREATED)
def create_alias_endpoint(
    customer_id: str,
    payload: AliasCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    """Upsert: same alias text for same customer replaces the mapping."""
    customer = get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    try:
        alias = upsert_alias(
            db, customer_id,
            alias=payload.alias, product_id=payload.product_id,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _alias_out(alias)


@router.delete("/aliases/{alias_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_alias_endpoint(
    alias_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops")),
):
    alias = get_alias(db, alias_id)
    if alias is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alias not found")
    delete_alias(db, alias, actor=actor)
    db.commit()
    return None
