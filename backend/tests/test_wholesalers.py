"""Tests for the wholesalers module (master data agent).

Covers: wholesalers CRUD, mappings (upsert + filters), supplier rules
(at least one of category/product, filters), role guards, in-use delete 409.
"""
from __future__ import annotations

from tests.conftest import admin_headers, client, finance_headers, ops_headers


# --- Wholesalers: list + filters ---------------------------------------------
def test_list_wholesalers_paged(client, admin_headers):
    r = client.get("/api/v1/wholesalers", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    for item in body["items"]:
        assert "code" in item and "name_en" in item and "name_zh" in item


def test_list_wholesalers_filter_q(client, admin_headers):
    r = client.get("/api/v1/wholesalers?q=foshan", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_list_wholesalers_finance_allowed(client, finance_headers):
    r = client.get("/api/v1/wholesalers", headers=finance_headers)
    assert r.status_code == 200


def test_list_wholesalers_requires_auth(client):
    r = client.get("/api/v1/wholesalers")
    assert r.status_code == 401


# --- Wholesalers: create -----------------------------------------------------
def test_create_wholesaler_success(client, admin_headers):
    r = client.post("/api/v1/wholesalers", headers=admin_headers, json={
        "code": "TEST-W-1",
        "name_en": "Test Wholesaler",
        "name_zh": "测试供应商",
        "contact_name": "Test Contact",
        "contact_phone": "13700000000",
        "is_active": True,
    })
    assert r.status_code == 201
    assert r.json()["code"] == "TEST-W-1"


def test_create_wholesaler_duplicate_code(client, admin_headers):
    r = client.post("/api/v1/wholesalers", headers=admin_headers, json={
        "code": "W001",
        "name_en": "Dup",
        "name_zh": "重复",
    })
    assert r.status_code == 400


def test_create_wholesaler_ops_allowed(client, ops_headers):
    r = client.post("/api/v1/wholesalers", headers=ops_headers, json={
        "code": "OPS-W-1",
        "name_en": "Ops Wholesaler",
        "name_zh": "运营供应商",
    })
    assert r.status_code == 201


def test_create_wholesaler_finance_forbidden(client, finance_headers):
    r = client.post("/api/v1/wholesalers", headers=finance_headers, json={
        "code": "FIN-W-1",
        "name_en": "Fin",
        "name_zh": "财务",
    })
    assert r.status_code == 403


# --- Wholesalers: GET / PATCH / DELETE ---------------------------------------
def test_get_wholesaler_not_found(client, admin_headers):
    r = client.get("/api/v1/wholesalers/nope-id", headers=admin_headers)
    assert r.status_code == 404


def test_patch_wholesaler(client, admin_headers):
    r = client.post("/api/v1/wholesalers", headers=admin_headers, json={
        "code": "PATCH-W-1",
        "name_en": "Before",
        "name_zh": "之前",
    })
    wid = r.json()["id"]

    r = client.patch(f"/api/v1/wholesalers/{wid}", headers=admin_headers, json={
        "name_en": "After",
        "contact_phone": "13700000099",
    })
    assert r.status_code == 200
    assert r.json()["name_en"] == "After"
    assert r.json()["contact_phone"] == "13700000099"


def test_delete_wholesaler_in_use_409(client, admin_headers):
    # W001 is in use (mappings) → 409.
    ws = client.get("/api/v1/wholesalers", headers=admin_headers).json()["items"]
    w001_id = next(w["id"] for w in ws if w["code"] == "W001")

    r = client.delete(f"/api/v1/wholesalers/{w001_id}", headers=admin_headers)
    assert r.status_code == 409


def test_delete_wholesaler_not_in_use(client, admin_headers):
    r = client.post("/api/v1/wholesalers", headers=admin_headers, json={
        "code": "DEL-W-1",
        "name_en": "Delete Me",
        "name_zh": "删除我",
    })
    wid = r.json()["id"]

    r = client.delete(f"/api/v1/wholesalers/{wid}", headers=admin_headers)
    assert r.status_code == 204


# --- Mappings: list + filters ------------------------------------------------
def test_list_mappings(client, admin_headers):
    r = client.get("/api/v1/product-wholesaler-mappings", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_list_mappings_filter_product(client, admin_headers):
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    pid = products[0]["id"]

    r = client.get(f"/api/v1/product-wholesaler-mappings?product_id={pid}", headers=admin_headers)
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["product_id"] == pid


def test_list_mappings_filter_wholesaler(client, admin_headers):
    ws = client.get("/api/v1/wholesalers", headers=admin_headers).json()["items"]
    wid = ws[0]["id"]

    r = client.get(f"/api/v1/product-wholesaler-mappings?wholesaler_id={wid}", headers=admin_headers)
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["wholesaler_id"] == wid


# --- Mappings: upsert --------------------------------------------------------
def test_mapping_upsert(client, admin_headers):
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    ws = client.get("/api/v1/wholesalers", headers=admin_headers).json()["items"]
    pid = products[0]["id"]
    wid = ws[0]["id"]

    # Create.
    r = client.post("/api/v1/product-wholesaler-mappings", headers=admin_headers, json={
        "product_id": pid,
        "wholesaler_id": wid,
        "supplier_sku": "UPSERT-1",
        "cost_price": 10.0,
    })
    assert r.status_code == 201
    mid_1 = r.json()["id"]

    # Upsert same pair → same id, updated fields.
    r = client.post("/api/v1/product-wholesaler-mappings", headers=admin_headers, json={
        "product_id": pid,
        "wholesaler_id": wid,
        "supplier_sku": "UPSERT-2",
        "cost_price": 12.5,
    })
    assert r.status_code == 201
    mid_2 = r.json()["id"]
    assert mid_2 == mid_1
    assert r.json()["supplier_sku"] == "UPSERT-2"
    assert r.json()["cost_price"] == 12.5


def test_delete_mapping(client, admin_headers):
    # Create a fresh mapping to delete.
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    ws = client.get("/api/v1/wholesalers", headers=admin_headers).json()["items"]

    # Use a product+wholesaler pair that may not already have a mapping.
    # Use a new wholesaler so the pair is unique.
    r = client.post("/api/v1/wholesalers", headers=admin_headers, json={
        "code": "MAP-DEL-W",
        "name_en": "Map Del W",
        "name_zh": "映射删除供应商",
    })
    wid = r.json()["id"]
    pid = products[0]["id"]

    r = client.post("/api/v1/product-wholesaler-mappings", headers=admin_headers, json={
        "product_id": pid,
        "wholesaler_id": wid,
        "cost_price": 5.0,
    })
    mid = r.json()["id"]

    r = client.delete(f"/api/v1/product-wholesaler-mappings/{mid}", headers=admin_headers)
    assert r.status_code == 204


def test_delete_mapping_not_found(client, admin_headers):
    r = client.delete("/api/v1/product-wholesaler-mappings/nope-id", headers=admin_headers)
    assert r.status_code == 404


# --- Supplier Rules ----------------------------------------------------------
def test_list_rules(client, admin_headers):
    r = client.get("/api/v1/supplier-rules", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_list_rules_filter_category(client, admin_headers):
    cats = client.get("/api/v1/product-categories", headers=admin_headers).json()["items"]
    cat_id = cats[0]["id"]

    r = client.get(f"/api/v1/supplier-rules?category_id={cat_id}", headers=admin_headers)
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["category_id"] == cat_id


def test_create_rule_success(client, admin_headers):
    cats = client.get("/api/v1/product-categories", headers=admin_headers).json()["items"]
    ws = client.get("/api/v1/wholesalers", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/supplier-rules", headers=admin_headers, json={
        "category_id": cats[0]["id"],
        "wholesaler_id": ws[0]["id"],
        "priority": 5,
        "moq": 10.0,
        "lead_time_days": 2,
        "is_default": False,
    })
    assert r.status_code == 201
    assert r.json()["category_id"] == cats[0]["id"]


def test_create_rule_requires_category_or_product(client, admin_headers):
    ws = client.get("/api/v1/wholesalers", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/supplier-rules", headers=admin_headers, json={
        "wholesaler_id": ws[0]["id"],
    })
    assert r.status_code == 400
    assert "at least one" in r.json()["detail"].lower()


def test_create_rule_product_id_ok(client, admin_headers):
    products = client.get("/api/v1/products", headers=admin_headers).json()["items"]
    ws = client.get("/api/v1/wholesalers", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/supplier-rules", headers=admin_headers, json={
        "product_id": products[0]["id"],
        "wholesaler_id": ws[0]["id"],
    })
    assert r.status_code == 201


def test_patch_rule(client, admin_headers):
    cats = client.get("/api/v1/product-categories", headers=admin_headers).json()["items"]
    ws = client.get("/api/v1/wholesalers", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/supplier-rules", headers=admin_headers, json={
        "category_id": cats[0]["id"],
        "wholesaler_id": ws[0]["id"],
    })
    rid = r.json()["id"]

    r = client.patch(f"/api/v1/supplier-rules/{rid}", headers=admin_headers, json={
        "priority": 99,
        "moq": 50.0,
    })
    assert r.status_code == 200
    assert r.json()["priority"] == 99
    assert r.json()["moq"] == 50.0


def test_delete_rule(client, admin_headers):
    cats = client.get("/api/v1/product-categories", headers=admin_headers).json()["items"]
    ws = client.get("/api/v1/wholesalers", headers=admin_headers).json()["items"]

    r = client.post("/api/v1/supplier-rules", headers=admin_headers, json={
        "category_id": cats[0]["id"],
        "wholesaler_id": ws[0]["id"],
    })
    rid = r.json()["id"]

    r = client.delete(f"/api/v1/supplier-rules/{rid}", headers=admin_headers)
    assert r.status_code == 204


def test_delete_rule_not_found(client, admin_headers):
    r = client.delete("/api/v1/supplier-rules/nope-id", headers=admin_headers)
    assert r.status_code == 404
