"""Mandatory human review gate (settings.intake_require_human_review).

Business rule, stated by the customer verbally and non-negotiable:

    Every order is reviewed by a human before it goes anywhere, even when the
    extractor reports 100% confidence. A wrong order shipping is a disaster;
    the few seconds a person spends confirming one is not.

So the "OCR proposes, human disposes" boundary is widened from *risky*
documents (extractor flagged `requires_human_review`) to *all* documents.
Confidence never buys an auto-approval.

These tests are the ones that would fail if someone "optimised" the gate back
into a confidence threshold. They opt into the production rule via the
`require_review` fixture; the rest of the suite runs with it off.
"""
from __future__ import annotations

import time

from tests.conftest import admin_headers, client

DEMO_TEXT = "土豆 50斤\n大白菜 30斤\n五花肉 20斤\n大米 2袋"

_JOB_TIMEOUT = 10


class _PerfectExtractor:
    """The most confident extractor imaginable — and still not trusted."""

    def extract(self, *, source_type, raw_text=None, file_path=None, original_filename=None):
        from app.ai.adapters import ExtractionResult, RawLine

        return ExtractionResult(
            lines=[
                RawLine(product_name="土豆", quantity=50, unit="斤", cancelled=False),
                RawLine(product_name="大白菜", quantity=30, unit="斤", cancelled=False),
            ],
            doc_type="text",
            form_type="structured",
            form_type_confidence=1.0,
            overall_confidence=1.0,  # 100% confident on purpose
            requires_human_review=False,  # extractor says "ship it"
            parser_notes="simulated perfect extraction",
        )


def _wait_for_job(client, job_id: str, headers: dict, timeout: float = _JOB_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = client.get(f"/api/v1/intake/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, f"job get failed: {r.status_code} {r.text}"
        last = r.json()
        if last["status"] in ("completed", "failed", "needs_review"):
            return last
        time.sleep(0.05)
    return last


def _get_customer_id(client, headers: dict, code: str = "C001") -> str:
    r = client.get("/api/v1/customers", headers=headers)
    assert r.status_code == 200, r.text
    for c in r.json()["items"]:
        if c["code"] == code:
            return c["id"]
    raise AssertionError(f"Customer {code} not found in seed data")


def _count_orders() -> int:
    from app.core.database import SessionLocal
    from app.models import Order

    with SessionLocal() as db:
        return db.query(Order).count()


def test_production_default_requires_review():
    """The gate ships ON. Turning it off must be a deliberate act."""
    from app.core.config import Settings

    field = Settings.model_fields["intake_require_human_review"]
    assert field.default is True, (
        "intake_require_human_review must default to True — every order is "
        "human-reviewed before processing, no confidence threshold"
    )


def test_clean_text_stops_for_review(client, admin_headers, require_review):
    """A plain, boring, perfectly-parsed order still waits for a human."""
    customer_id = _get_customer_id(client, admin_headers)
    before = _count_orders()

    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=admin_headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    job = _wait_for_job(client, r.json()["job_id"], admin_headers)

    assert job["status"] == "needs_review", (
        f"expected needs_review, got {job['status']}: {job.get('error')}"
    )
    assert job["draft_order_id"] is None, "no order may exist before a human confirms"
    assert _count_orders() == before, "an Order row was created without review"

    # The parsed lines are available so the reviewer can see what was read.
    ext = client.get(
        f"/api/v1/intake/extractions/{job['id']}", headers=admin_headers
    ).json()
    assert len(ext["raw_output"]["lines"]) == 4


def test_full_confidence_does_not_bypass_the_gate(
    client, admin_headers, require_review, monkeypatch, pin_cutoff
):
    """100% confidence is exactly the case the customer was worried about."""
    monkeypatch.setattr("app.ai.pipeline.get_extractor", lambda: _PerfectExtractor())
    pin_cutoff(False)
    customer_id = _get_customer_id(client, admin_headers)
    before = _count_orders()

    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=admin_headers,
    )
    assert r.status_code == 201
    job = _wait_for_job(client, r.json()["job_id"], admin_headers)

    assert job["status"] == "needs_review", (
        "a 1.0-confidence extraction must still stop for review"
    )
    assert _count_orders() == before

    # The reviewer's confirm is what actually creates it.
    r2 = client.post(
        f"/api/v1/intake/jobs/{job['id']}/confirm-review", headers=admin_headers
    )
    assert r2.status_code == 200, f"{r2.status_code} {r2.text}"
    assert _count_orders() == before + 1


def test_confirm_creates_order_then_completes_job(client, admin_headers, require_review, pin_cutoff):
    """Confirm-review is the only path from extraction to Order."""
    pin_cutoff(False)
    customer_id = _get_customer_id(client, admin_headers)

    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=admin_headers,
    )
    job_id = r.json()["job_id"]
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "needs_review"

    r2 = client.post(f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers)
    assert r2.status_code == 200, f"{r2.status_code} {r2.text}"
    body = r2.json()
    assert body["order_id"], "confirm must return the created order id"
    assert body["status"] == "draft"

    job2 = client.get(f"/api/v1/intake/jobs/{job_id}", headers=admin_headers).json()
    assert job2["status"] == "completed"
    assert job2["draft_order_id"] == body["order_id"]

    order = client.get(f"/api/v1/orders/{body['order_id']}", headers=admin_headers)
    if order.status_code == 200:
        assert len(order.json()["lines"]) == 4


