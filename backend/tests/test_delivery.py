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
    # Picking the last line now creates the delivery itself
    # (`picklists.pick_line` → `generate_deliveries`), so read it back instead of
    # calling `/deliveries/generate`, which is a no-op once the row exists. This
    # assert is deliberate: the whole delivery leg is unreachable without it.
    r = client.get(
        "/api/v1/deliveries", headers=warehouse_headers,
        params={"date": dd.isoformat()},
    )
    assert r.status_code == 200, r.text
    rows = r.json()["items"]
    assert rows, "picking the last line should have created the delivery"
    return {"pick_list": pk, "order_id": order_id, "picked_qty": pick_qty,
            "delivery_date": dd, "delivery_id": rows[0]["id"]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_deliveries_list_empty(client, ops_headers):
    r = client.get("/api/v1/deliveries", headers=ops_headers)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body


def test_picking_last_line_creates_delivery(client, warehouse_headers):
    """The whole delivery leg hangs off this.

    `picklists.pick_line` calls `generate_deliveries` the moment the pick list
    flips to "picked". Before that wiring existed nothing in the product called
    `generate_deliveries` at all, so an order reached `fulfilled` with no
    Delivery row and no way to get one from the UI. This test is the thing that
    fails if that call is ever dropped.
    """
    data = _build_chain_to_picked(client, warehouse_headers)
    r = client.get(
        "/api/v1/deliveries", headers=warehouse_headers,
        params={"date": data["delivery_date"].isoformat()},
    )
    assert r.status_code == 200, r.text
    rows = r.json()["items"]
    assert len(rows) == 1, "exactly one delivery per order for the date"
    d = rows[0]
    assert d["status"] == "scheduled"
    assert d["delivery_number"].startswith("DLV-")
    assert d["route"]  # customer.delivery_zone
    assert d["lines"], "a delivery with no lines is not shippable"
    assert d["driver_id"] is None, "created unassigned; a human assigns the driver"


def test_repicking_does_not_duplicate_the_delivery(client, warehouse_headers):
    """`generate_deliveries` skips orders that already have a row for the date.

    Re-picking (a correction, a second scan) must not fan out into two
    deliveries for the same order.
    """
    data = _build_chain_to_picked(client, warehouse_headers)
    pk = data["pick_list"]
    # Pick the same line again with a different quantity.
    r = client.post(
        f"/api/v1/pick-lists/{pk['id']}/lines/{pk['lines'][0]['id']}/pick",
        headers=warehouse_headers,
        json={"picked_quantity": data["picked_qty"]},
    )
    assert r.status_code in (200, 201), r.text
    r = client.get(
        "/api/v1/deliveries", headers=warehouse_headers,
        params={"date": data["delivery_date"].isoformat()},
    )
    assert r.json()["total"] == 1, "re-picking must not create a second delivery"


def test_delivery_generate_and_assign(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers)
    delivery_id = data["delivery_id"]
    # The row already exists; assert the shape that a driver dispatch needs.
    r = client.get(f"/api/v1/deliveries/{delivery_id}", headers=warehouse_headers)
    assert r.status_code == 200, r.text
    delivery = r.json()
    assert delivery["status"] == "scheduled"
    assert delivery["delivery_number"].startswith("DLV-")
    assert delivery["route"]  # customer.delivery_zone
    assert delivery["lines"]

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
    delivery_id = data["delivery_id"]
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
    delivery_id = data["delivery_id"]
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
    delivery_id = data["delivery_id"]
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
    delivery_id = data["delivery_id"]
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
    delivery_id = data["delivery_id"]
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
    delivery_id = data["delivery_id"]
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
    delivery_id = data["delivery_id"]
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


def test_delivery_generate_is_noop_when_row_exists(client, warehouse_headers, admin_headers):
    """`POST /deliveries/generate` is now a backfill, not the create step.

    Picking creates the row, so by the time an operator presses Generate for a
    date that was already picked there is nothing to create. It must report
    `created_count == 0` rather than duplicate or error — the endpoint is still
    the recovery path for orders stranded before the auto-create existed.
    """
    data = _build_chain_to_picked(client, warehouse_headers)
    for _ in range(2):
        r = client.post(
            "/api/v1/deliveries/generate", headers=admin_headers,
            json={"delivery_date": data["delivery_date"].isoformat()},
        )
        assert r.status_code == 201, r.text
        assert r.json()["created_count"] == 0
        assert r.json()["created"] == []
    # And still exactly one row for the date.
    r = client.get(
        "/api/v1/deliveries", headers=warehouse_headers,
        params={"date": data["delivery_date"].isoformat()},
    )
    assert r.json()["total"] == 1


def test_delivery_generate_backfills_a_missing_row(client, warehouse_headers, admin_headers):
    """The recovery path for orders stranded before the auto-create.

    Simulates the historical failure by deleting the delivery row that picking
    created, leaving the picked pick lines in place, then asking the endpoint to
    generate. This is the exact shape of the stuck production orders.
    """
    data = _build_chain_to_picked(client, warehouse_headers)
    from app.core.database import SessionLocal
    from app.models import Delivery
    with SessionLocal() as db:
        row = db.get(Delivery, data["delivery_id"])
        assert row is not None
        db.delete(row)
        db.commit()
    # Gone.
    r = client.get(
        "/api/v1/deliveries", headers=warehouse_headers,
        params={"date": data["delivery_date"].isoformat()},
    )
    assert r.json()["total"] == 0
    # Generate rebuilds it from the picked pick lines.
    r = client.post(
        "/api/v1/deliveries/generate", headers=admin_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created_count"] == 1, body
    rebuilt = body["created"][0]
    assert rebuilt["status"] == "scheduled"
    assert rebuilt["order_id"] == data["order_id"]
    assert rebuilt["lines"]


def test_deliveries_list_filters(client, warehouse_headers, admin_headers):
    data = _build_chain_to_picked(client, warehouse_headers)
    # Filter by date. (The row was created by the final pick, not by a generate.)
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
