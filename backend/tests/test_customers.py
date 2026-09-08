"""Tests for the customers module (master data agent).

Covers: CRUD, role guards, soft delete, contacts, aliases (upsert), filters.
"""
from __future__ import annotations

from tests.conftest import admin_headers, client, finance_headers, ops_headers


# --- List + filters ----------------------------------------------------------
def test_list_customers_paged(client, admin_headers):
    r = client.get("/api/v1/customers", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 1
    assert body["total"] >= 3
    assert isinstance(body["items"], list)
    for item in body["items"]:
        assert "id" in item and "code" in item
        assert "name_en" in item and "name_zh" in item


def test_list_customers_filter_q(client, admin_headers):
    r = client.get("/api/v1/customers?q=golden", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    for item in body["items"]:
        assert "golden" in item["name_en"].lower() or "golden" in item["code"].lower()


def test_list_customers_filter_type(client, admin_headers):
    r = client.get("/api/v1/customers?type=school", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    for item in body["items"]:
        assert item["type"] == "school"


def test_list_customers_filter_status(client, admin_headers):
    r = client.get("/api/v1/customers?status=active", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert all(item["status"] == "active" for item in body["items"])


def test_list_customers_requires_auth(client):
    r = client.get("/api/v1/customers")
    assert r.status_code == 401


def test_list_customers_finance_allowed(client, finance_headers):
    r = client.get("/api/v1/customers", headers=finance_headers)
    assert r.status_code == 200


def test_list_customers_driver_forbidden(client):
    from tests.conftest import _auth_headers
    headers = _auth_headers("driver@erp.local")
    r = client.get("/api/v1/customers", headers=headers)
    assert r.status_code == 403


# --- Create ------------------------------------------------------------------
def test_create_customer_success(client, admin_headers):
    r = client.post("/api/v1/customers", headers=admin_headers, json={
        "code": "TEST-CUST-1",
        "name_en": "Test Customer",
        "name_zh": "测试客户",
        "type": "restaurant",
        "contact_name": "Test Contact",
        "contact_phone": "13800000099",
        "status": "active",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["code"] == "TEST-CUST-1"
    assert body["name_en"] == "Test Customer"
    assert body["name_zh"] == "测试客户"
    assert body["type"] == "restaurant"


def test_create_customer_duplicate_code(client, admin_headers):
    r = client.post("/api/v1/customers", headers=admin_headers, json={
        "code": "C001",
        "name_en": "Dup",
        "name_zh": "重复",
        "type": "other",
    })
    assert r.status_code == 400


def test_create_customer_ops_allowed(client, ops_headers):
    r = client.post("/api/v1/customers", headers=ops_headers, json={
        "code": "OPS-CUST-1",
        "name_en": "Ops Created",
        "name_zh": "运营创建",
        "type": "canteen",
    })
    assert r.status_code == 201


def test_create_customer_finance_forbidden(client, finance_headers):
    r = client.post("/api/v1/customers", headers=finance_headers, json={
        "code": "FIN-CUST-1",
        "name_en": "Fin",
        "name_zh": "财务",
        "type": "other",
    })
    assert r.status_code == 403


# --- GET / PATCH / DELETE single --------------------------------------------
def test_get_customer_not_found(client, admin_headers):
    r = client.get("/api/v1/customers/nonexistent-id", headers=admin_headers)
    assert r.status_code == 404


def test_get_customer_by_id(client, admin_headers):
    r = client.post("/api/v1/customers", headers=admin_headers, json={
        "code": "GET-CUST-1",
        "name_en": "Get Me",
        "name_zh": "获取我",
        "type": "school",
    })
    cid = r.json()["id"]

    r = client.get(f"/api/v1/customers/{cid}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["id"] == cid


def test_patch_customer(client, admin_headers):
    r = client.post("/api/v1/customers", headers=admin_headers, json={
        "code": "PATCH-CUST-1",
        "name_en": "Before",
        "name_zh": "之前",
        "type": "other",
    })
    cid = r.json()["id"]

    r = client.patch(f"/api/v1/customers/{cid}", headers=admin_headers, json={
        "name_en": "After",
        "notes": "Updated notes",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["name_en"] == "After"
    assert body["notes"] == "Updated notes"


def test_delete_customer_soft(client, admin_headers):
    r = client.post("/api/v1/customers", headers=admin_headers, json={
        "code": "DEL-CUST-1",
        "name_en": "Delete Me",
        "name_zh": "删除我",
        "type": "other",
    })
    cid = r.json()["id"]

    r = client.delete(f"/api/v1/customers/{cid}", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "inactive"
    assert body["id"] == cid

    # Still retrievable (soft delete).
    r = client.get(f"/api/v1/customers/{cid}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "inactive"


def test_delete_customer_not_found(client, admin_headers):
    r = client.delete("/api/v1/customers/nope-id", headers=admin_headers)
    assert r.status_code == 404


# --- Contacts ----------------------------------------------------------------
def test_contacts_crud(client, admin_headers):
    # Create a customer first.
    r = client.post("/api/v1/customers", headers=admin_headers, json={
        "code": "CONTACT-CUST-1",
        "name_en": "Contact Customer",
        "name_zh": "联系人客户",
        "type": "school",
    })
    cid = r.json()["id"]

    # List contacts (empty).
    r = client.get(f"/api/v1/customers/{cid}/contacts", headers=admin_headers)
    assert r.status_code == 200
    assert r.json() == []

    # Create a contact.
    r = client.post(f"/api/v1/customers/{cid}/contacts", headers=admin_headers, json={
        "name": "John",
        "phone": "13900000000",
        "role": "Manager",
    })
    assert r.status_code == 201
    contact_id = r.json()["id"]
    assert r.json()["name"] == "John"

    # List again — should have one.
    r = client.get(f"/api/v1/customers/{cid}/contacts", headers=admin_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Delete contact.
    r = client.delete(f"/api/v1/customers/contacts/{contact_id}", headers=admin_headers)
    assert r.status_code == 204

    # List — empty again.
    r = client.get(f"/api/v1/customers/{cid}/contacts", headers=admin_headers)
    assert r.json() == []


def test_delete_contact_not_found(client, admin_headers):
    r = client.delete("/api/v1/customers/contacts/nope-id", headers=admin_headers)
    assert r.status_code == 404


# --- Aliases (upsert) --------------------------------------------------------
def test_alias_upsert(client, admin_headers):
    # Get a product id from catalog.
    r = client.get("/api/v1/products", headers=admin_headers)
    product_id = r.json()["items"][0]["id"]

    # Create a customer.
    r = client.post("/api/v1/customers", headers=admin_headers, json={
        "code": "ALIAS-CUST-1",
        "name_en": "Alias Customer",
        "name_zh": "别名客户",
        "type": "school",
    })
    cid = r.json()["id"]

    # Create an alias.
    r = client.post(f"/api/v1/customers/{cid}/aliases", headers=admin_headers, json={
        "alias": "土豆",
        "product_id": product_id,
    })
    assert r.status_code == 201
    alias_id_1 = r.json()["id"]
    assert r.json()["product_id"] == product_id

    # Get another product id for the upsert test.
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    other_product_id = products[1]["id"]

    # Upsert: same alias text, different product → should replace, not duplicate.
    r = client.post(f"/api/v1/customers/{cid}/aliases", headers=admin_headers, json={
        "alias": "土豆",
        "product_id": other_product_id,
    })
    assert r.status_code == 201
    alias_id_2 = r.json()["id"]
    assert alias_id_2 == alias_id_1  # same row, updated
    assert r.json()["product_id"] == other_product_id

    # List — only one alias.
    r = client.get(f"/api/v1/customers/{cid}/aliases", headers=admin_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Delete alias.
    r = client.delete(f"/api/v1/customers/aliases/{alias_id_1}", headers=admin_headers)
    assert r.status_code == 204

    r = client.get(f"/api/v1/customers/{cid}/aliases", headers=admin_headers)
    assert r.json() == []


def test_delete_alias_not_found(client, admin_headers):
    r = client.delete("/api/v1/customers/aliases/nope-id", headers=admin_headers)
    assert r.status_code == 404
