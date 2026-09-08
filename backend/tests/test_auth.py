"""Tests for the auth module (auth/system agent).

Covers: login success/failure, inactive user 401, /me, change-password
(success + wrong old password), and that user responses never leak password_hash.
"""
from __future__ import annotations

from tests.conftest import admin_headers, client


def test_login_success(client):
    r = client.post("/api/v1/auth/login", json={
        "email": "admin@erp.local",
        "password": "erp123",
    })
    assert r.status_code == 200
    body = r.json()
    assert "token" in body and isinstance(body["token"], str) and body["token"]
    assert body["user"]["email"] == "admin@erp.local"
    assert body["user"]["role"] == "admin"
    assert body["user"]["is_active"] is True
    assert "password_hash" not in body["user"]
    assert "password" not in body["user"]


def test_login_wrong_password(client):
    r = client.post("/api/v1/auth/login", json={
        "email": "admin@erp.local",
        "password": "wrong",
    })
    assert r.status_code == 401


def test_login_unknown_email(client):
    r = client.post("/api/v1/auth/login", json={
        "email": "nobody@erp.local",
        "password": "whatever",
    })
    assert r.status_code == 401


def test_login_inactive_user_blocked(client, admin_headers):
    # Create a fresh user, deactivate them via the system endpoint, then
    # attempt login — should be 401. (Avoids touching the shared admin user
    # because admin_headers is session-scoped across tests.)
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "inactive@erp.local",
        "name": "Inactive",
        "role": "ops",
        "password": "erp123",
    })
    assert r.status_code == 201
    user_id = r.json()["id"]

    r = client.delete(f"/api/v1/users/{user_id}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    r = client.post("/api/v1/auth/login", json={
        "email": "inactive@erp.local",
        "password": "erp123",
    })
    assert r.status_code == 401


def test_me_requires_auth(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_me_returns_current_user(client, admin_headers):
    r = client.get("/api/v1/auth/me", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "admin@erp.local"
    assert body["role"] == "admin"
    assert "password_hash" not in body
    assert "password" not in body


def test_change_password_success(client, admin_headers):
    r = client.post("/api/v1/auth/change-password", headers=admin_headers, json={
        "old_password": "erp123",
        "new_password": "newpass456",
    })
    assert r.status_code == 204

    # Old password no longer works.
    r = client.post("/api/v1/auth/login", json={
        "email": "admin@erp.local",
        "password": "erp123",
    })
    assert r.status_code == 401

    # New password works.
    r = client.post("/api/v1/auth/login", json={
        "email": "admin@erp.local",
        "password": "newpass456",
    })
    assert r.status_code == 200

    # Reset back so other session-scoped fixtures keep working.
    client.post("/api/v1/auth/change-password", headers=admin_headers, json={
        "old_password": "newpass456",
        "new_password": "erp123",
    })


def test_change_password_wrong_old(client, admin_headers):
    r = client.post("/api/v1/auth/change-password", headers=admin_headers, json={
        "old_password": "totally-wrong",
        "new_password": "anything",
    })
    assert r.status_code == 400