def test_confirm_twice_is_rejected(client, admin_headers, require_review):
    """A job can only be confirmed once — no double orders from a double click."""
    customer_id = _get_customer_id(client, admin_headers)

    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=admin_headers,
    )
    job_id = r.json()["job_id"]
    _wait_for_job(client, job_id, admin_headers)

    ok = client.post(f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers)
    assert ok.status_code == 200

    again = client.post(f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers)
    assert again.status_code == 400, "re-confirming a completed job must fail"


# ---------------------------------------------------------------------------
# Push notification to the ops group
# ---------------------------------------------------------------------------

def test_review_push_sent_to_ops_group(client, admin_headers, require_review, monkeypatch):
    """Parking an order taps the ops group on the shoulder."""
    import app.services.notify.wecom_notify as wn

    calls = []
    monkeypatch.setattr(wn, "notify", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(wn.settings, "wecom_ops_chat_id", "ops-chat-123")

    customer_id = _get_customer_id(client, admin_headers)
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=admin_headers,
    )
    job = _wait_for_job(client, r.json()["job_id"], admin_headers)
    assert job["status"] == "needs_review"

    assert len(calls) == 1, f"expected one ops push, got {calls}"
    kw = calls[0]
    assert kw["template"] == "intake_needs_review"
    assert kw["chat_id"] == "ops-chat-123"
    assert kw["customer_id"] is None, "an internal alert must not target a customer"
    assert kw["payload"]["job_id"] == job["id"]


def test_review_push_skipped_when_unconfigured(client, admin_headers, require_review, monkeypatch):
    """No WECOM_OPS_CHAT_ID → no push, but the order is still queued."""
    import app.services.notify.wecom_notify as wn

    calls = []
    monkeypatch.setattr(wn, "notify", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(wn.settings, "wecom_ops_chat_id", "")

    customer_id = _get_customer_id(client, admin_headers)
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=admin_headers,
    )
    job = _wait_for_job(client, r.json()["job_id"], admin_headers)

    assert job["status"] == "needs_review", "order is still queued, just not announced"
    assert calls == [], "unconfigured chat id must not attempt a send"


# ---------------------------------------------------------------------------
# The inbox's "unread" signal: which rows are still waiting
# ---------------------------------------------------------------------------

def _submit(client, headers, customer_id: str, text: str = DEMO_TEXT) -> str:
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": text},
        headers=headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    return r.json()["job_id"]


