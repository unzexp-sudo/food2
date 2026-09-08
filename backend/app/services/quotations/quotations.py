"""Quotation management service: list/get/create, line replace, update, delete.

Mirrors app/services/orders/orders.py but trimmed to the quotation domain
(no confirm/reject lifecycle, no contract-price lock). All state-changing
helpers add audit rows via app.core.audit.log_audit; the caller commits.
"""
from __future__ import annotations

import json

from sqlalchemy import or_

from app.core.audit import log_audit
from app.core.numbers import next_number
from app.models import (
    Customer,
    Product,
    Quotation,
    QuotationLine,
    Unit,
    User,
)


# --- Serializers --------------------------------------------------------------

def _line_out(ln: QuotationLine, *, unit: Unit | None = None) -> dict:
    return {
        "id": ln.id,
        "quotation_id": ln.quotation_id,
        "line_no": ln.line_no,
        "product_id": ln.product_id,
        "product_display": ln.product_display,
        "quantity": ln.quantity,
        "unit_id": ln.unit_id,
        "unit_code": unit.code if unit else (ln.unit.code if ln.unit else None),
        "unit_price": ln.unit_price,
    }


def _quotation_out(db, q: Quotation, *, include_lines: bool = True) -> dict:
    customer = db.get(Customer, q.customer_id)
    lines_out: list[dict] = []
    if include_lines:
        lines = (
            db.query(QuotationLine)
            .filter(QuotationLine.quotation_id == q.id)
            .order_by(QuotationLine.line_no)
            .all()
        )
        unit_ids = [ln.unit_id for ln in lines if ln.unit_id]
        units: dict[str, Unit] = {}
        if unit_ids:
            for u in db.query(Unit).filter(Unit.id.in_(unit_ids)).all():
                units[u.id] = u
        for ln in lines:
            lines_out.append(
                _line_out(ln, unit=units.get(ln.unit_id) if ln.unit_id else None)
            )
    tags: list[str] = []
    if q.tags:
        try:
            tags = json.loads(q.tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    return {
        "id": q.id,
        "code": q.code,
        "customer_id": q.customer_id,
        "customer_name_en": customer.name_en if customer else "",
        "customer_name_zh": customer.name_zh if customer else "",
        "status": q.status,
        "external_name": q.external_name,
        "service_time": q.service_time,
        "pricing_cycle": q.pricing_cycle,
        "tags": tags,
        "description": q.description,
        "line_count": len(lines_out) if include_lines else _count_lines(db, q.id),
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "lines": lines_out,
    }


def _count_lines(db, quotation_id: str) -> int:
    return db.query(QuotationLine).filter(QuotationLine.quotation_id == quotation_id).count()


# --- List / get ---------------------------------------------------------------

def list_quotations(
    db,
    *,
    status: str | None = None,
    customer_id: str | None = None,
    service_time: str | None = None,
    q: str | None = None,
) -> tuple[list[dict], int]:
    q_ = db.query(Quotation)
    if status:
        q_ = q_.filter(Quotation.status == status)
    if customer_id:
        q_ = q_.filter(Quotation.customer_id == customer_id)
    if service_time:
        q_ = q_.filter(Quotation.service_time == service_time)
    if q:
        like = f"%{q}%"
        q_ = q_.join(Customer, Customer.id == Quotation.customer_id).filter(
            or_(
                Quotation.code.ilike(like),
                Customer.name_en.ilike(like),
                Customer.name_zh.ilike(like),
            )
        ).distinct()
    total = q_.count()
    quotations = q_.order_by(Quotation.created_at.desc()).all()
    items = [_quotation_out(db, x, include_lines=False) for x in quotations]
    return items, total


def get_quotation(db, quotation_id: str) -> Quotation | None:
    return db.get(Quotation, quotation_id)


def serialize_quotation(db, q: Quotation) -> dict:
    return _quotation_out(db, q, include_lines=True)


# --- Create -------------------------------------------------------------------

def _insert_lines(db, q: Quotation, lines: list[dict], *, actor: User | None = None) -> None:
    product_ids = [ln.get("product_id") for ln in lines if ln.get("product_id")]
    unit_ids = [ln.get("unit_id") for ln in lines if ln.get("unit_id")]
    products: dict[str, Product] = {}
    units: dict[str, Unit] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            products[p.id] = p
    if unit_ids:
        for u in db.query(Unit).filter(Unit.id.in_(unit_ids)).all():
            units[u.id] = u
    for idx, ln in enumerate(lines, start=1):
        prod = products.get(ln.get("product_id")) if ln.get("product_id") else None
        unit = units.get(ln.get("unit_id")) if ln.get("unit_id") else None
        product_display = ln.get("product_display")
        if not product_display and prod is not None:
            product_display = f"{prod.name_en} / {prod.name_zh}"
        ql = QuotationLine(
            quotation_id=q.id,
            line_no=idx,
            product_id=ln.get("product_id"),
            product_display=product_display,
            quantity=float(ln["quantity"]),
            unit_id=ln.get("unit_id"),
            unit_price=ln.get("unit_price"),
        )
        db.add(ql)
    db.flush()


def create_quotation(
    db,
    *,
    customer_id: str,
    external_name: str | None = None,
    service_time: str = "default",
    pricing_cycle: str | None = None,
    tags: list[str] | None = None,
    description: str | None = None,
    lines: list[dict],
    actor: User | None = None,
) -> Quotation:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise ValueError(f"Customer not found: {customer_id}")
    if not lines:
        raise ValueError("Quotation must have at least one line")

    code = next_number(db, Quotation, "code", "Q")
    q = Quotation(
        code=code,
        customer_id=customer_id,
        status="active",
        external_name=external_name,
        service_time=service_time,
        pricing_cycle=pricing_cycle,
        tags=json.dumps(tags or []),
        description=description,
        created_by=actor.id if actor else None,
    )
    db.add(q)
    db.flush()
    _insert_lines(db, q, lines, actor=actor)

    log_audit(
        db, actor, "Quotation", q.id, "create",
        before=None,
        after={
            "code": q.code,
            "customer_id": customer_id,
            "status": q.status,
            "service_time": service_time,
            "line_count": len(lines),
        },
        summary=f"Quotation {q.code} created ({len(lines)} lines)",
    )
    return q


# --- Update -------------------------------------------------------------------

def update_quotation(db, q: Quotation, *, fields: dict, actor: User | None = None) -> Quotation:
    before = _quotation_out(db, q, include_lines=False)
    for key in ("status", "external_name", "service_time", "pricing_cycle", "description", "customer_id", "tags"):
        if key not in fields:
            continue
        value = fields[key]
        if value is None:
            continue
        if key == "tags":
            q.tags = json.dumps(value)
        else:
            setattr(q, key, value)
    db.flush()
    after = _quotation_out(db, q, include_lines=False)
    log_audit(
        db, actor, "Quotation", q.id, "update",
        before=before, after=after,
        summary=f"Quotation {q.code} updated",
    )
    return q


def replace_lines(db, q: Quotation, *, lines: list[dict], actor: User | None = None) -> Quotation:
    before = _quotation_out(db, q, include_lines=True)
    db.query(QuotationLine).filter(QuotationLine.quotation_id == q.id).delete(
        synchronize_session=False
    )
    db.flush()
    _insert_lines(db, q, lines, actor=actor)
    after = _quotation_out(db, q, include_lines=True)
    log_audit(
        db, actor, "Quotation", q.id, "update_lines",
        before=before, after=after,
        summary=f"Quotation {q.code} lines replaced ({len(lines)} lines)",
    )
    return q


def delete_quotation(db, q: Quotation, actor: User | None = None) -> None:
    before = _quotation_out(db, q, include_lines=False)
    db.query(QuotationLine).filter(QuotationLine.quotation_id == q.id).delete(
        synchronize_session=False
    )
    db.delete(q)
    db.flush()
    log_audit(
        db, actor, "Quotation", q.id, "delete",
        before=before, after=None,
        summary=f"Quotation {q.code} deleted",
    )
