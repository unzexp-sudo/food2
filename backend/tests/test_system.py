"""Tests for the system module (auth/system agent).

Covers: user CRUD + role validation, audit-log filtering, settings get/put,
and settings/public. Also verifies password_hash never appears in responses.
"""
from __future__ import annotations

from tests.conftest import admin_headers, client, ops_headers


# --- users: list + filters ----------------------------------------------------
def test_list_users_paged(client, admin_headers):
    r = client.get("/api/v1/users", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total"] >= 5  # 5 seeded users
    assert isinstance(body["items"], list)
    for item in body["items"]:
        assert "password_hash" not in item
        assert "password" not in item
        assert "id" in item and "email" in item and "role" in item


def test_list_users_filter_role(client, admin_headers):
    r = client.get("/api/v1/users?role=driver", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(item["role"] == "driver" for item in body["items"])


def test_list_users_filter_active(client, admin_headers):
    r = client.get("/api/v1/users?is_active=true", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert all(item["is_active"] for item in body["items"])


def test_list_users_filter_q(client, admin_headers):
    r = client.get("/api/v1/users?q=admin", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    for item in body["items"]:
        assert "admin" in item["email"].lower() or "admin" in item["name"].lower()


def test_list_users_requires_auth(client):
    r = client.get("/api/v1/users")
    assert r.status_code == 401


def test_list_users_wrong_role(client, ops_headers):
    # ops may NOT manage users (only admin).
    r = client.get("/api/v1/users", headers=ops_headers)
    assert r.status_code == 403


# --- users: create ------------------------------------------------------------
def test_create_user_success(client, admin_headers):
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "newuser@erp.local",
        "name": "New User",
        "role": "ops",
        "password": "secret123",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "newuser@erp.local"
    assert body["role"] == "ops"
    assert "password_hash" not in body
    assert "password" not in body

    # Login should work with the new password.
    r = client.post("/api/v1/auth/login", json={
        "email": "newuser@erp.local",
        "password": "secret123",
    })
    assert r.status_code == 200


def test_create_user_invalid_role(client, admin_headers):
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "badrole@erp.local",
        "name": "Bad",
        "role": "superadmin",
        "password": "secret123",
    })
    assert r.status_code == 400
    assert "superadmin" in r.json()["detail"]


def test_create_user_duplicate_email(client, admin_headers):
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "admin@erp.local",
        "name": "Dup",
        "role": "admin",
        "password": "secret123",
    })
    assert r.status_code == 400


def test_create_user_requires_admin(client, ops_headers):
    r = client.post("/api/v1/users", headers=ops_headers, json={
        "email": "x@erp.local",
        "name": "X",
        "role": "ops",
        "password": "secret123",
    })
    assert r.status_code == 403


# --- users: get / patch / delete ---------------------------------------------
def test_get_user_not_found(client, admin_headers):
    r = client.get("/api/v1/users/nonexistent-id", headers=admin_headers)
    assert r.status_code == 404


def test_patch_user_rename_and_role(client, admin_headers):
    # Create a user to patch.
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "patchme@erp.local",
        "name": "Before",
        "role": "warehouse",
        "password": "pw12345",
    })
    user_id = r.json()["id"]

    r = client.patch(f"/api/v1/users/{user_id}", headers=admin_headers, json={
        "name": "After",
        "role": "finance",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "After"
    assert body["role"] == "finance"


def test_patch_user_password(client, admin_headers):
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "pwchange@erp.local",
        "name": "PW",
        "role": "driver",
        "password": "oldpw123",
    })
    user_id = r.json()["id"]

    r = client.patch(f"/api/v1/users/{user_id}", headers=admin_headers, json={
        "password": "newpw456",
    })
    assert r.status_code == 200

    # Old password fails.
    r = client.post("/api/v1/auth/login", json={
        "email": "pwchange@erp.local",
        "password": "oldpw123",
    })
    assert r.status_code == 401
    # New password works.
    r = client.post("/api/v1/auth/login", json={
        "email": "pwchange@erp.local",
        "password": "newpw456",
    })
    assert r.status_code == 200


def test_patch_user_invalid_role(client, admin_headers):
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "rolecheck@erp.local",
        "name": "RC",
        "role": "ops",
        "password": "pw12345",
    })
    user_id = r.json()["id"]

    r = client.patch(f"/api/v1/users/{user_id}", headers=admin_headers, json={
        "role": "ceo",
    })
    assert r.status_code == 400


