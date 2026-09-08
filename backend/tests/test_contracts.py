"""Tests for the contracts module (master data agent).

Covers: contract prices CRUD + filters, standing order templates CRUD,
the standing-order create-order flow, role guards.
"""
from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import admin_headers, client, finance_headers, ops_headers


# --- Contract prices: list + filters -----------------------------------------
def test_list_contract_prices(client, admin_headers):
    r = client.get("/api/v1/contract-prices", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_list_contract_prices_filter_customer(client, admin_headers):
    customers = client.get("/api/v1/customers", headers=admin_headers).json()["items"]
    cid = customers[0]["id"]

    r = client.get(f"/api/v1/contract-prices?customer_id={cid}", headers=admin_headers)
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["customer_id"] == cid


def test_list_contract_prices_filter_product(client, admin_headers):
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    pid = products[0]["id"]

    r = client.get(f"/api/v1/contract-prices?product_id={pid}", headers=admin_headers)
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["product_id"] == pid


def test_list_contract_prices_finance_allowed(client, finance_headers):
    r = client.get("/api/v1/contract-prices", headers=finance_headers)
    assert r.status_code == 200


# --- Contract prices: create + delete ----------------------------------------
def test_create_contract_price_success(client, admin_headers):
    customers = client.get("/api/v1/customers", headers=admin_headers).json()["items"]
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    units = client.get("/api/v1/units", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/contract-prices", headers=admin_headers, json={
        "customer_id": customers[0]["id"],
        "product_id": products[0]["id"],
        "unit_id": units[0]["id"],
        "price": 9.99,
        "valid_from": "2026-01-01",
    })
    assert r.status_code == 201
    assert r.json()["price"] == 9.99


def test_create_contract_price_invalid_customer(client, admin_headers):
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    units = client.get("/api/v1/units", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/contract-prices", headers=admin_headers, json={
        "customer_id": "nonexistent",
        "product_id": products[0]["id"],
        "unit_id": units[0]["id"],
        "price": 5.0,
        "valid_from": "2026-01-01",
    })
    assert r.status_code == 400


def test_delete_contract_price(client, admin_headers):
    customers = client.get("/api/v1/customers", headers=admin_headers).json()["items"]
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    units = client.get("/api/v1/units", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/contract-prices", headers=admin_headers, json={
        "customer_id": customers[0]["id"],
        "product_id": products[0]["id"],
        "unit_id": units[0]["id"],
        "price": 3.50,
        "valid_from": "2026-01-01",
    })
    cpid = r.json()["id"]

    r = client.delete(f"/api/v1/contract-prices/{cpid}", headers=admin_headers)
    assert r.status_code == 204


def test_delete_contract_price_not_found(client, admin_headers):
    r = client.delete("/api/v1/contract-prices/nope-id", headers=admin_headers)
    assert r.status_code == 404


# --- Standing order templates: list + create ---------------------------------
def test_list_templates(client, admin_headers):
    r = client.get("/api/v1/standing-order-templates", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_list_templates_filter_customer(client, admin_headers):
    customers = client.get("/api/v1/customers", headers=admin_headers).json()["items"]
    # Find the seeded standing-order customer (C003).
    c003 = next(c for c in customers if c["code"] == "C003")

    r = client.get(f"/api/v1/standing-order-templates?customer_id={c003['id']}", headers=admin_headers)
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["customer_id"] == c003["id"]


def test_create_template_success(client, admin_headers):
    customers = client.get("/api/v1/customers", headers=admin_headers).json()["items"]
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    units = client.get("/api/v1/units", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/standing-order-templates", headers=admin_headers, json={
        "customer_id": customers[0]["id"],
        "name": "Test Template",
        "delivery_days": ["mon", "wed"],
        "is_active": True,
        "lines": [
            {"product_id": products[0]["id"], "quantity": 5.0, "unit_id": units[0]["id"]},
            {"product_id": products[1]["id"], "quantity": 3.0, "unit_id": units[0]["id"]},
        ],
    })
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Test Template"
    assert len(body["lines"]) == 2
    assert body["delivery_days"] == ["mon", "wed"]


def test_create_template_invalid_product(client, admin_headers):
    customers = client.get("/api/v1/customers", headers=admin_headers).json()["items"]
    units = client.get("/api/v1/units", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/standing-order-templates", headers=admin_headers, json={
        "customer_id": customers[0]["id"],
        "name": "Bad Template",
        "delivery_days": [],
        "lines": [
            {"product_id": "nonexistent", "quantity": 5.0, "unit_id": units[0]["id"]},
        ],
    })
    assert r.status_code == 400


# --- Templates: GET / PATCH / DELETE -----------------------------------------
def test_get_template_not_found(client, admin_headers):
    r = client.get("/api/v1/standing-order-templates/nope-id", headers=admin_headers)
    assert r.status_code == 404


