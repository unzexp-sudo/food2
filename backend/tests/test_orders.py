"""Tests for the orders module.

Covers (per docs/AGENT_CONTRACTS.md §5):
- Manual order create → auto-confirm (high confidence) → confirmed
- Auto-confirm disabled → stays draft
- Low confidence (unmatched line) → pending_confirmation
- Manual confirm endpoint
- Reject endpoint
- Request-clarification + resubmit
- Patch lines (allowed in editable statuses; 409 in confirmed)
- Lineage (empty chain on a fresh order)
- Role guard (finance read-only; driver forbidden)
- Filters / pagination on list

Tests run against the seeded DB (see conftest.py). The auto-confirm cutoff is
pushed to 23:59 in setup so tests are time-of-day independent.
"""
from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import admin_headers, client, ops_headers, finance_headers, _auth_headers


TOMORROW = (date.today() + timedelta(days=1)).isoformat()
DAY_AFTER = (date.today() + timedelta(days=2)).isoformat()


def _setup_late_cutoff(client, headers: dict) -> None:
    """Push the auto-confirm cutoff to 23:59 so tests run any time of day."""
    r = client.put(
        "/api/v1/settings",
        json={
            "auto_confirm": {"enabled": True, "min_confidence": 0.95},
            "cutoff_time": "23:59",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text


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


def _enable_auto_confirm(client, headers: dict) -> None:
    r = client.put(
        "/api/v1/settings",
        json={"auto_confirm": {"enabled": True, "min_confidence": 0.95}},
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


def _make_manual_order(
    client,
    headers: dict,
    *,
    customer_id: str | None = None,
    delivery_date: str | None = None,
    lines: list[dict] | None = None,
) -> dict:
    """Create a manual order via POST /orders."""
    cid = customer_id or _first_customer_id(client, headers)
    dd = delivery_date or TOMORROW
    if lines is None:
        p = _first_product(client, headers, "VG001")
        uid = _first_unit_id(client, headers, "jin")
        lines = [{"product_id": p["id"], "product_display": "Potato 土豆",
                  "quantity": 50, "unit_id": uid}]
    body = {
        "customer_id": cid,
        "delivery_date": dd,
        "lines": lines,
    }
    r = client.post("/api/v1/orders", json=body, headers=headers)
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    return r.json()


# ---------------------------------------------------------------------------
# 1. Manual create → auto-confirm (high confidence, all matched)
# ---------------------------------------------------------------------------
def test_manual_order_auto_confirms(client, admin_headers):
    _setup_late_cutoff(client, admin_headers)
    order = _make_manual_order(client, admin_headers)
    assert order["status"] == "confirmed", (
        f"High-confidence matched order should auto-confirm; got {order['status']}"
    )
    assert order["confirmed_at"] is not None
    # Lines should have contract prices locked (C001 has VG001 jin = 2.2)
    ln = order["lines"][0]
    assert ln["unit_price"] == 2.2


# ---------------------------------------------------------------------------
# 2. Auto-confirm disabled → order stays draft
# ---------------------------------------------------------------------------
def test_auto_confirm_disabled_stays_draft(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    order = _make_manual_order(client, admin_headers)
    assert order["status"] == "draft"
    # Re-enable for subsequent tests.
    _enable_auto_confirm(client, admin_headers)
    _setup_late_cutoff(client, admin_headers)


# ---------------------------------------------------------------------------
# 3. Low confidence (unmatched line) → pending_confirmation
# ---------------------------------------------------------------------------
def test_unmatched_line_goes_to_pending(client, admin_headers):
    _setup_late_cutoff(client, admin_headers)
    # Line with no product_id → unmatched → confidence 1.0 per service, but
    # the unmatched line lowers overall confidence? In our service manual
    # lines default confidence 1.0. To simulate "low confidence", we'll
    # patch the order lines with an unmatched product_display (no product_id).
    cid = _first_customer_id(client, admin_headers)
    body = {
        "customer_id": cid,
        "delivery_date": TOMORROW,
        "lines": [
            {"product_display": "Mystery Veg 未知菜", "quantity": 10},
        ],
    }
    r = client.post("/api/v1/orders", json=body, headers=admin_headers)
    assert r.status_code == 201, r.text
    order = r.json()
    # Unmatched line → auto-confirm rules fail → pending_confirmation.
    assert order["status"] == "pending_confirmation", (
        f"Unmatched order should go to pending; got {order['status']}"
    )
    assert order["lines"][0]["match_method"] == "unmatched"


# ---------------------------------------------------------------------------
# 4. Manual confirm endpoint (auto-confirm disabled)
# ---------------------------------------------------------------------------
def test_manual_confirm_endpoint(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        order = _make_manual_order(client, admin_headers)
        assert order["status"] == "draft"
        r = client.post(
            f"/api/v1/orders/{order['id']}/confirm",
            json={"notes": "Looks good"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "confirmed"
        assert body["confirmed_at"] is not None
        assert body["confirmed_by"] is not None
        # Contract price locked.
        assert body["lines"][0]["unit_price"] == 2.2
    finally:
        _restore_default_settings(client, admin_headers)


def test_confirm_confirmed_order_409(client, admin_headers):
    _setup_late_cutoff(client, admin_headers)
    order = _make_manual_order(client, admin_headers)  # auto-confirms
    assert order["status"] == "confirmed"
    r = client.post(
        f"/api/v1/orders/{order['id']}/confirm",
        json={}, headers=admin_headers,
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# 5. Reject
# ---------------------------------------------------------------------------
def test_reject_order(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        order = _make_manual_order(client, admin_headers)
        r = client.post(
            f"/api/v1/orders/{order['id']}/reject",
            json={"reason": "Customer cancelled"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "rejected"
        assert "Customer cancelled" in (body["notes"] or "")
    finally:
        _restore_default_settings(client, admin_headers)


# ---------------------------------------------------------------------------
# 6. Request-clarification + resubmit
# ---------------------------------------------------------------------------
def test_clarification_and_resubmit(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        order = _make_manual_order(client, admin_headers)
        r = client.post(
            f"/api/v1/orders/{order['id']}/request-clarification",
            json={"note": "Quantity unclear"},
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "needs_clarification"

        r = client.post(
            f"/api/v1/orders/{order['id']}/resubmit",
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "pending_confirmation"
    finally:
        _restore_default_settings(client, admin_headers)


def test_resubmit_wrong_status_409(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        order = _make_manual_order(client, admin_headers)  # draft
        # Resubmit from draft → 409 (only needs_clarification may resubmit)
        r = client.post(
            f"/api/v1/orders/{order['id']}/resubmit", headers=admin_headers
        )
        assert r.status_code == 409
    finally:
        _restore_default_settings(client, admin_headers)


# ---------------------------------------------------------------------------
# 7. Patch lines
# ---------------------------------------------------------------------------
def test_patch_lines_when_editable(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        order = _make_manual_order(client, admin_headers)
        p2 = _first_product(client, admin_headers, "VG002")
        u2 = _first_unit_id(client, admin_headers, "jin")
        r = client.patch(
            f"/api/v1/orders/{order['id']}/lines",
            json={
                "lines": [
                    {"product_id": p2["id"], "product_display": "Cabbage 大白菜",
                     "quantity": 30, "unit_id": u2},
                ]
            },
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["lines"]) == 1
        assert body["lines"][0]["product_id"] == p2["id"]
        assert body["lines"][0]["quantity"] == 30
        assert body["lines"][0]["match_method"] == "manual"
    finally:
        _restore_default_settings(client, admin_headers)


def test_patch_lines_confirmed_409(client, admin_headers):
    _setup_late_cutoff(client, admin_headers)
    order = _make_manual_order(client, admin_headers)  # auto-confirms
    r = client.patch(
        f"/api/v1/orders/{order['id']}/lines",
        json={"lines": [{"product_display": "X", "quantity": 1}]},
        headers=admin_headers,
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# 8. Lineage (empty chain on a fresh order)
# ---------------------------------------------------------------------------
def test_lineage_empty_chain(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        order = _make_manual_order(client, admin_headers)
        r = client.get(
            f"/api/v1/orders/{order['id']}/lineage", headers=admin_headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["order_id"] == order["id"]
        assert body["order_number"] == order["order_number"]
        assert len(body["lines"]) == 1
        ln = body["lines"][0]
        assert ln["batch_number"] is None
        assert ln["po_number"] is None
        assert ln["received_quantity"] is None
        assert ln["picked_quantity"] is None
        assert ln["delivered_quantity"] is None
        assert ln["invoice_id"] is None
    finally:
        _restore_default_settings(client, admin_headers)


# ---------------------------------------------------------------------------
# 9. List filters & pagination
# ---------------------------------------------------------------------------
def test_list_orders_filters(client, admin_headers):
    # Status filter.
    r = client.get("/api/v1/orders?status=confirmed", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    for it in body["items"]:
        assert it["status"] == "confirmed"
    assert body["total"] >= 1
    # q filter on order_number.
    if body["items"]:
        on = body["items"][0]["order_number"]
        # Use first 4 chars as a substring.
        r2 = client.get(f"/api/v1/orders?q={on[:4]}", headers=admin_headers)
        assert r2.status_code == 200
        assert r2.json()["total"] >= 1


def test_list_orders_pagination(client, admin_headers):
    r = client.get("/api/v1/orders?page=1&page_size=5", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 1
    assert body["page_size"] == 5
    assert len(body["items"]) <= 5


# ---------------------------------------------------------------------------
# 10. Role guards
# ---------------------------------------------------------------------------
def test_finance_can_read_orders(client, finance_headers):
    r = client.get("/api/v1/orders", headers=finance_headers)
    assert r.status_code == 200


def test_finance_cannot_create_orders(client, finance_headers):
    cid = _first_customer_id(client, finance_headers)
    r = client.post(
        "/api/v1/orders",
        json={"customer_id": cid, "delivery_date": TOMORROW,
              "lines": [{"product_display": "X", "quantity": 1}]},
        headers=finance_headers,
    )
    assert r.status_code == 403


def test_driver_cannot_access_orders(client):
    driver_headers = _auth_headers("driver@erp.local")
    r = client.get("/api/v1/orders", headers=driver_headers)
    assert r.status_code == 403


def test_get_order_not_found(client, admin_headers):
    r = client.get("/api/v1/orders/nonexistent-id", headers=admin_headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 11. Get single order has lines embedded
# ---------------------------------------------------------------------------
def test_get_order_has_lines(client, admin_headers):
    _disable_auto_confirm(client, admin_headers)
    try:
        order = _make_manual_order(client, admin_headers)
        r = client.get(f"/api/v1/orders/{order['id']}", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == order["id"]
        assert len(body["lines"]) == 1
        assert body["line_count"] == 1
        # Embedded customer + unit fields.
        assert body["customer_name_en"]
        assert body["customer_name_zh"]
        assert body["lines"][0]["unit_code"] == "jin"
    finally:
        _restore_default_settings(client, admin_headers)
