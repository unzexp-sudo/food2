"""Warehouse module tests: inbound receipts, pick lists, inventory.

Test data setup inserts rows directly via SessionLocal (allowed per the
contract note) so we don't depend on the orders/procurement routers' exact
behaviour. We drive the full chain: order → confirm → consolidate → send PO
→ inbound (discrepancy + full) → pick (full/short) → inventory loss.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tests.conftest import client, admin_headers, warehouse_headers, ops_headers


DELIVERY_DATE = date(2026, 9, 8)

# Counter for unique delivery dates per test (avoids cross-test pick-list
# collisions on the shared session-scoped DB).
_date_counter = {"n": 0}


def _unique_date() -> date:
    """Return a unique future date so each test's pick lists are isolated."""
    _date_counter["n"] += 1
    # Start from 2027-01-01 + n days to avoid colliding with seeded data.
    return date(2027, 1, 1) + timedelta(days=_date_counter["n"])


# ---------------------------------------------------------------------------
# Helpers — build a full chain directly in the DB.
# ---------------------------------------------------------------------------

def _seed_chain_for_inbound(db, *, partial: bool = False, delivery_date: date | None = None):
    """Create customer, order (confirmed), consolidation batch + lines,
    purchase order (sent), so the warehouse endpoints can receive against it.

    If partial=True, the first PO line will be ordered for more than we'll
    receive (so the PO lands as partially_received). Otherwise both lines
    are fully received.
    """
    delivery_date = delivery_date or _unique_date()
    from app.models import (
        ConsolidationBatch,
        ConsolidationBatchLine,
        Customer,
        Order,
        OrderLine,
        Product,
        PurchaseOrder,
        PurchaseOrderLine,
        Unit,
        User,
        Wholesaler,
    )

    # Pick a couple of seeded products (Potato VG001, Cabbage VG002).
    potato = db.query(Product).filter(Product.sku == "VG001").one()
    cabbage = db.query(Product).filter(Product.sku == "VG002").one()
    jin = db.query(Unit).filter(Unit.code == "jin").one()
    customer = db.query(Customer).filter(Customer.code == "C001").one()
    wholesaler = db.query(Wholesaler).first()
    ops = db.query(User).filter(User.email == "ops@erp.local").one()

    order_number = f"ORD-TEST-{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
    order = Order(
        order_number=order_number,
        customer_id=customer.id,
        status="confirmed",
        delivery_date=delivery_date,
        source_type="manual",
        overall_confidence=1.0,
        confirmed_by=ops.id,
        confirmed_at=datetime.now(timezone.utc),
        created_by=ops.id,
    )
    db.add(order)
    db.flush()
    ol1 = OrderLine(
        order_id=order.id, line_no=1, raw_text="土豆50斤",
        product_id=potato.id, product_display="Potato / 土豆",
        quantity=50.0, unit_id=jin.id, unit_price=2.2,
        confidence=1.0, match_method="manual",
    )
    ol2 = OrderLine(
        order_id=order.id, line_no=2, raw_text="大白菜30斤",
        product_id=cabbage.id, product_display="Chinese Cabbage / 大白菜",
        quantity=30.0, unit_id=jin.id, unit_price=1.5,
        confidence=1.0, match_method="manual",
    )
    db.add_all([ol1, ol2])
    db.flush()

    batch_number = f"CON-TEST-{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
    batch = ConsolidationBatch(
        batch_number=batch_number,
        delivery_date=delivery_date,
        cutoff_at=datetime.now(timezone.utc),
        status="open",
        created_by=ops.id,
    )
    db.add(batch)
    db.flush()

    po_number = f"PO-TEST-{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
    po = PurchaseOrder(
        po_number=po_number,
        wholesaler_id=wholesaler.id,
        category_id=potato.category_id,
        batch_id=batch.id,
        status="sent",
        total_amount=0.0,
        sent_at=datetime.now(timezone.utc),
        created_by=ops.id,
    )
    db.add(po)
    db.flush()
    # Order 60 of potato (so receiving 50 is a partial / discrepancy) and 30 of cabbage.
    pol1_qty = 60.0 if partial else 50.0
    pol1 = PurchaseOrderLine(
        po_id=po.id, product_id=potato.id,
        quantity_ordered=pol1_qty, unit_id=jin.id,
        cost_price=1.8, quantity_received=0.0,
    )
    pol2 = PurchaseOrderLine(
        po_id=po.id, product_id=cabbage.id,
        quantity_ordered=30.0, unit_id=jin.id,
        cost_price=1.0, quantity_received=0.0,
    )
    db.add_all([pol1, pol2])
    db.flush()

    # Link batch lines to PO lines.
    bl1 = ConsolidationBatchLine(
        batch_id=batch.id, order_line_id=ol1.id,
        purchase_order_line_id=pol1.id,
    )
    bl2 = ConsolidationBatchLine(
        batch_id=batch.id, order_line_id=ol2.id,
        purchase_order_line_id=pol2.id,
    )
    db.add_all([bl1, bl2])
    # Move order to consolidated so pick-list generation picks it up.
    order.status = "consolidated"
    db.flush()
    db.commit()
    return {
        "order": order, "ol1": ol1, "ol2": ol2,
        "batch": batch, "po": po, "pol1": pol1, "pol2": pol2,
        "potato": potato, "cabbage": cabbage,
        "delivery_date": delivery_date,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_list_inbound_receipts_empty(client, warehouse_headers):
    r = client.get("/api/v1/inbound-receipts", headers=warehouse_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 0
    assert isinstance(body["items"], list)


def test_po_received_summary_before_any_receipt(client, admin_headers):
    from app.core.database import SessionLocal
    data = _seed_chain_for_inbound(SessionLocal())
    po_id = data["po"].id
    r = client.get(f"/api/v1/purchase-orders/{po_id}/received", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["po_id"] == po_id
    assert body["status"] == "sent"
    assert body["total_ordered"] == 80.0  # 50 + 30
    assert body["total_received"] == 0.0
    assert len(body["lines"]) == 2
    assert len(body["receipts"]) == 0


def test_inbound_receipt_full_marks_po_received(client, warehouse_headers, admin_headers):
    from app.core.database import SessionLocal
    data = _seed_chain_for_inbound(SessionLocal())
    po_id = data["po"].id
    pol1_id = data["pol1"].id
    pol2_id = data["pol2"].id
    # Fully receive both lines (60 potato, 30 cabbage).
    r = client.post(
        "/api/v1/inbound-receipts",
        headers=warehouse_headers,
        json={
            "po_id": po_id,
            "lines": [
                {"po_line_id": pol1_id, "quantity_received": 50.0},
                {"po_line_id": pol2_id, "quantity_received": 30.0},
            ],
        },
    )
    assert r.status_code == 201, r.text
    receipt = r.json()
    assert receipt["status"] == "posted"
    assert receipt["receipt_number"].startswith("RCP-")
    assert len(receipt["lines"]) == 2
    # PO should now be received.
    summary = client.get(
        f"/api/v1/purchase-orders/{po_id}/received", headers=admin_headers
    ).json()
    assert summary["status"] == "received"
    assert summary["total_received"] == 80.0
    # No discrepancy on either line.
    assert all(ln["discrepancy"] is False for ln in summary["lines"])


def test_inbound_receipt_discrepancy_marks_partially_received(client, warehouse_headers, admin_headers):
    from app.core.database import SessionLocal
    data = _seed_chain_for_inbound(SessionLocal(), partial=True)
    po_id = data["po"].id
    pol1_id = data["pol1"].id  # ordered 60
    pol2_id = data["pol2"].id  # ordered 30
    # Receive 50 of potato (short of 60) and 30 of cabbage (full).
    r = client.post(
        "/api/v1/inbound-receipts",
        headers=warehouse_headers,
        json={
            "po_id": po_id,
            "lines": [
                {"po_line_id": pol1_id, "quantity_received": 50.0, "quantity_damaged": 0},
                {"po_line_id": pol2_id, "quantity_received": 30.0},
            ],
        },
    )
    assert r.status_code == 201, r.text
    summary = client.get(
        f"/api/v1/purchase-orders/{po_id}/received", headers=admin_headers
    ).json()
    # 50 < 60 on pol1 → partially_received.
    assert summary["status"] == "partially_received"
    assert summary["total_received"] == 80.0
    # The potato line should be flagged as a discrepancy.
    potato_line = [ln for ln in summary["lines"] if ln["product_id"] == data["potato"].id][0]
    assert potato_line["discrepancy"] is True
    assert potato_line["quantity_received"] == 50.0


def test_inbound_receipt_409_when_po_not_receivable(client, warehouse_headers):
    from app.core.database import SessionLocal
    from app.models import PurchaseOrder
    data = _seed_chain_for_inbound(SessionLocal())
    po = data["po"]
    # Force the PO into 'received' so receiving again 409s.
    with SessionLocal() as db:
        po_db = db.get(PurchaseOrder, po.id)
        po_db.status = "received"
        db.commit()
    r = client.post(
        "/api/v1/inbound-receipts",
        headers=warehouse_headers,
        json={"po_id": po.id, "lines": [
            {"po_line_id": data["pol1"].id, "quantity_received": 1},
        ]},
    )
    assert r.status_code == 409


def test_pick_list_generation_and_pick_full(client, warehouse_headers, admin_headers):
    from app.core.database import SessionLocal
    data = _seed_chain_for_inbound(SessionLocal())
    po_id = data["po"].id
    pol1_id, pol2_id = data["pol1"].id, data["pol2"].id
    dd = data["delivery_date"]
    # Receive full stock first so allocation is 100%.
    client.post(
        "/api/v1/inbound-receipts",
        headers=warehouse_headers,
        json={"po_id": po_id, "lines": [
            {"po_line_id": pol1_id, "quantity_received": 50.0},
            {"po_line_id": pol2_id, "quantity_received": 30.0},
        ]},
    )
    # Inbound should have triggered pick-list regeneration; but also call generate directly.
    r = client.post(
        "/api/v1/pick-lists/generate",
        headers=warehouse_headers,
        json={"delivery_date": dd.isoformat()},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["line_count"] == 2
    pk = body["pick_list"]
    assert pk["status"] == "open"
    assert len(pk["lines"]) == 2
    # Each planned quantity should equal the order qty (50 and 30).
    qtys = {ln["product_id"]: ln["quantity"] for ln in pk["lines"]}
    assert qtys[data["potato"].id] == 50.0
    assert qtys[data["cabbage"].id] == 30.0
    pick_list_id = pk["id"]

    # Pick both lines fully.
    for ln in pk["lines"]:
        rr = client.post(
            f"/api/v1/pick-lists/{pick_list_id}/lines/{ln['id']}/pick",
            headers=warehouse_headers,
            json={"picked_quantity": ln["quantity"]},
        )
        assert rr.status_code == 200, rr.text
    updated = rr.json()
    assert updated["status"] == "picked"
    assert all(ln["status"] == "picked" for ln in updated["lines"])

    # The order should now be fulfilled.
    from app.core.database import SessionLocal
    from app.models import Order
    with SessionLocal() as db:
        order = db.get(Order, data["order"].id)
        assert order.status == "fulfilled"


def test_pick_list_short_line_marks_short(client, warehouse_headers):
    from app.core.database import SessionLocal
    data = _seed_chain_for_inbound(SessionLocal())
    po_id = data["po"].id
    pol1_id, pol2_id = data["pol1"].id, data["pol2"].id
    dd = data["delivery_date"]
    # Receive full.
    client.post(
        "/api/v1/inbound-receipts",
        headers=warehouse_headers,
        json={"po_id": po_id, "lines": [
            {"po_line_id": pol1_id, "quantity_received": 50.0},
            {"po_line_id": pol2_id, "quantity_received": 30.0},
        ]},
    )
    r = client.post(
        "/api/v1/pick-lists/generate",
        headers=warehouse_headers,
        json={"delivery_date": dd.isoformat()},
    )
    pk = r.json()["pick_list"]
    # Pick the first line short (planned 50, pick 40).
    first = pk["lines"][0]
    rr = client.post(
        f"/api/v1/pick-lists/{pk['id']}/lines/{first['id']}/pick",
        headers=warehouse_headers,
        json={"picked_quantity": 40.0},
    )
    assert rr.status_code == 200
    body = rr.json()
    # The first line should be 'short', and the list 'picking'.
    short_line = [ln for ln in body["lines"] if ln["id"] == first["id"]][0]
    assert short_line["status"] == "short"
    assert body["status"] == "picking"


def test_pick_list_proportional_allocation_when_received_short(client, warehouse_headers):
    """When total received < total ordered for a product, planned qty is
    allocated proportionally by order line quantity.
    """
    from app.core.database import SessionLocal
    from app.models import (
        ConsolidationBatch, ConsolidationBatchLine, Customer, Order, OrderLine,
        Product, PurchaseOrder, PurchaseOrderLine, Unit, User, Wholesaler,
    )
    from datetime import datetime, timezone

    dd = _unique_date()
    with SessionLocal() as db:
        potato = db.query(Product).filter(Product.sku == "VG001").one()
        jin = db.query(Unit).filter(Unit.code == "jin").one()
        customer = db.query(Customer).filter(Customer.code == "C001").one()
        wholesaler = db.query(Wholesaler).first()
        ops = db.query(User).filter(User.email == "ops@erp.local").one()

        # Two orders for the same product on the same date, total 80.
        stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
        o1 = Order(order_number=f"ORD-PA1-{stamp}", customer_id=customer.id,
                   status="confirmed", delivery_date=dd,
                   source_type="manual", overall_confidence=1.0,
                   confirmed_by=ops.id, confirmed_at=datetime.now(timezone.utc),
                   created_by=ops.id)
        o2 = Order(order_number=f"ORD-PA2-{stamp}", customer_id=customer.id,
                   status="confirmed", delivery_date=dd,
                   source_type="manual", overall_confidence=1.0,
                   confirmed_by=ops.id, confirmed_at=datetime.now(timezone.utc),
                   created_by=ops.id)
        db.add_all([o1, o2]); db.flush()
        ol1 = OrderLine(order_id=o1.id, line_no=1, raw_text="土豆50斤",
                        product_id=potato.id, product_display="Potato",
                        quantity=50.0, unit_id=jin.id, confidence=1.0,
                        match_method="manual")
        ol2 = OrderLine(order_id=o2.id, line_no=1, raw_text="土豆30斤",
                        product_id=potato.id, product_display="Potato",
                        quantity=30.0, unit_id=jin.id, confidence=1.0,
                        match_method="manual")
        db.add_all([ol1, ol2]); db.flush()

        batch = ConsolidationBatch(batch_number=f"CON-PA-{stamp}",
                                   delivery_date=dd,
                                   cutoff_at=datetime.now(timezone.utc),
                                   status="open", created_by=ops.id)
        db.add(batch); db.flush()
        po = PurchaseOrder(po_number=f"PO-PA-{stamp}", wholesaler_id=wholesaler.id,
                           category_id=potato.category_id, batch_id=batch.id,
                           status="sent", total_amount=0.0,
                           sent_at=datetime.now(timezone.utc), created_by=ops.id)
        db.add(po); db.flush()
        pol = PurchaseOrderLine(po_id=po.id, product_id=potato.id,
                               quantity_ordered=80.0, unit_id=jin.id,
                               cost_price=1.8, quantity_received=0.0)
        db.add(pol); db.flush()
        db.add_all([
            ConsolidationBatchLine(batch_id=batch.id, order_line_id=ol1.id,
                                   purchase_order_line_id=pol.id),
            ConsolidationBatchLine(batch_id=batch.id, order_line_id=ol2.id,
                                   purchase_order_line_id=pol.id),
        ])
        o1.status = "consolidated"; o2.status = "consolidated"
        db.flush(); db.commit()
        po_id = po.id
        pol_id = pol.id

    # Receive only 40 of 80 → ratio 0.5.
    client.post(
        "/api/v1/inbound-receipts",
        headers=warehouse_headers,
        json={"po_id": po_id, "lines": [
            {"po_line_id": pol_id, "quantity_received": 40.0},
        ]},
    )
    r = client.post(
        "/api/v1/pick-lists/generate",
        headers=warehouse_headers,
        json={"delivery_date": dd.isoformat()},
    )
    assert r.status_code == 201, r.text
    pk = r.json()["pick_list"]
    # Two lines, planned 25 and 15 (proportional to 50 and 30).
    qtys = sorted(ln["quantity"] for ln in pk["lines"])
    assert qtys == [15.0, 25.0]


def test_inventory_loss_creates_negative_movement(client, warehouse_headers):
    from app.core.database import SessionLocal
    from app.models import Product
    with SessionLocal() as db:
        potato = db.query(Product).filter(Product.sku == "VG001").one()
        potato_id = potato.id
    r = client.post(
        "/api/v1/inventory/loss",
        headers=warehouse_headers,
        json={"product_id": potato_id, "quantity": 5, "reason": "spoilage"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["quantity_delta"] == -5.0
    assert body["ref_type"] == "loss"
    # Ledger lookup.
    r2 = client.get(
        "/api/v1/inventory",
        headers=warehouse_headers,
        params={"product_id": potato_id},
    )
    assert r2.status_code == 200
    items = r2.json()["items"]
    assert any(m["quantity_delta"] == -5.0 for m in items)


def test_inventory_filter_by_product(client, warehouse_headers):
    from app.core.database import SessionLocal
    from app.models import Product
    with SessionLocal() as db:
        potato = db.query(Product).filter(Product.sku == "VG001").one()
        cabbage = db.query(Product).filter(Product.sku == "VG002").one()
        potato_id = potato.id
        cabbage_id = cabbage.id
    # Record losses on two different products.
    client.post("/api/v1/inventory/loss", headers=warehouse_headers,
                json={"product_id": potato_id, "quantity": 1, "reason": "test1"})
    client.post("/api/v1/inventory/loss", headers=warehouse_headers,
                json={"product_id": cabbage_id, "quantity": 2, "reason": "test2"})
    r = client.get("/api/v1/inventory", headers=warehouse_headers,
                   params={"product_id": potato_id})
    assert r.status_code == 200
    items = r.json()["items"]
    # All movements should be for the potato product.
    assert all(m["product_id"] == potato_id for m in items)


def test_warehouse_endpoints_require_auth(client):
    # No auth header → 401.
    r = client.get("/api/v1/inbound-receipts")
    assert r.status_code == 401
    r = client.get("/api/v1/pick-lists")
    assert r.status_code == 401
    r = client.get("/api/v1/inventory")
    assert r.status_code == 401


def test_pick_list_not_found(client, warehouse_headers):
    r = client.get("/api/v1/pick-lists/does-not-exist", headers=warehouse_headers)
    assert r.status_code == 404


def test_inbound_receipt_po_not_found(client, warehouse_headers):
    r = client.post(
        "/api/v1/inbound-receipts",
        headers=warehouse_headers,
        json={"po_id": "nope", "lines": [
            {"po_line_id": "x", "quantity_received": 1},
        ]},
    )
    assert r.status_code == 404


def test_pick_list_get_embeds_order_and_customer(client, warehouse_headers):
    from app.core.database import SessionLocal
    data = _seed_chain_for_inbound(SessionLocal())
    po_id = data["po"].id
    pol1_id, pol2_id = data["pol1"].id, data["pol2"].id
    dd = data["delivery_date"]
    client.post(
        "/api/v1/inbound-receipts",
        headers=warehouse_headers,
        json={"po_id": po_id, "lines": [
            {"po_line_id": pol1_id, "quantity_received": 50.0},
            {"po_line_id": pol2_id, "quantity_received": 30.0},
        ]},
    )
    client.post(
        "/api/v1/pick-lists/generate",
        headers=warehouse_headers,
        json={"delivery_date": dd.isoformat()},
    )
    r = client.get("/api/v1/pick-lists", headers=warehouse_headers,
                   params={"delivery_date": dd.isoformat()})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items
    pk = items[0]
    # Fetch the detail to check embeds.
    detail = client.get(f"/api/v1/pick-lists/{pk['id']}", headers=warehouse_headers).json()
    assert detail["lines"][0]["order_number"]
    assert detail["lines"][0]["customer_name_en"]
    assert detail["lines"][0]["customer_name_zh"]
    assert detail["lines"][0]["product_name_en"]
    assert detail["lines"][0]["product_name_zh"]