def test_delete_user_soft(client, admin_headers):
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "delete@erp.local",
        "name": "Del",
        "role": "ops",
        "password": "pw12345",
    })
    user_id = r.json()["id"]

    r = client.delete(f"/api/v1/users/{user_id}", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["is_active"] is False
    assert body["id"] == user_id  # row still present (soft delete)

    # Login must fail now.
    r = client.post("/api/v1/auth/login", json={
        "email": "delete@erp.local",
        "password": "pw12345",
    })
    assert r.status_code == 401

    # GET user still returns the (deactivated) row.
    r = client.get(f"/api/v1/users/{user_id}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def test_delete_user_not_found(client, admin_headers):
    r = client.delete("/api/v1/users/nope-id", headers=admin_headers)
    assert r.status_code == 404


# --- audit logs ---------------------------------------------------------------
def test_audit_logs_paged(client, admin_headers):
    # Generate at least one audit entry (we already created users above, but
    # make a fresh one here to be safe).
    client.post("/api/v1/users", headers=admin_headers, json={
        "email": "auditgen@erp.local",
        "name": "Audit",
        "role": "ops",
        "password": "pw12345",
    })
    r = client.get("/api/v1/audit-logs", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert isinstance(body["items"], list)
    for item in body["items"]:
        assert "entity_type" in item and "action" in item and "created_at" in item
    # Newest first: timestamps should be non-increasing.
    timestamps = [item["created_at"] for item in body["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_audit_logs_filter_entity_type(client, admin_headers):
    client.post("/api/v1/users", headers=admin_headers, json={
        "email": "filterme@erp.local",
        "name": "Filter",
        "role": "ops",
        "password": "pw12345",
    })
    r = client.get("/api/v1/audit-logs?entity_type=User", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(item["entity_type"] == "User" for item in body["items"])


def test_audit_logs_filter_entity_id(client, admin_headers):
    # Create a user, grab its id, then filter audit-logs by that id.
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "eid@erp.local",
        "name": "EID",
        "role": "ops",
        "password": "pw12345",
    })
    user_id = r.json()["id"]
    r = client.get(f"/api/v1/audit-logs?entity_id={user_id}", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(item["entity_id"] == user_id for item in body["items"])


def test_audit_logs_ops_allowed(client, ops_headers):
    r = client.get("/api/v1/audit-logs", headers=ops_headers)
    assert r.status_code == 200


def test_audit_logs_driver_forbidden(client):
    # driver@erp.local has no access to audit logs.
    from tests.conftest import _auth_headers
    headers = _auth_headers("driver@erp.local")
    r = client.get("/api/v1/audit-logs", headers=headers)
    assert r.status_code == 403


# --- settings -----------------------------------------------------------------
def test_get_settings_admin(client, admin_headers):
    r = client.get("/api/v1/settings", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "auto_confirm" in body
    assert "cutoff_time" in body
    assert "auto_invoice" in body
    assert body["cutoff_time"] == "18:00"
    assert body["auto_confirm"]["enabled"] is True
    assert body["auto_confirm"]["min_confidence"] == 0.95


def test_get_settings_requires_admin(client, ops_headers):
    r = client.get("/api/v1/settings", headers=ops_headers)
    assert r.status_code == 403


def test_put_settings_partial(client, admin_headers):
    # Change only cutoff_time.
    r = client.put("/api/v1/settings", headers=admin_headers, json={
        "cutoff_time": "17:30",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["cutoff_time"] == "17:30"
    # Other keys untouched.
    assert "auto_confirm" in body and "auto_invoice" in body

    # Reset.
    client.put("/api/v1/settings", headers=admin_headers, json={
        "cutoff_time": "18:00",
    })


def test_put_settings_admin_only(client, ops_headers):
    r = client.put("/api/v1/settings", headers=ops_headers, json={
        "cutoff_time": "20:00",
    })
    assert r.status_code == 403


def test_put_settings_empty_rejected(client, admin_headers):
    r = client.put("/api/v1/settings", headers=admin_headers, json={})
    assert r.status_code == 400


# --- settings/public ----------------------------------------------------------
def test_settings_public_any_logged_in(client, admin_headers):
    r = client.get("/api/v1/settings/public", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    # Public subset only.
    assert "auto_confirm" in body
    assert "cutoff_time" in body
    # Must NOT include sensitive settings.
    assert "auto_invoice" not in body


def test_settings_public_requires_auth(client):
    r = client.get("/api/v1/settings/public")
    assert r.status_code == 401


def test_settings_public_driver_ok(client):
    from tests.conftest import _auth_headers
    headers = _auth_headers("driver@erp.local")
    r = client.get("/api/v1/settings/public", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "auto_confirm" in body
    assert "cutoff_time" in body
