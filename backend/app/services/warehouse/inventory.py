"""Inventory ledger service.

Listing inventory movements and recording loss/shrinkage.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.models import InventoryMovement, Product, User


def _movement_out(db: Session, mv: InventoryMovement) -> dict:
    prod = db.get(Product, mv.product_id) if mv.product_id else None
    return {
        "id": mv.id,
        "product_id": mv.product_id,
        "product_name_en": prod.name_en if prod else None,
        "product_name_zh": prod.name_zh if prod else None,
        "quantity_delta": mv.quantity_delta,
        "ref_type": mv.ref_type,
        "ref_id": mv.ref_id,
        "note": mv.note,
        "created_at": mv.created_at.isoformat() if mv.created_at else None,
    }


def list_movements(
    db: Session,
    *,
    product_id: str | None = None,
) -> tuple[list[dict], int]:
    q = db.query(InventoryMovement)
    if product_id:
        q = q.filter(InventoryMovement.product_id == product_id)
    total = q.count()
    # Newest first. `created_at` is the chronological key; `id` only breaks
    # ties so paging is stable when several movements share a timestamp.
    rows = q.order_by(
        InventoryMovement.created_at.desc(), InventoryMovement.id.desc()
    ).all()
    return [_movement_out(db, m) for m in rows], total


def record_loss(
    db: Session,
    *,
    product_id: str,
    quantity: float,
    reason: str,
    actor: User | None = None,
) -> InventoryMovement:
    mv = InventoryMovement(
        product_id=product_id,
        quantity_delta=-abs(float(quantity)),
        ref_type="loss",
        ref_id=None,
        note=reason,
    )
    db.add(mv)
    db.flush()
    log_audit(
        db, actor, "InventoryMovement", mv.id, "loss",
        before=None,
        after={"product_id": product_id, "quantity": -abs(float(quantity)), "reason": reason},
        summary=f"Inventory loss: {quantity} of product {product_id} ({reason})",
    )
    return mv
