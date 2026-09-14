"""Gate 1 Phase 2 — enforcement actually parks non-orders.

In shadow mode triage only *records* a verdict; every message still becomes an
intake document, so the inbox is still flooded. This is the part that stops
the flooding.

The safety rule that matters most here: **only Tier 0 verdicts are parked.**
Tier 0 is deterministic (empty, emoji-only, or a message that is nothing but a
greeting/ack/system event). Tier 1 "not_order" is a heuristic score and stays
ingested, because dropping a real order costs far more than an extra row.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.conftest import admin_headers, client


def _customer_id(code: str = "C001") -> str:
    """Bind every posted message to a real customer.

    Without it the pipeline cannot create a draft order (`orders.customer_id`
    is NOT NULL) and the job ends "failed" for a reason that has nothing to do
    with triage — which would make the "still ingested" assertions below pass
    vacuously.
    """
    from app.core.database import SessionLocal
    from app.models import Customer

    with SessionLocal() as db:
        return db.query(Customer).filter(Customer.code == code).one().id


def _post(client, headers, content: str, msgid: str, **extra):
    payload = {
        "msgid": msgid,
        "msgtype": "text",
        "content": content,
        "source_type": "text",
        "customer_id": _customer_id(),
        **extra,
    }
    return client.post("/api/v1/intake/wecom", json=payload, headers=headers)


def _job_for(client, headers, job_id: str) -> dict:
    r = client.get(f"/api/v1/intake/jobs/{job_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def enforce(monkeypatch):
    """Turn enforcement on for the duration of one test."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "intake_triage_mode", "enforce")
    return None


# --- Shadow mode is still the default ---------------------------------------


def test_shadow_mode_ingests_chatter(client, admin_headers):
    """Default behaviour is unchanged: chatter still lands in the inbox."""
    r = _post(client, admin_headers, "你好", "shadow-1")
    assert r.status_code == 201, r.text
    job = _job_for(client, admin_headers, r.json()["job_id"])
    assert job["status"] in ("queued", "processing", "completed", "needs_review")


# --- Enforcement --------------------------------------------------------------


def test_enforce_parks_a_greeting(client, admin_headers, enforce):
    """'你好' on its own is not an order and must not enter the inbox."""
    r = _post(client, admin_headers, "你好", "enf-1")
    assert r.status_code == 201, r.text
    job = _job_for(client, admin_headers, r.json()["job_id"])
    assert job["status"] == "parked"


def test_enforce_parks_an_acknowledgement(client, admin_headers, enforce):
    r = _post(client, admin_headers, "收到", "enf-2")
    assert r.status_code == 201, r.text
    job = _job_for(client, admin_headers, r.json()["job_id"])
    assert job["status"] == "parked"


def test_enforce_parks_emoji_only(client, admin_headers, enforce):
    r = _post(client, admin_headers, "👍👍", "enf-3")
    assert r.status_code == 201, r.text
    job = _job_for(client, admin_headers, r.json()["job_id"])
    assert job["status"] == "parked"


def test_enforce_never_parks_a_real_order(client, admin_headers, enforce):
    """The regression that would cost real money."""
    r = _post(client, admin_headers, "土豆 50斤\n大白菜 30斤", "enf-4")
    assert r.status_code == 201, r.text
    job = _job_for(client, admin_headers, r.json()["job_id"])
    assert job["status"] != "parked", "a real order was parked by triage"


def test_enforce_never_parks_an_attachment(client, admin_headers, enforce):
    """A photo of a handwritten list has no text — it is still an order."""
    # tempfile, not the pytest `tmp_path` fixture: this sandbox refuses to
    # create pytest's basetemp root, so `tmp_path` errors during setup.
    with tempfile.TemporaryDirectory(prefix="triage_att_") as d:
        f = Path(d) / "order.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        r = _post(
            client, admin_headers, "", "enf-5",
            msgtype="image", file_path=str(f),
        )
    assert r.status_code == 201, r.text
    job = _job_for(client, admin_headers, r.json()["job_id"])
    assert job["status"] != "parked", "an image order was parked by triage"