def test_review_count_counts_unreviewed_only(client, admin_headers, require_review):
    """The nav badge shows how many orders still need a human."""
    customer_id = _get_customer_id(client, admin_headers)

    base = client.get("/api/v1/intake/review-count", headers=admin_headers).json()
    assert "pending_review" in base
    before = base["pending_review"]

    job_id = _submit(client, admin_headers, customer_id)
    _wait_for_job(client, job_id, admin_headers)

    after = client.get("/api/v1/intake/review-count", headers=admin_headers).json()
    assert after["pending_review"] == before + 1, "parked order must be counted"

    client.post(f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers)

    settled = client.get("/api/v1/intake/review-count", headers=admin_headers).json()
    assert settled["pending_review"] == before, "confirmed order must drop off the badge"


def test_documents_status_filter_works(client, admin_headers, require_review):
    """The UI sends ?status=... — it must not be silently ignored."""
    customer_id = _get_customer_id(client, admin_headers)
    job_id = _submit(client, admin_headers, customer_id, "土豆 7斤")
    _wait_for_job(client, job_id, admin_headers)

    r = client.get(
        "/api/v1/intake/documents?status=needs_review", headers=admin_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert body["items"], "status filter returned nothing"
    for d in body["items"]:
        assert d["job_status"] == "needs_review", "filter leaked a non-pending row"

    # A status nothing matches → empty page, total 0 (not "everything").
    r2 = client.get(
        "/api/v1/intake/documents?status=failed", headers=admin_headers
    )
    assert r2.json()["total"] == 0


def test_pending_first_floats_unreviewed_to_top(client, admin_headers, require_review):
    """A reviewer opening the inbox sees the queue, not yesterday's orders."""
    customer_id = _get_customer_id(client, admin_headers)

    # An already-confirmed order (created earlier in this test).
    done = _submit(client, admin_headers, customer_id, "土豆 1斤")
    _wait_for_job(client, done, admin_headers)
    client.post(f"/api/v1/intake/jobs/{done}/confirm-review", headers=admin_headers)

    # A freshly parked one.
    pending = _submit(client, admin_headers, customer_id, "土豆 2斤")
    _wait_for_job(client, pending, admin_headers)

    r = client.get(
        "/api/v1/intake/documents?pending_first=true", headers=admin_headers
    )
    items = r.json()["items"]
    statuses = [d["job_status"] for d in items]
    first_done = next(i for i, s in enumerate(statuses) if s != "needs_review")
    assert not any(
        s == "needs_review" for s in statuses[first_done:]
    ), f"unreviewed row sorted below a settled one: {statuses}"


# ---------------------------------------------------------------------------
# No order is ever processed without a human confirming it — including
# overnight, and including at "confident enough" confidence.
# ---------------------------------------------------------------------------

def _submit_and_confirm_review(client, headers, customer_id) -> str:
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=headers,
    )
    job = _wait_for_job(client, r.json()["job_id"], headers)
    assert job["status"] == "needs_review"
    r2 = client.post(f"/api/v1/intake/jobs/{job['id']}/confirm-review", headers=headers)
    assert r2.status_code == 200, r2.text
    return r2.json()["order_id"]


