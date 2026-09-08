"""Contract service functions: contract prices, standing order templates."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.events import emit
from app.core.numbers import next_number
from app.models import (
    ContractPrice,
    Customer,
    Order,
    OrderLine,
    Product,
    StandingOrderTemplate,
    StandingOrderTemplateLine,
    Unit,
    User,
)


# --- Contract Prices ----------------------------------------------------------
def _price_to_dict(p: ContractPrice) -> dict[str, Any]:
    return {
        "id": p.id,
        "customer_id": p.customer_id,
        "product_id": p.product_id,
        "unit_id": p.unit_id,
        "price": p.price,
        "valid_from": p.valid_from.isoformat() if p.valid_from else None,
        "valid_until": p.valid_until.isoformat() if p.valid_until else None,
    }


def list_contract_prices(
    db: Session,
    *,
    customer_id: str | None = None,
    product_id: str | None = None,
) -> tuple[list[ContractPrice], int]:
    query = db.query(ContractPrice)
    if customer_id:
        query = query.filter(ContractPrice.customer_id == customer_id)
    if product_id:
        query = query.filter(ContractPrice.product_id == product_id)
    total = query.count()
    return query.order_by(ContractPrice.valid_from.desc()).all(), total


def create_contract_price(
    db: Session,
    *,
    customer_id: str,
    product_id: str,
    unit_id: str,
    price: float,
    valid_from: date,
    valid_until: date | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> ContractPrice:
    if db.get(Customer, customer_id) is None:
        raise ValueError(f"Customer not found: {customer_id}")
    if db.get(Product, product_id) is None:
        raise ValueError(f"Product not found: {product_id}")
    if db.get(Unit, unit_id) is None:
        raise ValueError(f"Unit not found: {unit_id}")
    # Deactivate existing active price for same (customer, product, unit).
    existing = (
        db.query(ContractPrice)
        .filter(
            ContractPrice.customer_id == customer_id,
            ContractPrice.product_id == product_id,
            ContractPrice.unit_id == unit_id,
            ContractPrice.valid_until.is_(None),
        )
        .first()
    )
    if existing is not None:
        existing.valid_until = valid_from
        db.flush()
    row = ContractPrice(
        customer_id=customer_id,
        product_id=product_id,
        unit_id=unit_id,
        price=price,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    db.add(row)
    db.flush()
    log_audit(
        db, actor, "ContractPrice", row.id, "create",
        before=None, after=_price_to_dict(row),
        summary=f"Created contract price for customer={customer_id} product={product_id}",
    )
    if commit:
        db.commit()
    return row


def get_contract_price(db: Session, price_id: str) -> ContractPrice | None:
    return db.get(ContractPrice, price_id)


def delete_contract_price(
    db: Session,
    price: ContractPrice,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> None:
    log_audit(
        db, actor, "ContractPrice", price.id, "delete",
        before=_price_to_dict(price), after=None,
        summary=f"Deleted contract price {price.id}",
    )
    db.delete(price)
    db.flush()
    if commit:
        db.commit()


# --- Standing Order Templates -------------------------------------------------
def _template_line_to_dict(line: StandingOrderTemplateLine) -> dict[str, Any]:
    return {
        "id": line.id,
        "template_id": line.template_id,
        "product_id": line.product_id,
        "quantity": line.quantity,
        "unit_id": line.unit_id,
    }


def _template_to_dict(t: StandingOrderTemplate) -> dict[str, Any]:
    return {
        "id": t.id,
        "customer_id": t.customer_id,
        "name": t.name,
        "delivery_days": t.delivery_days,
        "is_active": t.is_active,
        "lines": [_template_line_to_dict(line) for line in (t.lines or [])],
    }


def list_templates(
    db: Session, *, customer_id: str | None = None
) -> tuple[list[StandingOrderTemplate], int]:
    query = db.query(StandingOrderTemplate)
    if customer_id:
        query = query.filter(StandingOrderTemplate.customer_id == customer_id)
    total = query.count()
    return query.order_by(StandingOrderTemplate.created_at).all(), total


def get_template(db: Session, template_id: str) -> StandingOrderTemplate | None:
    return db.get(StandingOrderTemplate, template_id)


def create_template(
    db: Session,
    *,
    customer_id: str,
    name: str,
    delivery_days: list[str],
    is_active: bool = True,
    lines: list[dict[str, Any]],
    actor: User | None = None,
    commit: bool = False,
) -> StandingOrderTemplate:
    if db.get(Customer, customer_id) is None:
        raise ValueError(f"Customer not found: {customer_id}")
    # Validate line references.
    for ln in lines:
        if db.get(Product, ln["product_id"]) is None:
            raise ValueError(f"Product not found: {ln['product_id']}")
        if db.get(Unit, ln["unit_id"]) is None:
            raise ValueError(f"Unit not found: {ln['unit_id']}")
    template = StandingOrderTemplate(
        customer_id=customer_id,
        name=name,
        delivery_days=delivery_days,
        is_active=is_active,
    )
    db.add(template)
    db.flush()
    for ln in lines:
        db.add(StandingOrderTemplateLine(
            template_id=template.id,
            product_id=ln["product_id"],
            quantity=ln["quantity"],
            unit_id=ln["unit_id"],
        ))
    db.flush()
    log_audit(
        db, actor, "StandingOrderTemplate", template.id, "create",
        before=None, after=_template_to_dict(template),
        summary=f"Created standing order template '{template.name}'",
    )
    if commit:
        db.commit()
    return template


def update_template(
    db: Session,
    template: StandingOrderTemplate,
    *,
    customer_id: str | None = None,
    name: str | None = None,
    delivery_days: list[str] | None = None,
    is_active: bool | None = None,
    lines: list[dict[str, Any]] | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> StandingOrderTemplate:
    before = _template_to_dict(template)
    if customer_id is not None:
        if db.get(Customer, customer_id) is None:
            raise ValueError(f"Customer not found: {customer_id}")
        template.customer_id = customer_id
    if name is not None:
        template.name = name
    if delivery_days is not None:
        template.delivery_days = delivery_days
    if is_active is not None:
        template.is_active = is_active
    if lines is not None:
        # Replace all lines: delete old, then add new. Expire the
        # relationship so it re-queries after flush.
        old_lines = list(template.lines or [])
        for old_line in old_lines:
            db.delete(old_line)
        db.flush()
        # Force SQLAlchemy to re-query the relationship next access.
        db.expire(template)
        db.flush()
        for ln in lines:
            if db.get(Product, ln["product_id"]) is None:
                raise ValueError(f"Product not found: {ln['product_id']}")
            if db.get(Unit, ln["unit_id"]) is None:
                raise ValueError(f"Unit not found: {ln['unit_id']}")
            db.add(StandingOrderTemplateLine(
                template_id=template.id,
                product_id=ln["product_id"],
                quantity=ln["quantity"],
                unit_id=ln["unit_id"],
            ))
    db.flush()
    log_audit(
        db, actor, "StandingOrderTemplate", template.id, "update",
        before=before, after=_template_to_dict(template),
        summary=f"Updated standing order template '{template.name}'",
    )
    if commit:
        db.commit()
    return template


def delete_template(
    db: Session,
    template: StandingOrderTemplate,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> StandingOrderTemplate:
    """Soft delete — is_active=False."""
    before = _template_to_dict(template)
    template.is_active = False
    db.flush()
    log_audit(
        db, actor, "StandingOrderTemplate", template.id, "delete",
        before=before, after=_template_to_dict(template),
        summary=f"Deactivated standing order template '{template.name}'",
    )
    if commit:
        db.commit()
    return template


# --- Standing order → create draft order -------------------------------------
def create_order_from_template(
    db: Session,
    template: StandingOrderTemplate,
    *,
    delivery_date: date | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> Order:
    """Create a draft Order from a standing order template.

    - status "draft", source_type "standing", standing_template_id set
    - one OrderLine per template line
    - product_display = product name (en preferred)
    - unit_price from active contract price if found, else None
    - confidence 1.0, match_method "manual"
    - emits "order.draft_created" after flush so auto-confirm can run
    """
    delivery_date = delivery_date or date.today()
    order_number = next_number(db, Order, "order_number", "ORD", on=delivery_date)
    order = Order(
        order_number=order_number,
        customer_id=template.customer_id,
        status="draft",
        delivery_date=delivery_date,
        source_type="standing",
        standing_template_id=template.id,
        overall_confidence=1.0,
        created_by=actor.id if actor else None,
    )
    db.add(order)
    db.flush()

    for idx, line in enumerate(template.lines or [], start=1):
        product = db.get(Product, line.product_id)
        product_display = product.name_en if product else None
        # Look up active contract price for this customer/product/unit.
        contract = (
            db.query(ContractPrice)
            .filter(
                ContractPrice.customer_id == template.customer_id,
                ContractPrice.product_id == line.product_id,
                ContractPrice.unit_id == line.unit_id,
                ContractPrice.valid_from <= delivery_date,
            )
            .filter(
                (ContractPrice.valid_until.is_(None))
                | (ContractPrice.valid_until >= delivery_date)
            )
            .first()
        )
        unit_price = contract.price if contract else None
        ol = OrderLine(
            order_id=order.id,
            line_no=idx,
            product_id=line.product_id,
            product_display=product_display,
            quantity=line.quantity,
            unit_id=line.unit_id,
            unit_price=unit_price,
            confidence=1.0,
            match_method="manual",
        )
        db.add(ol)

    db.flush()
    log_audit(
        db, actor, "Order", order.id, "create",
        before=None,
        after={"order_number": order.order_number, "status": "draft",
               "source_type": "standing", "standing_template_id": template.id},
        summary=f"Created draft order {order.order_number} from template '{template.name}'",
    )
    if commit:
        db.commit()
    # Emit event so orders module can run auto-confirm evaluation.
    emit("order.draft_created", db=db, order=order)
    return order
