"""Tests for the ERP half of the WeCom handoff (docs/WECOM_CONTRACTS.md §6).

These cover the paths the Gateway depends on and that previously shipped
broken with zero test coverage:

* idempotency on `msgid` (a gateway retry must NOT create a second order)
* reply-to-parent linking (a reply must inherit its parent's customer)
* auth (service key must work; endpoints must not be wide open)
* the lookup-customer endpoint that drives the auto-bind cascade
"""
from __future__ import annotations

import pytest

from tests.conftest import admin_headers, client  # noqa: F401

SERVICE_HEADERS = {"X-ERP-Service-Key": "dev-service-key"}


def _customer_id(code: str = "C001") -> str:
    from app.core.database import SessionLocal
    from app.models import Customer

    with SessionLocal() as db:
        return db.query(Customer).filter(Customer.code == code).one().id


def _post(client, body: dict, headers: dict | None = None):  # noqa: F811
    return client.post(
        "/api/v1/intake/wecom", json=body, headers=headers or SERVICE_HEADERS
    )


# ---------------------------------------------------------------------------
# Idempotency — the single most important guarantee in §6
# ---------------------------------------------------------------------------


def test_wecom_intake_is_idempotent_on_msgid(client):  # noqa: F811
    """A gateway retry must reuse the first document, never create a second.

    Regression guard: the idempotency lookup used `cast(meta['wecom']['msgid'],
    String)`, which on SQLite compiles to `CAST(JSON_QUOTE(JSON_EXTRACT(...)))`.
    JSON_QUOTE wraps the value in literal double quotes, so `"MSG1" = 'MSG1'`
    never matched → every retry silently created a duplicate order.
    """
    customer_id = _customer_id("C001")
    body = {
        "msgid": "wm-idem-0001",
        "msgtype": "text",
        "content": "土豆 50斤\n白菜 30斤",
        "customer_id": customer_id,
        "external_userid": "wmExtCanteen001",
    }
    first = _post(client, body)
    assert first.status_code == 201, first.text
    assert first.json()["duplicate"] is False

    retry = _post(client, body)
    assert retry.status_code == 200, retry.text
    payload = retry.json()
    assert payload["duplicate"] is True
    # Same document AND same job — nothing was re-created.
    assert payload["document_id"] == first.json()["document_id"]
    assert payload["job_id"] == first.json()["job_id"]


