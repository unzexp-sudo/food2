"""Delivery service.

Generation:
  - One Delivery per order whose lines were picked for the date
    (pick_lines with status picked|short grouped by order; order status
    consolidated|fulfilled).
  - delivery_number "DLV" via next_number.
  - route = customer.delivery_zone.
  - status "scheduled".
  - lines = picked quantities per order line (delivery_line.quantity =
    picked_quantity).
  - Idempotent: skip orders already having a delivery for that date.

Assignment:
  - Validate driver_id belongs to a user with role "driver".

Status:
  - picked / out_for_delivery — set picked_at / out_at.

Complete:
  - delivered_quantity per line.
  - status delivered (all full) / partial (some short) / failed (all zero).
  - Create ProofOfDelivery (photo saved via settings.files_path).
  - delivered_at = now.
  - Audit-log.
  - Order status → "fulfilled" when delivered or partial.
  - Emit "delivery.completed" (finance auto-invoices).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.config import settings
from app.core.numbers import next_number
from app.models import (
    Customer,
    Delivery,
    DeliveryLine,
    Order,
    OrderLine,
    PickLine,
    PickList,
    Product,
    ProofOfDelivery,
    User,
)


# --- Serializers -------------------------------------------------------------

def _delivery_line_out(db: Session, dl: DeliveryLine) -> dict:
    ol = db.get(OrderLine, dl.order_line_id) if dl.order_line_id else None
    prod = db.get(Product, ol.product_id) if ol and ol.product_id else None
    return {
        "id": dl.id,
        "order_line_id": dl.order_line_id,
        "product_name_en": prod.name_en if prod else "",
        "product_name_zh": prod.name_zh if prod else "",
        "quantity": dl.quantity,
        "delivered_quantity": dl.delivered_quantity,
    }


def _pod_out(pod: ProofOfDelivery | None) -> dict | None:
    if pod is None:
        return None
    return {
        "id": pod.id,
        "photo_url": pod.photo_path,
        "signature_url": pod.signature_path,
        "received_by": pod.received_by,
        "gps_lat": pod.gps_lat,
        "gps_lng": pod.gps_lng,
        "delivered_at": pod.delivered_at.isoformat() if pod.delivered_at else None,
    }


def serialize_delivery(db: Session, d: Delivery) -> dict:
    order = db.get(Order, d.order_id) if d.order_id else None
    customer = db.get(Customer, order.customer_id) if order else None
    driver = db.get(User, d.driver_id) if d.driver_id else None
    return {
        "id": d.id,
        "delivery_number": d.delivery_number,
        "order_id": d.order_id,
        "order_number": order.order_number if order else None,
        "customer_id": order.customer_id if order else "",
        "customer_name_en": customer.name_en if customer else "",
        "customer_name_zh": customer.name_zh if customer else "",
        "route": d.route,
        "driver_id": d.driver_id,
        "driver_name": driver.name if driver else None,
        "status": d.status,
        "scheduled_date": d.scheduled_date.isoformat() if d.scheduled_date else None,
        "picked_at": d.picked_at.isoformat() if d.picked_at else None,
        "out_at": d.out_at.isoformat() if d.out_at else None,
        "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
        "lines": [_delivery_line_out(db, ln) for ln in (d.lines or [])],
        "pod": _pod_out(d.pod),
    }


# --- List / get ---------------------------------------------------------------

def list_deliveries(
    db: Session,
    *,
    date_: date | None = None,
    status: str | None = None,
    driver_id: str | None = None,
) -> tuple[list[dict], int]:
    q = db.query(Delivery)
    if date_:
        q = q.filter(Delivery.scheduled_date == date_)
    if status:
        q = q.filter(Delivery.status == status)
    if driver_id:
        q = q.filter(Delivery.driver_id == driver_id)
    total = q.count()
    rows = q.order_by(Delivery.created_at.desc()).all()
    return [serialize_delivery(db, d) for d in rows], total


def get_delivery(db: Session, delivery_id: str) -> dict | None:
    d = db.get(Delivery, delivery_id)
    if d is None:
        return None
    return serialize_delivery(db, d)


# --- Generate -----------------------------------------------------------------

def generate_deliveries(
    db: Session,
    *,
    delivery_date: date,
    actor: User | None = None,
) -> dict:
    """Create one Delivery per order whose lines were picked for the date.

    Idempotent: skip orders already having a delivery for that date.
    """
    # Pick lines picked|short grouped by order.
    pick_lines = (
        db.query(PickLine)
        .join(PickList, PickList.id == PickLine.pick_list_id)
        .filter(
            PickList.delivery_date == delivery_date,
            PickList.status.in_(("picked",)),
            PickLine.status.in_(("picked", "short")),
        )
        .all()
    )
    order_ids: list[str] = []
    seen: set[str] = set()
    for pl in pick_lines:
        ol = db.get(OrderLine, pl.order_line_id) if pl.order_line_id else None
        if ol and ol.order_id and ol.order_id not in seen:
            seen.add(ol.order_id)
            order_ids.append(ol.order_id)

    created: list[Delivery] = []
    skipped: list[str] = []
    for order_id in order_ids:
        order = db.get(Order, order_id)
        if order is None or order.status not in ("consolidated", "fulfilled"):
            skipped.append(order_id)
            continue
        # Idempotency: skip if a delivery already exists for this order+date.
        existing = (
            db.query(Delivery)
            .filter(
                Delivery.order_id == order_id,
                Delivery.scheduled_date == delivery_date,
            )
            .first()
        )
        if existing:
            skipped.append(order_id)
            continue
        customer = db.get(Customer, order.customer_id)
        delivery_number = next_number(db, Delivery, "delivery_number", "DLV", on=delivery_date)
        delivery = Delivery(
            delivery_number=delivery_number,
            order_id=order_id,
            route=customer.delivery_zone if customer else None,
            driver_id=None,
            status="scheduled",
            scheduled_date=delivery_date,
            created_by=actor.id if actor else None,
        )
        db.add(delivery)
        db.flush()
        # One delivery line per order line that has a picked quantity.
        ol_ids = [pl.order_line_id for pl in pick_lines]
        order_ols = [
            ol for ol in db.query(OrderLine).filter(OrderLine.order_id == order_id).all()
            if ol.id in ol_ids
        ]
        for ol in order_ols:
            # Sum picked quantity across pick lines for this order line.
            picked = sum(
                (pl.picked_quantity or 0)
                for pl in pick_lines
                if pl.order_line_id == ol.id
            )
            dl = DeliveryLine(
                delivery_id=delivery.id,
                order_line_id=ol.id,
                quantity=float(picked),
                delivered_quantity=0.0,
            )
            db.add(dl)
        created.append(delivery)
        log_audit(
            db, actor, "Delivery", delivery.id, "generate",
            before=None,
            after={
                "delivery_number": delivery.delivery_number,
                "order_id": order_id,
                "scheduled_date": delivery_date.isoformat(),
            },
            summary=f"Delivery {delivery.delivery_number} generated for order {order.order_number}",
        )
    db.flush()
    return {
        "created": [serialize_delivery(db, d) for d in created],
        "created_count": len(created),
        "skipped_order_ids": skipped,
    }


# --- Assign -------------------------------------------------------------------

def assign_driver(
    db: Session,
    *,
    delivery: Delivery,
    driver_id: str,
    actor: User | None = None,
) -> Delivery:
    driver = db.get(User, driver_id)
    if driver is None:
        raise ValueError("Driver not found")
    if driver.role != "driver":
        raise ValueError("Assigned user is not a driver")
    before = {"driver_id": delivery.driver_id}
    delivery.driver_id = driver_id
    db.flush()
    log_audit(
        db, actor, "Delivery", delivery.id, "assign_driver",
        before=before, after={"driver_id": driver_id},
        summary=f"Driver {driver.name} assigned to delivery {delivery.delivery_number}",
    )
    return delivery


# --- Status -------------------------------------------------------------------

def set_status(
    db: Session,
    *,
    delivery: Delivery,
    status: str,
    actor: User | None = None,
) -> Delivery:
    if status not in ("picked", "out_for_delivery"):
        raise ValueError("Status must be 'picked' or 'out_for_delivery'")
    before = {"status": delivery.status}
    delivery.status = status
    now = datetime.now(timezone.utc)
    if status == "picked":
        delivery.picked_at = now
    elif status == "out_for_delivery":
        delivery.out_at = now
    db.flush()
    log_audit(
        db, actor, "Delivery", delivery.id, "set_status",
        before=before, after={"status": delivery.status},
        summary=f"Delivery {delivery.delivery_number} → {status}",
    )
    return delivery


# --- Complete -----------------------------------------------------------------

def complete_delivery(
    db: Session,
    *,
    delivery: Delivery,
    line_qty: list[dict],
    received_by: str | None = None,
    gps_lat: float | None = None,
    gps_lng: float | None = None,
    photo_bytes: bytes | None = None,
    photo_ext: str | None = None,
    actor: User | None = None,
) -> Delivery:
    """Mark the delivery complete with per-line delivered quantities and POD."""
    if delivery.status not in ("picked", "out_for_delivery"):
        raise ValueError(
            f"Cannot complete delivery in status '{delivery.status}' "
            "(only picked|out_for_delivery)"
        )
    before = {"status": delivery.status, "delivered_at": delivery.delivered_at}

    # Apply delivered quantities.
    line_map: dict[str, DeliveryLine] = {ln.id: ln for ln in delivery.lines}
    for entry in line_qty:
        dl = line_map.get(entry["delivery_line_id"])
        if dl is None:
            raise ValueError(
                f"delivery_line_id {entry.get('delivery_line_id')} not on this delivery"
            )
        dl.delivered_quantity = float(entry["delivered_quantity"])
    db.flush()

    # Determine outcome status.
    total_planned = sum((ln.quantity or 0) for ln in delivery.lines)
    total_delivered = sum((ln.delivered_quantity or 0) for ln in delivery.lines)
    if total_delivered <= 1e-9:
        outcome = "failed"
    elif total_delivered + 1e-9 >= total_planned:
        outcome = "delivered"
    else:
        outcome = "partial"

    delivery.status = outcome
    delivery.delivered_at = datetime.now(timezone.utc)
    db.flush()

    # POD record.
    photo_path: str | None = None
    if photo_bytes is not None:
        ext = (photo_ext or "jpg").lstrip(".").lower()
        path = settings.files_path("pod", f"{delivery.id}.{ext}")
        path.write_bytes(photo_bytes)
        photo_path = str(path)
    pod = ProofOfDelivery(
        delivery_id=delivery.id,
        photo_path=photo_path,
        signature_path=None,
        received_by=received_by,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        delivered_at=delivery.delivered_at,
    )
    db.add(pod)
    db.flush()

    # Order status → fulfilled when delivered or partial.
    order = db.get(Order, delivery.order_id) if delivery.order_id else None
    order_before = None
    if order is not None:
        order_before = {"status": order.status}
        if outcome in ("delivered", "partial"):
            note = f"delivery {outcome}" if outcome == "partial" else "delivered"
            order.status = "fulfilled"
            if order.notes:
                order.notes = f"{order.notes}\n[{note}]"
            else:
                order.notes = f"[{note}]"
    db.flush()

    after = {
        "status": delivery.status,
        "delivered_at": delivery.delivered_at.isoformat() if delivery.delivered_at else None,
        "total_delivered": total_delivered,
        "total_planned": total_planned,
    }
    log_audit(
        db, actor, "Delivery", delivery.id, "complete",
        before=before, after=after,
        summary=(
            f"Delivery {delivery.delivery_number} completed as '{outcome}' "
            f"({total_delivered}/{total_planned})"
        ),
    )
    if order is not None:
        log_audit(
            db, actor, "Order", order.id, "fulfill",
            before=order_before, after={"status": order.status},
            summary=f"Order {order.order_number} → {order.status} (delivery {delivery.delivery_number})",
        )
    return delivery
