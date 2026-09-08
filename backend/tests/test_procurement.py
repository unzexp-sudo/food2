"""Tests for the procurement module (consolidation + purchase orders).

Covers (per docs/AGENT_CONTRACTS.md §5):
- Consolidation run produces POs + exceptions
- Idempotency: 409 if an open batch already exists for the date
- Consolidation batch list / get
- PO list / get
- PO line edit (draft only → 409 otherwise)
- PO send / cancel
- Lineage reflects consolidation linkage after a run
- Role guard (driver forbidden)

Setup: confirmed orders are produced by disabling auto-confirm, creating orders
via POST /orders, then manually confirming each. This avoids time-of-day
flakiness in the auto-confirm cutoff.
"""
from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import admin_headers, client, ops_headers, _auth_headers


TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def _disable_auto_confirm(client, headers: dict) -> None:
    r = client.put(
        "/api/v1/settings",
        json={"auto_confirm": {"enabled": False, "min_confidence": 0.95}},
        headers=headers,
    )
    assert r.status_code == 200, r.text


def _restore_default_settings(client, headers: dict) -> None:
    """Restore seed defaults so other modules' tests still pass."""
    r = client.put(
        "/api/v1/settings",
        json={
            "auto_confirm": {"enabled": True, "min_confidence": 0.95},
            "cutoff_time": "18:00",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text


def _first_customer_id(client, headers: dict, code: str = "C001") -> str:
    r = client.get("/api/v1/customers", headers=headers)
    assert r.status_code == 200
    for c in r.json()["items"]:
        if c["code"] == code:
            return c["id"]
    raise AssertionError(f"Customer {code} not seeded")


def _first_product(client, headers: dict, sku: str = "VG001") -> dict:
    r = client.get("/api/v1/products", headers=headers)
    assert r.status_code == 200
    for p in r.json()["items"]:
        if p["sku"] == sku:
            return p
    raise AssertionError(f"Product {sku} not seeded")


def _first_unit_id(client, headers: dict, code: str = "jin") -> str:
    r = client.get("/api/v1/units", headers=headers)
    assert r.status_code == 200
    for u in r.json()["items"]:
        if u["code"] == code:
            return u["id"]
    raise AssertionError(f"Unit {code} not seeded")


def _create_confirmed_order(
    client,
    headers: dict,
    *,
    customer_code: str = "C001",
    delivery_date: str = TOMORROW,
    lines: list[dict] | None = None,
) -> dict:
    """Create a manual order, then manually confirm it."""
    cid = _first_customer_id(client, headers, customer_code)
    if lines is None:
        p = _first_product(client, headers, "VG001")
        uid = _first_unit_id(client, headers, "jin")
        lines = [{"product_id": p["id"], "product_display": "Potato 土豆",
                  "quantity": 50, "unit_id": uid}]
    body = {"customer_id": cid, "delivery_date": delivery_date, "lines": lines}
    r = client.post("/api/v1/orders", json=body, headers=headers)
    assert r.status_code == 201, r.text
    order = r.json()
    # Confirm it.
    r2 = client.post(
        f"/api/v1/orders/{order['id']}/confirm",
        json={}, headers=headers,
    )
    assert r2.status_code == 200, r2.text
    return r2.json()


# ---------------------------------------------------------------------------
# 1. Consolidation run produces POs + exceptions
# ---------------------------------------------------------------------------
def test_consolidation_run_produces_pos(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        # Use a unique date so other tests' orders don't pollute this batch.
        unique = (date.today() + timedelta(days=5)).isoformat()
        p1 = _first_product(client, admin_headers, "VG001")
        p2 = _first_product(client, admin_headers, "VG002")
        uid = _first_unit_id(client, admin_headers, "jin")
        _create_confirmed_order(
            client, admin_headers,
            customer_code="C001",
            delivery_date=unique,
            lines=[{"product_id": p1["id"], "product_display": "Potato 土豆",
                    "quantity": 50, "unit_id": uid}],
        )
        _create_confirmed_order(
            client, admin_headers,
            customer_code="C002",
            delivery_date=unique,
            lines=[{"product_id": p2["id"], "product_display": "Cabbage 大白菜",
                    "quantity": 30, "unit_id": uid}],
        )

        r = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        body = r.json()
        assert body["batch"]["batch_number"].startswith("CON-")
        assert body["batch"]["delivery_date"] == unique
        assert body["batch"]["order_count"] == 2
        assert len(body["purchase_orders"]) >= 1
        assert body["batch"]["exception_count"] == 0

        # Each PO summary has wholesaler & category.
        for po in body["purchase_orders"]:
            assert po["po_number"].startswith("PO-")
            assert po["wholesaler_id"]
            assert po["wholesaler_name_en"]
            assert po["wholesaler_name_zh"]
            assert po["status"] == "draft"
            assert po["total_amount"] >= 0
    finally:
        _restore_default_settings(client, admin_headers)


def test_consolidation_run_with_exception(client, admin_headers):
    """A line with no product_id (unmatched) cannot resolve a supplier → exception."""
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=19)).isoformat()
        cid = _first_customer_id(client, admin_headers, "C001")
        # Unmatched line: no product_id, no unit_id — supplier resolution fails.
        body = {
            "customer_id": cid, "delivery_date": unique,
            "lines": [{"product_display": "Mystery 没这菜", "quantity": 10}],
        }
        r = client.post("/api/v1/orders", json=body, headers=admin_headers)
        assert r.status_code == 201, r.text
        order = r.json()
        r2 = client.post(
            f"/api/v1/orders/{order['id']}/confirm", json={}, headers=admin_headers
        )
        assert r2.status_code == 200, r2.text

        r3 = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r3.status_code == 200, r3.text
        body3 = r3.json()
        assert body3["batch"]["exception_count"] == 1
        assert len(body3["exceptions"]) == 1
        assert body3["exceptions"][0]["reason"] == "no_supplier"
        # No POs created (the only line had no supplier).
        assert len(body3["purchase_orders"]) == 0
    finally:
        _restore_default_settings(client, admin_headers)


# ---------------------------------------------------------------------------
# 2. Idempotency: 409 if open batch already exists for the date
# ---------------------------------------------------------------------------
def test_consolidation_idempotency_409(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=10)).isoformat()
        _create_confirmed_order(client, admin_headers, delivery_date=unique)
        r1 = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r1.status_code == 200, r1.text
        # Second run on the same date → 409.
        r2 = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r2.status_code == 409
    finally:
        _restore_default_settings(client, admin_headers)