def test_wecom_intake_accepts_idempotency_key_header(client):  # noqa: F811
    """§6: the idempotency key may arrive as a header instead of in the body."""
    customer_id = _customer_id("C001")
    body = {"msgtype": "text", "content": "土豆 10斤", "customer_id": customer_id}
    r = client.post(
        "/api/v1/intake/wecom",
        json=body,
        headers={**SERVICE_HEADERS, "Idempotency-Key": "wm-header-key-0001"},
    )
    assert r.status_code == 201, r.text
    again = client.post(
        "/api/v1/intake/wecom",
        json=body,
        headers={**SERVICE_HEADERS, "Idempotency-Key": "wm-header-key-0001"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["duplicate"] is True


def test_wecom_intake_requires_msgid(client):  # noqa: F811
    r = _post(client, {"msgtype": "text", "content": "土豆 10斤"})
    assert r.status_code == 400, r.text


def test_wecom_intake_rejects_anonymous_caller(client):  # noqa: F811
    r = client.post(
        "/api/v1/intake/wecom",
        json={"msgid": "wm-anon", "msgtype": "text", "content": "hi"},
    )
    assert r.status_code in (401, 403), r.text


# ---------------------------------------------------------------------------
# Reply loop (§6 / DoD 8)
# ---------------------------------------------------------------------------


def test_reply_links_to_parent_and_inherits_customer(client):  # noqa: F811
    """A reply with `reply_to_msgid` must resolve its parent and inherit
    that parent's customer, so it does not land as an unresolved new order."""
    customer_id = _customer_id("C001")
    parent = _post(
        client,
        {
            "msgid": "wm-parent-0001",
            "msgtype": "text",
            "content": "土豆 50斤",
            "customer_id": customer_id,
        },
    )
    assert parent.status_code == 201, parent.text
    parent_doc = parent.json()["document_id"]

    reply = client.post(
        "/api/v1/intake/wecom/reply",
        json={
            "msgid": "wm-reply-0001",
            "msgtype": "text",
            "content": "改成 60斤",
            "reply_to_msgid": "wm-parent-0001",
        },
        headers=SERVICE_HEADERS,
    )
    assert reply.status_code == 201, reply.text
    body = reply.json()
    assert body["parent_document_id"] == parent_doc
    # Inherited from the parent rather than left unresolved.
    assert body["customer_id"] == customer_id


def test_reply_with_unknown_parent_still_ingests(client):  # noqa: F811
    """An unresolvable parent must not drop the reply — fall back to a
    normal ingest so the message is never lost."""
    reply = client.post(
        "/api/v1/intake/wecom/reply",
        json={
            "msgid": "wm-reply-orphan",
            "msgtype": "text",
            "content": "土豆 20斤",
            "reply_to_msgid": "wm-does-not-exist",
        },
        headers=SERVICE_HEADERS,
    )
    assert reply.status_code == 201, reply.text
    assert reply.json()["parent_document_id"] is None


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_wecom_provenance_is_written_to_document_meta(client):  # noqa: F811
    customer_id = _customer_id("C002")
    r = _post(
        client,
        {
            "msgid": "wm-meta-0001",
            "msgtype": "image",
            "content": None,
            "customer_id": customer_id,
            "external_userid": "wmExtHotel002",
            "chat_id": "wrCanteenGroup001",
            "received_at": "2026-09-08T10:00:00+00:00",
        },
    )
    assert r.status_code == 201, r.text
    from app.core.database import SessionLocal
    from app.models import IntakeDocument

    with SessionLocal() as db:
        doc = db.get(IntakeDocument, r.json()["document_id"])
        wecom = (doc.document_meta or {}).get("wecom") or {}
    assert wecom["msgid"] == "wm-meta-0001"
    assert wecom["external_userid"] == "wmExtHotel002"
    assert wecom["chat_id"] == "wrCanteenGroup001"
    assert wecom["received_at"] == "2026-09-08T10:00:00+00:00"


def test_wecom_messages_list_contains_the_message(client):  # noqa: F811
    # Depends on the previous test having created wm-meta-0001; create it
    # here too so the test stands alone.
    _post(
        client,
        {
            "msgid": "wm-meta-0001",
            "msgtype": "text",
            "content": "土豆 5斤",
            "customer_id": _customer_id("C002"),
        },
    )
    r = client.get("/api/v1/intake/wecom-messages", headers=SERVICE_HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert any(
        (i.get("wecom") or {}).get("msgid") == "wm-meta-0001" for i in items
    )


# ---------------------------------------------------------------------------
# lookup-customer — drives the Gateway's auto-bind cascade (§5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params,expected_code",
    [
        ({"code": "C001"}, "C001"),
        ({"phone": "13800000002"}, "C002"),
        ({"code": "C003"}, "C003"),
    ],
)
def test_lookup_customer_resolves_by_code_and_phone(
    client, params, expected_code  # noqa: F811
):
    r = client.get(
        "/api/v1/intake/wecom/lookup-customer",
        params=params,
        headers=SERVICE_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert body["customer"]["code"] == expected_code
    assert body["customer"]["id"]


def test_lookup_customer_returns_found_false_for_unknown(client):  # noqa: F811
    r = client.get(
        "/api/v1/intake/wecom/lookup-customer",
        params={"code": "NOPE-999"},
        headers=SERVICE_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"found": False, "customer": None}


def test_lookup_customer_requires_a_parameter(client):  # noqa: F811
    r = client.get(
        "/api/v1/intake/wecom/lookup-customer", headers=SERVICE_HEADERS
    )
    assert r.status_code == 400, r.text


def test_admin_jwt_also_accepted_on_wecom_endpoints(  # noqa: F811
    client, admin_headers
):
    """Service key is not the only way in — an ops/admin JWT works too."""
    r = client.get(
        "/api/v1/intake/wecom/lookup-customer",
        params={"code": "C001"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["found"] is True