def test_order_is_not_confirmed_just_because_the_human_okd_the_extraction(
    client, admin_headers, require_review, require_human_confirmation, pin_cutoff
):
    """The regression this guards: auto-confirm settled the order itself.

    A human approved the *reading* of the order. Nobody approved the order.
    Auto-confirm used to flip it to "confirmed" with confirmed_by=None and
    lock contract prices, all without another human action.
    """
    # Even with the clock well before the cutoff and auto-confirm switched on
    # in the settings table, the order must not settle on its own.
    pin_cutoff(True)
    r = client.put(
        "/api/v1/settings",
        json={
            "auto_confirm": {"enabled": True, "min_confidence": 0.95},
            "cutoff_time": "23:59",
        },
        headers=admin_headers,
    )
    # Guard against this test quietly going vacuous: if the write fails we are
    # no longer proving that auto-confirm gets overridden.
    assert r.status_code == 200, f"could not enable auto_confirm: {r.text}"

    try:
        customer_id = _get_customer_id(client, admin_headers)
        order_id = _submit_and_confirm_review(client, admin_headers, customer_id)

        order = client.get(f"/api/v1/orders/{order_id}", headers=admin_headers).json()
        assert order["status"] == "draft", (
            f"order settled without a human confirming it: {order['status']}"
        )
        assert not order.get("confirmed_by"), "no person confirmed this order"
        assert not order.get("confirmed_at"), "no person confirmed this order"
    finally:
        # Leave the seed defaults for the rest of the suite.
        client.put(
            "/api/v1/settings",
            json={
                "auto_confirm": {"enabled": True, "min_confidence": 0.95},
                "cutoff_time": "18:00",
            },
            headers=admin_headers,
        )


def test_overnight_order_waits_for_a_human(
    client, admin_headers, require_review, require_human_confirmation, monkeypatch
):
    """3am, nobody at a desk: the order sits. It does not process itself."""
    import app.services.orders.auto_confirm as auto_confirm

    # Pretend it is 03:00 — before the 18:00 cutoff, which is exactly when
    # auto-confirm used to fire.
    monkeypatch.setattr(auto_confirm, "_before_cutoff", lambda cutoff: True)

    customer_id = _get_customer_id(client, admin_headers)
    order_id = _submit_and_confirm_review(client, admin_headers, customer_id)

    order = client.get(f"/api/v1/orders/{order_id}", headers=admin_headers).json()
    assert order["status"] == "draft"
    assert not order.get("confirmed_at")

    # When a human does confirm it, it settles.
    r = client.post(f"/api/v1/orders/{order_id}/confirm", json={}, headers=admin_headers)
    assert r.status_code == 200, r.text
    settled = client.get(f"/api/v1/orders/{order_id}", headers=admin_headers).json()
    assert settled["status"] == "confirmed"
    assert settled.get("confirmed_by"), "a real person must be recorded as the confirmer"


def test_production_default_requires_human_order_confirmation():
    """Like the intake gate, this rule ships ON."""
    from app.core.config import Settings

    field = Settings.model_fields["orders_require_human_confirmation"]
    assert field.default is True, (
        "orders_require_human_confirmation must default to True — no order is "
        "processed without a human confirming it"
    )


def test_customer_is_not_messaged_before_a_human_confirms(
    client, admin_headers, require_review, require_human_confirmation, monkeypatch
):
    """No automatic WeCom chatter: the customer hears from us once, after
    a person has approved the order."""
    import app.services.notify.wecom_notify as wn

    calls = []
    monkeypatch.setattr(wn, "notify", lambda *a, **kw: calls.append(kw))

    customer_id = _get_customer_id(client, admin_headers)
    order_id = _submit_and_confirm_review(client, admin_headers, customer_id)

    templates = [c.get("template") for c in calls]
    assert "needs_customer_confirm" not in templates, (
        f"customer was asked to confirm before anyone approved: {templates}"
    )


# --- The confirmation queue has to be countable, or nobody knows it exists ---


def test_confirm_count_returns_a_number_not_a_404(client, admin_headers):
    """`GET /orders/confirm-count` must not be eaten by `/orders/{order_id}`.

    Route order matters: if `/{order_id}` is registered first, "confirm-count"
    is parsed as an order id and the nav badge silently reads zero forever.
    """
    r = client.get("/api/v1/orders/confirm-count", headers=admin_headers)
    assert r.status_code == 200, f"confirm-count broken: {r.status_code} {r.text}"
    assert "awaiting_confirmation" in r.json()
    assert r.json()["awaiting_confirmation"] >= 0