def test_consolidation_no_confirmed_orders(client, admin_headers):
    # Pick a date far in the future with no orders.
    far = (date.today() + timedelta(days=30)).isoformat()
    r = client.post(
        "/api/v1/consolidation/run",
        json={"delivery_date": far},
        headers=admin_headers,
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# 3. Batch list / get
# ---------------------------------------------------------------------------
def test_batch_list_and_get(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=11)).isoformat()
        _create_confirmed_order(client, admin_headers, delivery_date=unique)
        r = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        batch_id = r.json()["batch"]["id"]

        # List.
        r2 = client.get(
            f"/api/v1/consolidation/batches?delivery_date={unique}",
            headers=admin_headers,
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["total"] >= 1
        assert any(b["id"] == batch_id for b in body["items"])

        # Get.
        r3 = client.get(
            f"/api/v1/consolidation/batches/{batch_id}",
            headers=admin_headers,
        )
        assert r3.status_code == 200, r3.text
        detail = r3.json()
        assert detail["id"] == batch_id
        assert isinstance(detail["purchase_orders"], list)
        assert isinstance(detail["exceptions"], list)
    finally:
        _restore_default_settings(client, admin_headers)


def test_batch_get_not_found(client, admin_headers):
    r = client.get("/api/v1/consolidation/batches/nope", headers=admin_headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 4. PO list / get
# ---------------------------------------------------------------------------
def test_po_list_and_get(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=12)).isoformat()
        _create_confirmed_order(client, admin_headers, delivery_date=unique)
        r = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        pos = r.json()["purchase_orders"]
        assert pos
        po_id = pos[0]["id"]

        # List with status filter.
        r2 = client.get(
            "/api/v1/purchase-orders?status=draft",
            headers=admin_headers,
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["total"] >= 1
        for it in body["items"]:
            assert it["status"] == "draft"

        # List filtered by delivery_date (batch's date).
        r3 = client.get(
            f"/api/v1/purchase-orders?delivery_date={unique}",
            headers=admin_headers,
        )
        assert r3.status_code == 200
        body3 = r3.json()
        assert body3["total"] >= 1
        assert any(it["id"] == po_id for it in body3["items"])

        # Get single.
        r4 = client.get(
            f"/api/v1/purchase-orders/{po_id}",
            headers=admin_headers,
        )
        assert r4.status_code == 200, r4.text
        detail = r4.json()
        assert detail["id"] == po_id
        assert isinstance(detail["lines"], list)
        assert len(detail["lines"]) >= 1
        # Each line embeds source_orders list.
        for ln in detail["lines"]:
            assert isinstance(ln["source_orders"], list)
            assert all(s.startswith("ORD-") for s in ln["source_orders"])
    finally:
        _restore_default_settings(client, admin_headers)


def test_po_get_not_found(client, admin_headers):
    r = client.get("/api/v1/purchase-orders/nope", headers=admin_headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 5. PO line edit (draft only → 409 otherwise)
# ---------------------------------------------------------------------------
def test_patch_po_lines_draft(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=13)).isoformat()
        _create_confirmed_order(client, admin_headers, delivery_date=unique)
        r = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        pos = r.json()["purchase_orders"]
        po_id = pos[0]["id"]
        detail = client.get(
            f"/api/v1/purchase-orders/{po_id}", headers=admin_headers
        ).json()
        line_id = detail["lines"][0]["id"]
        old_total = detail["total_amount"]

        r2 = client.patch(
            f"/api/v1/purchase-orders/{po_id}/lines",
            json={"lines": [{"id": line_id, "quantity_ordered": 200,
                            "cost_price": 1.5}]},
            headers=admin_headers,
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["lines"][0]["quantity_ordered"] == 200
        assert body["lines"][0]["cost_price"] == 1.5
        # total recomputed.
        assert body["total_amount"] == 200 * 1.5
        assert body["total_amount"] != old_total
    finally:
        _restore_default_settings(client, admin_headers)


def test_patch_po_lines_sent_409(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=14)).isoformat()
        _create_confirmed_order(client, admin_headers, delivery_date=unique)
        r = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        po_id = r.json()["purchase_orders"][0]["id"]
        # Send the PO first.
        r2 = client.post(
            f"/api/v1/purchase-orders/{po_id}/send", headers=admin_headers
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "sent"
        detail = client.get(
            f"/api/v1/purchase-orders/{po_id}", headers=admin_headers
        ).json()
        line_id = detail["lines"][0]["id"]
        r3 = client.patch(
            f"/api/v1/purchase-orders/{po_id}/lines",
            json={"lines": [{"id": line_id, "quantity_ordered": 200}]},
            headers=admin_headers,
        )
        assert r3.status_code == 409
    finally:
        _restore_default_settings(client, admin_headers)


# ---------------------------------------------------------------------------
# 6. PO send / cancel
# ---------------------------------------------------------------------------
def test_po_send_and_cancel(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=15)).isoformat()
        _create_confirmed_order(client, admin_headers, delivery_date=unique)
        r = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        po_id = r.json()["purchase_orders"][0]["id"]

        # Send.
        r2 = client.post(
            f"/api/v1/purchase-orders/{po_id}/send", headers=admin_headers
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["status"] == "sent"
        assert body["sent_at"] is not None

        # Cancel from sent.
        r3 = client.post(
            f"/api/v1/purchase-orders/{po_id}/cancel", headers=admin_headers
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()["status"] == "cancelled"
    finally:
        _restore_default_settings(client, admin_headers)


def test_po_cancel_received_409(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=16)).isoformat()
        _create_confirmed_order(client, admin_headers, delivery_date=unique)
        r = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        po_id = r.json()["purchase_orders"][0]["id"]
        # Manually flip status via service to "received" — can't via API,
        # so we patch through the DB.
        from app.core.database import SessionLocal
        from app.models import PurchaseOrder
        with SessionLocal() as db:
            po = db.get(PurchaseOrder, po_id)
            po.status = "received"
            db.commit()
        r2 = client.post(
            f"/api/v1/purchase-orders/{po_id}/cancel", headers=admin_headers
        )
        assert r2.status_code == 409
    finally:
        _restore_default_settings(client, admin_headers)


# ---------------------------------------------------------------------------
# 7. Lineage reflects consolidation linkage after a run
# ---------------------------------------------------------------------------
def test_lineage_after_consolidation(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=17)).isoformat()
        order = _create_confirmed_order(client, admin_headers, delivery_date=unique)
        r = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        batch_number = r.json()["batch"]["batch_number"]
        po_number = r.json()["purchase_orders"][0]["po_number"]

        r2 = client.get(
            f"/api/v1/orders/{order['id']}/lineage", headers=admin_headers
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        ln = body["lines"][0]
        assert ln["batch_number"] == batch_number
        assert ln["po_number"] == po_number
        assert ln["po_quantity"] == 50
        # No receipts/picks/deliveries/invoices yet.
        assert ln["received_quantity"] == 0
        assert ln["picked_quantity"] is None
        assert ln["delivered_quantity"] is None
        assert ln["invoice_id"] is None
    finally:
        _restore_default_settings(client, admin_headers)


# ---------------------------------------------------------------------------
# 8. Role guards
# ---------------------------------------------------------------------------
def test_driver_cannot_run_consolidation(client):
    driver_headers = _auth_headers("driver@erp.local")
    r = client.post(
        "/api/v1/consolidation/run",
        json={"delivery_date": TOMORROW},
        headers=driver_headers,
    )
    assert r.status_code == 403


def test_finance_can_list_pos(client, finance_headers):
    r = client.get("/api/v1/purchase-orders", headers=finance_headers)
    assert r.status_code == 200


def test_finance_cannot_run_consolidation(client, finance_headers):
    r = client.post(
        "/api/v1/consolidation/run",
        json={"delivery_date": TOMORROW},
        headers=finance_headers,
    )
    assert r.status_code == 403


def test_ops_can_run_consolidation(client, admin_headers, ops_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        unique = (date.today() + timedelta(days=18)).isoformat()
        _create_confirmed_order(client, ops_headers, delivery_date=unique)
        r = client.post(
            "/api/v1/consolidation/run",
            json={"delivery_date": unique},
            headers=ops_headers,
        )
        assert r.status_code == 200, r.text
    finally:
        _restore_default_settings(client, admin_headers)