def test_enforce_does_not_park_tier1_not_order(client, admin_headers, enforce):
    """Tier 1 is a heuristic — too risky to park, so it still gets ingested.

    A bare product name with no quantity scores 0 → "not_order" at tier1.
    That could be a badly-written order someone needs to chase up, so it must
    stay visible rather than being hidden by a heuristic.
    """
    r = _post(client, admin_headers, "土豆", "enf-6")
    assert r.status_code == 201, r.text
    job = _job_for(client, admin_headers, r.json()["job_id"])
    assert job["status"] != "parked", "tier1 (heuristic) verdict was parked"


def test_parked_messages_are_hidden_from_the_inbox(client, admin_headers, enforce):
    """Parking that still shows the row has achieved nothing."""
    _post(client, admin_headers, "你好", "hide-1")

    r = client.get("/api/v1/intake/documents", params={"page_size": 100}, headers=admin_headers)
    assert r.status_code == 200, r.text
    statuses = [d.get("job_status") for d in r.json()["items"]]
    assert "parked" not in statuses, f"parked message leaked into the inbox: {statuses}"

    # ...but they are still reachable when you go looking for them.
    r2 = client.get(
        "/api/v1/intake/documents",
        params={"status": "parked", "page_size": 100},
        headers=admin_headers,
    )
    assert r2.status_code == 200, r2.text
    assert any(d.get("job_status") == "parked" for d in r2.json()["items"]), (
        "parked messages cannot be audited — a wrong park would be unrecoverable"
    )


# --- Promote: a wrong park is recoverable ------------------------------------


def test_promote_puts_a_parked_message_back_in_the_queue(client, admin_headers, enforce):
    r = _post(client, admin_headers, "你好", "promo-1")
    job_id = r.json()["job_id"]
    assert _job_for(client, admin_headers, job_id)["status"] == "parked"

    p = client.post(f"/api/v1/intake/jobs/{job_id}/promote", headers=admin_headers)
    assert p.status_code == 200, p.text
    assert p.json()["status"] == "queued"


def test_promote_refuses_a_job_that_is_not_parked(client, admin_headers, enforce):
    r = _post(client, admin_headers, "土豆 50斤", "promo-2")
    job_id = r.json()["job_id"]
    p = client.post(f"/api/v1/intake/jobs/{job_id}/promote", headers=admin_headers)
    assert p.status_code == 400, "promoting a live job should be rejected"


def test_promote_records_the_human_override(client, admin_headers, enforce):
    """The audit trail must show a person disagreed with the classifier."""
    r = _post(client, admin_headers, "你好", "promo-3")
    job_id = r.json()["job_id"]

    def parked_count() -> int:
        rep = client.get("/api/v1/intake/wecom/triage-report", headers=admin_headers)
        assert rep.status_code == 200, rep.text
        return rep.json()["actually_parked_count"]

    # The DB is shared across the whole module, so an absolute count also
    # counts every other test's parked rows. Only the delta is meaningful.
    before = parked_count()
    p = client.post(f"/api/v1/intake/jobs/{job_id}/promote", headers=admin_headers)
    assert p.status_code == 200, p.text
    after = parked_count()
    # Promoted means it is no longer counted as parked — the only way the
    # report can drop is by reading the `overridden` flag the promote wrote.
    assert after == before - 1


# --- Counts -------------------------------------------------------------------


def test_review_count_reports_parked_separately(client, admin_headers, enforce):
    """`parked` is not `pending_review` — the two must never be conflated."""
    _post(client, admin_headers, "你好", "count-1")
    r = client.get("/api/v1/intake/review-count", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "parked" in body
    assert body["parked"] >= 1
    assert "pending_review" in body