def test_patch_template_replace_lines(client, admin_headers):
    customers = client.get("/api/v1/customers", headers=admin_headers).json()["items"]
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    units = client.get("/api/v1/units", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/standing-order-templates", headers=admin_headers, json={
        "customer_id": customers[0]["id"],
        "name": "Patch Template",
        "delivery_days": ["mon"],
        "lines": [
            {"product_id": products[0]["id"], "quantity": 5.0, "unit_id": units[0]["id"]},
        ],
    })
    tid = r.json()["id"]
    assert len(r.json()["lines"]) == 1

    # Replace lines with 2 new ones.
    r = client.patch(f"/api/v1/standing-order-templates/{tid}", headers=admin_headers, json={
        "name": "Patched Template",
        "lines": [
            {"product_id": products[1]["id"], "quantity": 10.0, "unit_id": units[0]["id"]},
            {"product_id": products[2]["id"], "quantity": 2.0, "unit_id": units[0]["id"]},
        ],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Patched Template"
    assert len(body["lines"]) == 2


def test_delete_template_soft(client, admin_headers):
    customers = client.get("/api/v1/customers", headers=admin_headers).json()["items"]
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    units = client.get("/api/v1/units", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/standing-order-templates", headers=admin_headers, json={
        "customer_id": customers[0]["id"],
        "name": "Delete Template",
        "delivery_days": [],
        "lines": [
            {"product_id": products[0]["id"], "quantity": 5.0, "unit_id": units[0]["id"]},
        ],
    })
    tid = r.json()["id"]

    r = client.delete(f"/api/v1/standing-order-templates/{tid}", headers=admin_headers)
    assert r.status_code == 204

    # Still retrievable, is_active=False.
    r = client.get(f"/api/v1/standing-order-templates/{tid}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def test_delete_template_not_found(client, admin_headers):
    r = client.delete("/api/v1/standing-order-templates/nope-id", headers=admin_headers)
    assert r.status_code == 404


# --- Standing order → create draft order -------------------------------------
def test_create_order_from_template(client, admin_headers, pin_cutoff):
    """Create a draft order from the seeded standing order template.

    Verifies: order_number (ORD-...), status "draft", source_type "standing",
    standing_template_id set, one OrderLine per template line, confidence 1.0,
    match_method "manual".
    """
    # Auto-confirm would flip this to "confirmed" before the daily cutoff.
    pin_cutoff(False)
    # Find the seeded template (C003 "Weekly staples").
    templates = client.get("/api/v1/standing-order-templates", headers=admin_headers).json()["items"]
    template = templates[0]
    tid = template["id"]
    line_count = len(template["lines"])

    delivery_date = (date.today() + timedelta(days=1)).isoformat()
    r = client.post(f"/api/v1/standing-order-templates/{tid}/create-order", headers=admin_headers, json={
        "delivery_date": delivery_date,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["order_number"].startswith("ORD-")
    assert body["status"] == "draft"
    assert body["source_type"] == "standing"
    assert body["standing_template_id"] == tid
    assert body["customer_id"] == template["customer_id"]
    assert body["delivery_date"] == delivery_date
    assert body["overall_confidence"] == 1.0
    assert len(body["lines"]) == line_count

    for line in body["lines"]:
        assert line["confidence"] == 1.0
        assert line["match_method"] == "manual"
        assert line["product_display"]  # product name set
        assert line["quantity"] > 0


def test_create_order_from_template_default_date(client, admin_headers):
    """If no delivery_date given, defaults to today."""
    templates = client.get("/api/v1/standing-order-templates", headers=admin_headers).json()["items"]
    tid = templates[0]["id"]

    r = client.post(f"/api/v1/standing-order-templates/{tid}/create-order", headers=admin_headers, json={})
    assert r.status_code == 201
    assert r.json()["delivery_date"] == date.today().isoformat()


def test_create_order_template_not_found(client, admin_headers):
    r = client.post("/api/v1/standing-order-templates/nope-id/create-order", headers=admin_headers, json={})
    assert r.status_code == 404


def test_create_order_from_template_ops_allowed(client, ops_headers):
    templates = client.get("/api/v1/standing-order-templates", headers=ops_headers).json()["items"]
    tid = templates[0]["id"]

    r = client.post(f"/api/v1/standing-order-templates/{tid}/create-order", headers=ops_headers, json={})
    assert r.status_code == 201


def test_create_order_from_template_finance_forbidden(client, finance_headers):
    templates = client.get("/api/v1/standing-order-templates", headers=finance_headers).json()["items"]
    tid = templates[0]["id"]

    r = client.post(f"/api/v1/standing-order-templates/{tid}/create-order", headers=finance_headers, json={})
    assert r.status_code == 403