def test_confirm_count_matches_orders_waiting_on_a_person(
    client, admin_headers, require_review, require_human_confirmation
):
    """The badge counts exactly the orders a human still has to confirm."""
    from app.core.database import SessionLocal
    from app.services.orders.orders import CONFIRMABLE

    before = client.get("/api/v1/orders/confirm-count", headers=admin_headers).json()
    before_n = before["awaiting_confirmation"]

    customer_id = _get_customer_id(client, admin_headers)
    order_id = _submit_and_confirm_review(client, admin_headers, customer_id)

    after = client.get("/api/v1/orders/confirm-count", headers=admin_headers).json()
    assert after["awaiting_confirmation"] == before_n + 1, (
        f"draft order did not appear in the queue: {before_n} -> {after['awaiting_confirmation']}"
    )

    # Confirming it removes it from the queue — the badge must go back down,
    # otherwise operators learn to ignore it.
    r = client.post(f"/api/v1/orders/{order_id}/confirm", json={}, headers=admin_headers)
    assert r.status_code == 200, r.text

    settled = client.get("/api/v1/orders/confirm-count", headers=admin_headers).json()
    assert settled["awaiting_confirmation"] == before_n

    with SessionLocal() as db:
        from app.models import Order

        assert db.get(Order, order_id).status == "confirmed"
        assert "confirmed" not in CONFIRMABLE


def test_statuses_filter_returns_the_whole_confirmation_queue(
    client, admin_headers, require_review, require_human_confirmation
):
    """`statuses=draft,pending_confirmation,needs_clarification` in one call.

    "Waiting on a human" spans three statuses. Without a multi-status filter
    the UI would need three requests and could not sort the queue as one list.
    """
    customer_id = _get_customer_id(client, admin_headers)
    order_id = _submit_and_confirm_review(client, admin_headers, customer_id)

    statuses = "draft,pending_confirmation,needs_clarification"
    r = client.get(
        "/api/v1/orders",
        params={"statuses": statuses, "page": 1, "page_size": 100},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [o["id"] for o in body["items"]]
    assert order_id in ids, "draft order missing from the confirmation queue"
    assert all(o["status"] in statuses.split(",") for o in body["items"]), (
        f"filter leaked non-queued statuses: {[o['status'] for o in body['items']]}"
    )

    # The single-status filter still works (backwards compatibility).
    r2 = client.get(
        "/api/v1/orders",
        params={"status": "draft", "page": 1, "page_size": 100},
        headers=admin_headers,
    )
    assert r2.status_code == 200, r2.text
    assert all(o["status"] == "draft" for o in r2.json()["items"])


def test_awaiting_first_floats_unconfirmed_orders_to_the_top(
    client, admin_headers, require_review, require_human_confirmation
):
    """An operator opening Orders sees the queue first, not the settled bulk."""
    from app.core.database import SessionLocal
    from app.models import Order

    customer_id = _get_customer_id(client, admin_headers)
    draft_id = _submit_and_confirm_review(client, admin_headers, customer_id)
    confirmed_id = _submit_and_confirm_review(client, admin_headers, customer_id)
    r = client.post(f"/api/v1/orders/{confirmed_id}/confirm", json={}, headers=admin_headers)
    assert r.status_code == 200, r.text

    with SessionLocal() as db:
        # Force the confirmed row to be newer, so created_at desc alone would
        # sort it ABOVE the draft. Only awaiting_first can save us here.
        o = db.get(Order, confirmed_id)
        d = db.get(Order, draft_id)
        o.created_at, d.created_at = d.created_at, o.created_at
        db.commit()

    r = client.get(
        "/api/v1/orders",
        params={"awaiting_first": True, "page": 1, "page_size": 100},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    statuses = [o["status"] for o in r.json()["items"]]
    first_confirmed = next((i for i, s in enumerate(statuses) if s == "confirmed"), len(statuses))
    last_draft = max((i for i, s in enumerate(statuses) if s == "draft"), default=-1)
    assert last_draft < first_confirmed, (
        f"an unconfirmed order sorted below a settled one: {statuses}"
    )
