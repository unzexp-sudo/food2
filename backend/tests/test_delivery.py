"""Delivery module tests: generate, assign, status, complete (delivered/partial/failed),
POD storage, and the delivery.completed event emission.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.conftest import client, admin_headers, warehouse_headers, ops_headers


DELIVERY_DATE = date(2026, 9, 8)

# Unique delivery dates per test (shared session-scoped DB).
_date_counter = {"n": 100}


def _unique_date() -> date:
    _date_counter["n"] += 1
    return date(2027, 1, 1) + timedelta(days=_date_counter["n"])


# ---------------------------------------------------------------------------
# Track delivery.completed events.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _capture_delivery_completed(monkeypatch):
    """Register a test handler that records delivery.completed emissions."""
    from app.core import events

    received: list[dict] = []

    def _handler(*, db=None, delivery=None, **kw):
        received.append({"delivery_id": getattr(delivery, "id", None)})

    # Register on the live registry so the endpoint's emit() reaches it.
    events._handlers.setdefault("delivery.completed", []).append(_handler)
    try:
        yield {"received": received}
    finally:
        # Remove our handler so it doesn't leak across tests.
        lst = events._handlers.get("delivery.completed", [])
        if _handler in lst:
            lst.remove(_handler)


# ---------------------------------------------------------------------------
# Helpers — build the full chain through the API + DB.
# ---------------------------------------------------------------------------

def _build_chain_to_picked(client, warehouse_headers, *, planned_qty=50.0, pick_full=True):
    """Create order → consolidate → PO → inbound → pick list → pick lines.

    Returns the pick list dict and the PO/order ids.
    """
    from app.core.database import SessionLocal
    from app.models import (
        ConsolidationBatch, ConsolidationBatchLine, Customer, Order, OrderLine,
        Product, PurchaseOrder, PurchaseOrderLine, Unit, User, Wholesaler,
    )

    dd = _unique_date()
    with SessionLocal() as db:
        potato = db.query(Product).filter(Product.sku == "VG001").one()
        jin = db.query(Unit).filter(Unit.code == "jin").one()
        customer = db.query(Customer).filter(Customer.code == "C001").one()
        wholesaler = db.query(Wholesaler).first()
        ops = db.query(User).filter(User.email == "ops@erp.local").one()

        stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
        order = Order(
            order_number=f"ORD-DLV-{stamp}", customer_id=customer.id,
            status="confirmed", delivery_date=dd,
            source_type="manual", overall_confidence=1.0,
            confirmed_by=ops.id, confirmed_at=datetime.now(timezone.utc),
            created_by=ops.id,
        )
        db.add(order); db.flush()
        ol = OrderLine(
            order_id=order.id, line_no=1, raw_text="土豆50斤",
            product_id=potato.id, product_display="Potato / 土豆",
            quantity=planned_qty, unit_id=jin.id, unit_price=2.2,
            confidence=1.0, match_method="manual",
        )
        db.add(ol); db.flush()

        batch = ConsolidationBatch(
            batch_number=f"CON-DLV-{stamp}", delivery_date=dd,
            cutoff_at=datetime.now(timezone.utc), status="open", created_by=ops.id,
        )
        db.add(batch); db.flush()
        po = PurchaseOrder(
            po_number=f"PO-DLV-{stamp}", wholesaler_id=wholesaler.id,
            category_id=potato.category_id, batch_id=batch.id,
            status="sent", total_amount=0.0,
            sent_at=datetime.now(timezone.utc), created_by=ops.id,
        )
        db.add(po); db.flush()
        pol = PurchaseOrderLine(
            po_id=po.id, product_id=potato.id,
            quantity_ordered=planned_qty, unit_id=jin.id,
            cost_price=1.8, quantity_received=0.0,
        )
        db.add(pol); db.flush()
        db.add(ConsolidationBatchLine(
            batch_id=batch.id, order_line_id=ol.id,
            purchase_order_line_id=pol.id,
        ))
        order.status = "consolidated"
        db.flush(); db.commit()
        order_id = order.id
        po_id = po.id
        pol_id = pol.id

    # Receive fully.
    client.post(
        "/api/v1/inbound-receipts", headers=warehouse_headers,
        json={"po_id": po_id, "lines": [
            {"po_line_id": pol_id, "quantity_received": planned_qty},
        ]},
    )
    # Generate pick list.
    r = client.post(
        "/api/v1/pick-lists/generate", headers=warehouse_headers,
        json={"delivery_date": dd.isoformat()},
    )
    assert r.status_code == 201, r.text
    pk = r.json()["pick_list"]
    # Pick the line.
    pick_qty = pk["lines"][0]["quantity"] if pick_full else max(0.0, pk["lines"][0]["quantity"] - 10)
    client.post(
        f"/api/v1/pick-lists/{pk['id']}/lines/{pk['lines'][0]['id']}/pick",
        headers=warehouse_headers,
        json={"picked_quantity": pick_qty},
    )
    return {"pick_list": pk, "order_id": order_id, "picked_qty": pick_qty,
            "delivery_date": dd}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_deliveries_list_empty(client, ops_headers):
    r = client.get("/api/v1/deliveries", headers=ops_headers)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body


def test_delivery_generate_and_assign(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers)
    # Generate deliveries for the date.
    r = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created_count"] >= 1
    delivery = body["created"][0]
    assert delivery["status"] == "scheduled"
    assert delivery["delivery_number"].startswith("DLV-")
    assert delivery["route"]  # customer.delivery_zone
    assert delivery["lines"]
    delivery_id = delivery["id"]

    # Assign a driver (seeded driver@erp.local).
    from app.core.database import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        driver = db.query(User).filter(User.role == "driver").first()
        driver_id = driver.id
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/assign", headers=admin_headers,
        json={"driver_id": driver_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["driver_id"] == driver_id


def test_delivery_assign_non_driver_404(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers)
    r = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    )
    delivery_id = r.json()["created"][0]["id"]
    # Assign a non-driver user (ops).
    from app.core.database import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        ops = db.query(User).filter(User.role == "ops").first()
        ops_id = ops.id
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/assign", headers=admin_headers,
        json={"driver_id": ops_id},
    )
    assert r.status_code == 404


def test_delivery_status_picked_and_out(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers)
    delivery_id = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    ).json()["created"][0]["id"]
    # Move to picked.
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
        json={"status": "picked"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "picked"
    assert r.json()["picked_at"]
    # Then out for delivery.
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
        json={"status": "out_for_delivery"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "out_for_delivery"
    assert r.json()["out_at"]


def test_delivery_complete_delivered(client, warehouse_headers, admin_headers, _capture_delivery_completed):
    data = _build_chain_to_picked(client, warehouse_headers)
    delivery_id = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    ).json()["created"][0]["id"]
    # Move to picked then out for delivery.
    client.post(f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
                json={"status": "picked"})
    client.post(f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
                json={"status": "out_for_delivery"})
    # Fetch lines.
    detail = client.get(f"/api/v1/deliveries/{delivery_id}", headers=warehouse_headers).json()
    lines = detail["lines"]
    # Fully deliver each line.
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/complete", headers=warehouse_headers,
        json={
            "lines": [
                {"delivery_line_id": ln["id"], "delivered_quantity": ln["quantity"]}
                for ln in lines
            ],
            "received_by": "Ms. Chen",
            "gps_lat": 23.0,
            "gps_lng": 113.0,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "delivered"
    assert body["delivered_at"]
    assert body["pod"]["received_by"] == "Ms. Chen"
    assert body["pod"]["gps_lat"] == 23.0
    # Order is fulfilled, then auto-invoiced by the finance handler on delivery.completed.
    from app.core.database import SessionLocal
    from app.models import Order
    with SessionLocal() as db:
        order = db.get(Order, data["order_id"])
        assert order.status in ("fulfilled", "invoiced")
    # The delivery.completed event should have fired.
    assert any(rec["delivery_id"] == delivery_id
               for rec in _capture_delivery_completed["received"])


def test_delivery_complete_partial(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers, planned_qty=50.0, pick_full=True)
    delivery_id = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    ).json()["created"][0]["id"]
    client.post(f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
                json={"status": "picked"})
    client.post(f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
                json={"status": "out_for_delivery"})
    detail = client.get(f"/api/v1/deliveries/{delivery_id}", headers=warehouse_headers).json()
    lines = detail["lines"]
    # Deliver half the planned quantity (short).
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/complete", headers=warehouse_headers,
        json={
            "lines": [
                {"delivery_line_id": ln["id"], "delivered_quantity": ln["quantity"] / 2}
                for ln in lines
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "partial"
    # Order is fulfilled (partial keeps fulfilled per contract), then auto-invoiced.
    from app.core.database import SessionLocal
    from app.models import Order
    with SessionLocal() as db:
        order = db.get(Order, data["order_id"])
        assert order.status in ("fulfilled", "invoiced")


def test_delivery_complete_failed_all_zero(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers)
    delivery_id = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    ).json()["created"][0]["id"]
    client.post(f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
                json={"status": "picked"})
    client.post(f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
                json={"status": "out_for_delivery"})
    detail = client.get(f"/api/v1/deliveries/{delivery_id}", headers=warehouse_headers).json()
    lines = detail["lines"]
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/complete", headers=warehouse_headers,
        json={"lines": [
            {"delivery_line_id": ln["id"], "delivered_quantity": 0}
            for ln in lines
        ]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"


def test_delivery_complete_409_when_scheduled(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers)
    delivery_id = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    ).json()["created"][0]["id"]
    # No status change — still scheduled.
    detail = client.get(f"/api/v1/deliveries/{delivery_id}", headers=warehouse_headers).json()
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/complete", headers=warehouse_headers,
        json={"lines": [
            {"delivery_line_id": ln["id"], "delivered_quantity": ln["quantity"]}
            for ln in detail["lines"]
        ]},
    )
    assert r.status_code == 409


def test_delivery_complete_with_multipart_photo(client, warehouse_headers, admin_headers):
    import json
    data = _build_chain_to_picked(client, warehouse_headers)
    delivery_id = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    ).json()["created"][0]["id"]
    client.post(f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
                json={"status": "picked"})
    client.post(f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
                json={"status": "out_for_delivery"})
    detail = client.get(f"/api/v1/deliveries/{delivery_id}", headers=warehouse_headers).json()
    lines = detail["lines"]
    # Submit via multipart with a photo.
    lines_json = json.dumps([
        {"delivery_line_id": ln["id"], "delivered_quantity": ln["quantity"]}
        for ln in lines
    ])
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/complete",
        headers=warehouse_headers,
        data={
            "lines": lines_json,
            "received_by": "Mr. Li",
            "gps_lat": "23.1",
            "gps_lng": "113.1",
        },
        files={"photo": ("pod.png", io.BytesIO(b"\x89PNG\r\n\x1a\n test png"), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "delivered"
    assert body["pod"]["photo_url"]
    # Fetch the photo back.
    r2 = client.get(f"/api/v1/deliveries/{delivery_id}/photo", headers=warehouse_headers)
    assert r2.status_code == 200
    assert r2.content


def test_delivery_generate_idempotent(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers)
    # First generation.
    r1 = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    )
    assert r1.status_code == 201
    first_count = r1.json()["created_count"]
    # Second generation should skip the order.
    r2 = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    )
    assert r2.status_code == 201
    assert r2.json()["created_count"] == 0
    assert first_count >= 1


def test_deliveries_list_filters(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers)
    client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    )
    # Filter by date.
    r = client.get("/api/v1/deliveries", headers=warehouse_headers,
                   params={"date": data["delivery_date"].isoformat()})
    assert r.status_code == 200
    assert r.json()["total"] >= 1
    # Filter by status=scheduled.
    r = client.get("/api/v1/deliveries", headers=warehouse_headers,
                   params={"status": "scheduled"})
    assert r.status_code == 200
    assert all(it["status"] == "scheduled" for it in r.json()["items"])


def test_delivery_get_not_found(client, warehouse_headers):
    r = client.get("/api/v1/deliveries/nope", headers=warehouse_headers)
    assert r.status_code == 404


def test_deliveries_require_auth(client):
    r = client.get("/api/v1/deliveries")
    assert r.status_code == 401
