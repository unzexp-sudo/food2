"""Tests for the catalog module (master data agent).

Covers: products CRUD + filters, categories CRUD + in-use 409,
units CRUD + in-use 409, product wholesalers sub-resource, role guards.
"""
from __future__ import annotations

from tests.conftest import admin_headers, client, ops_headers


# --- Products: list + filters ------------------------------------------------
def test_list_products_paged(client, admin_headers):
    r = client.get("/api/v1/products", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 16
    for item in body["items"]:
        assert "sku" in item and "name_en" in item and "name_zh" in item
        assert "category_name_en" in item
        assert "default_unit_code" in item


def test_list_products_filter_q(client, admin_headers):
    r = client.get("/api/v1/products?q=potato", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    for item in body["items"]:
        assert "potato" in item["name_en"].lower() or "potato" in item["sku"].lower()


def test_list_products_filter_active(client, admin_headers):
    r = client.get("/api/v1/products?is_active=true", headers=admin_headers)
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["is_active"] is True


def test_list_products_any_role(client):
    from tests.conftest import _auth_headers
    headers = _auth_headers("driver@erp.local")
    r = client.get("/api/v1/products", headers=headers)
    assert r.status_code == 200


def test_list_products_requires_auth(client):
    r = client.get("/api/v1/products")
    assert r.status_code == 401


# --- Products: create --------------------------------------------------------
def test_create_product_success(client, admin_headers):
    # Get a category and unit id.
    cats = client.get("/api/v1/product-categories", headers=admin_headers).json()["items"]
    units = client.get("/api/v1/units", headers=admin_headers).json()["items"]
    cat_id = cats[0]["id"]
    unit_id = units[0]["id"]

    r = client.post("/api/v1/products", headers=admin_headers, json={
        "sku": "TEST-PROD-1",
        "name_en": "Test Product",
        "name_zh": "测试产品",
        "category_id": cat_id,
        "default_unit_id": unit_id,
        "shelf_life_days": 30,
        "is_active": True,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["sku"] == "TEST-PROD-1"
    assert body["category_name_en"] is not None
    assert body["default_unit_code"] is not None


def test_create_product_duplicate_sku(client, admin_headers):
    r = client.post("/api/v1/products", headers=admin_headers, json={
        "sku": "VG001",
        "name_en": "Dup",
        "name_zh": "重复",
    })
    assert r.status_code == 400


def test_create_product_ops_allowed(client, ops_headers):
    r = client.post("/api/v1/products", headers=ops_headers, json={
        "sku": "OPS-PROD-1",
        "name_en": "Ops Product",
        "name_zh": "运营产品",
    })
    assert r.status_code == 201


def test_create_product_finance_forbidden(client):
    from tests.conftest import _auth_headers
    headers = _auth_headers("finance@erp.local")
    r = client.post("/api/v1/products", headers=headers, json={
        "sku": "FIN-PROD-1",
        "name_en": "Fin",
        "name_zh": "财务",
    })
    assert r.status_code == 403


# --- Products: GET / PATCH / DELETE -----------------------------------------
def test_get_product_not_found(client, admin_headers):
    r = client.get("/api/v1/products/nope-id", headers=admin_headers)
    assert r.status_code == 404


def test_patch_product(client, admin_headers):
    r = client.post("/api/v1/products", headers=admin_headers, json={
        "sku": "PATCH-PROD-1",
        "name_en": "Before",
        "name_zh": "之前",
    })
    pid = r.json()["id"]

    r = client.patch(f"/api/v1/products/{pid}", headers=admin_headers, json={
        "name_en": "After",
        "shelf_life_days": 99,
    })
    assert r.status_code == 200
    assert r.json()["name_en"] == "After"
    assert r.json()["shelf_life_days"] == 99


def test_delete_product_soft(client, admin_headers):
    r = client.post("/api/v1/products", headers=admin_headers, json={
        "sku": "DEL-PROD-1",
        "name_en": "Delete Me",
        "name_zh": "删除我",
    })
    pid = r.json()["id"]

    r = client.delete(f"/api/v1/products/{pid}", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["is_active"] is False
    assert body["id"] == pid


def test_delete_product_not_found(client, admin_headers):
    r = client.delete("/api/v1/products/nope-id", headers=admin_headers)
    assert r.status_code == 404


# --- Products: wholesalers sub-resource --------------------------------------
def test_get_product_wholesalers(client, admin_headers):
    r = client.get("/api/v1/products", headers=admin_headers)
    pid = r.json()["items"][0]["id"]

    r = client.get(f"/api/v1/products/{pid}/wholesalers", headers=admin_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    for item in r.json():
        assert "product_id" in item and "wholesaler_id" in item


# --- Categories ---------------------------------------------------------------
def test_list_categories(client, admin_headers):
    r = client.get("/api/v1/product-categories", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 4


def test_create_category(client, admin_headers):
    r = client.post("/api/v1/product-categories", headers=admin_headers, json={
        "name_en": "Beverages",
        "name_zh": "饮料",
        "is_active": True,
    })
    assert r.status_code == 201
    assert r.json()["name_en"] == "Beverages"


def test_patch_category(client, admin_headers):
    r = client.post("/api/v1/product-categories", headers=admin_headers, json={
        "name_en": "Patch Cat",
        "name_zh": "补丁分类",
    })
    cid = r.json()["id"]

    r = client.patch(f"/api/v1/product-categories/{cid}", headers=admin_headers, json={
        "name_en": "Patched Cat",
    })
    assert r.status_code == 200
    assert r.json()["name_en"] == "Patched Cat"


def test_delete_category_in_use_409(client, admin_headers):
    # Vegetables category is in use by seeded products → 409.
    cats = client.get("/api/v1/product-categories", headers=admin_headers).json()["items"]
    veg_id = next(c["id"] for c in cats if c["name_en"] == "Vegetables")

    r = client.delete(f"/api/v1/product-categories/{veg_id}", headers=admin_headers)
    assert r.status_code == 409


def test_delete_category_not_in_use(client, admin_headers):
    r = client.post("/api/v1/product-categories", headers=admin_headers, json={
        "name_en": "Delete Cat",
        "name_zh": "删除分类",
    })
    cid = r.json()["id"]

    r = client.delete(f"/api/v1/product-categories/{cid}", headers=admin_headers)
    assert r.status_code == 204


def test_delete_category_not_found(client, admin_headers):
    r = client.delete("/api/v1/product-categories/nope-id", headers=admin_headers)
    assert r.status_code == 404


# --- Units --------------------------------------------------------------------
def test_list_units(client, admin_headers):
    r = client.get("/api/v1/units", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 5


def test_create_unit(client, admin_headers):
    r = client.post("/api/v1/units", headers=admin_headers, json={
        "code": "crate",
        "name_en": "Crate",
        "name_zh": "筐",
    })
    assert r.status_code == 201
    assert r.json()["code"] == "crate"


def test_create_unit_duplicate_code(client, admin_headers):
    r = client.post("/api/v1/units", headers=admin_headers, json={
        "code": "jin",
        "name_en": "Dup",
        "name_zh": "重复",
    })
    assert r.status_code == 400


def test_patch_unit(client, admin_headers):
    r = client.post("/api/v1/units", headers=admin_headers, json={
        "code": "patch-unit",
        "name_en": "Before",
        "name_zh": "之前",
    })
    uid = r.json()["id"]

    r = client.patch(f"/api/v1/units/{uid}", headers=admin_headers, json={
        "name_en": "After",
    })
    assert r.status_code == 200
    assert r.json()["name_en"] == "After"


def test_delete_unit_in_use_409(client, admin_headers):
    # jin unit is in use → 409.
    units = client.get("/api/v1/units", headers=admin_headers).json()["items"]
    jin_id = next(u["id"] for u in units if u["code"] == "jin")

    r = client.delete(f"/api/v1/units/{jin_id}", headers=admin_headers)
    assert r.status_code == 409


def test_delete_unit_not_in_use(client, admin_headers):
    r = client.post("/api/v1/units", headers=admin_headers, json={
        "code": "delete-unit",
        "name_en": "Delete",
        "name_zh": "删除",
    })
    uid = r.json()["id"]

    r = client.delete(f"/api/v1/units/{uid}", headers=admin_headers)
    assert r.status_code == 204


def test_delete_unit_not_found(client, admin_headers):
    r = client.delete("/api/v1/units/nope-id", headers=admin_headers)
    assert r.status_code == 404
