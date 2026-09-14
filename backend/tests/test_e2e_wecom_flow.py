"""End-to-end: a WeCom message becomes a confirmed ERP order — gates ON.

The unit suites each prove one gate in isolation, with the *other* gates
switched off by the autouse fixtures in `tests/conftest.py`. Nobody had ever
driven the whole journey with production settings, which is exactly the
configuration that ships:

    intake_require_human_review      = True  (Gate 2: a person reads it)
    orders_require_human_confirmation = True  (Gate 3: a person approves it)
    intake_triage_mode                = "enforce" (Gate 1: chatter is parked)

These tests opt into all three (`require_review`, `require_human_confirmation`,
`enforce`) and assert every hop: message → job → review → draft → confirm.
The one assertion that matters most is #5: approving the *extraction* is not
approving the *order*.
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.core.database import SessionLocal
from app.models import IntakeDocument, Order, User

from tests.conftest import admin_headers, client
from tests.test_mandatory_review import _get_customer_id, _wait_for_job

# The Gateway authenticates with the shared service key; that is how messages
# really arrive, so that is how these tests post them.
SERVICE_HEADERS = {"X-ERP-Service-Key": "dev-service-key"}

ORDER_TEXT = "土豆 50斤\n大白菜 30斤"


@pytest.fixture
def enforce(monkeypatch):
    """Gate 1 in enforcement mode (local copy of tests/test_triage_parking.py's
    fixture — importing that module would re-collect its tests here)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "intake_triage_mode", "enforce")


# --- helpers -----------------------------------------------------------------

def _msgid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _post_wecom(client, *, msgid: str, content: str | None, customer_id: str, **extra):
    """One message through the real Gateway handoff endpoint."""
    payload = {
        "msgid": msgid,
        "msgtype": "text",
        "content": content,
        "customer_id": customer_id,
        **extra,
    }
    return client.post("/api/v1/intake/wecom", json=payload, headers=SERVICE_HEADERS)


def _count_orders() -> int:
    with SessionLocal() as db:
        return db.query(Order).count()


def _admin_user_id() -> str:
    with SessionLocal() as db:
        return db.query(User).filter(User.email == "admin@erp.local").one().id


def _is_real_user(user_id: str | None) -> bool:
    if not user_id:
        return False
    with SessionLocal() as db:
        return db.get(User, user_id) is not None


def _triage_verdict(document_id: str) -> dict:
    with SessionLocal() as db:
        doc = db.get(IntakeDocument, document_id)
        return ((doc.document_meta or {}).get("wecom") or {}).get("triage") or {}


def _order(client, order_id: str, headers: dict) -> dict:
    r = client.get(f"/api/v1/orders/{order_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- 1. The happy path, end to end, with every gate ON ------------------------

def test_wecom_text_order_survives_the_whole_chain(
    client, admin_headers, require_review, require_human_confirmation, pin_cutoff
):
    """土豆 50斤 / 大白菜 30斤 → job → human review → draft → human confirm.

    Every hop is asserted, because a silent skip at any one of them is exactly
    how a wrong order ends up shipping.
    """
    # The clock is pinned *inside* the auto-confirm window and auto-confirm is
    # left as the seed data has it: the harshest conditions for "nothing settles
    # on its own".
    pin_cutoff(True)
    customer_id = _get_customer_id(client, admin_headers)
    orders_before = _count_orders()

    # --- hop 1: the Gateway hands the message over --------------------------
    r = _post_wecom(
        client,
        msgid=_msgid("e2e-text"),
        content=ORDER_TEXT,
        customer_id=customer_id,
        external_userid="wm-e2e-chen",
        chat_id="chat-e2e-1",
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    body = r.json()
    job_id = body["job_id"]
    assert job_id, "WeCom handoff created no intake job"
    assert body["customer_id"] == customer_id

    # --- hop 2: it stops for a human, and no order exists yet ---------------
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "needs_review", (
        f"expected needs_review, got {job['status']}: {job.get('error')}"
    )
    assert job["draft_order_id"] is None, "an order existed before anyone reviewed it"
    assert _count_orders() == orders_before, "an Order row was created without review"

    # The reviewer can actually see what was read.
    ext = client.get(
        f"/api/v1/intake/extractions/{job_id}", headers=admin_headers
    ).json()
    assert len(ext["raw_output"]["lines"]) == 2

    # --- hop 3: a human confirms the extraction → a draft order -------------
    r2 = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers
    )
    assert r2.status_code == 200, f"{r2.status_code} {r2.text}"
    confirmed_review = r2.json()
    order_id = confirmed_review["order_id"]
    assert order_id, "confirm-review returned no order id"
    assert confirmed_review["status"] == "draft", (
        f"confirm-review produced a {confirmed_review['status']} order, not a draft"
    )
    assert _count_orders() == orders_before + 1, "exactly one order should exist now"

    # The job is closed out and points at the order it produced.
    job2 = client.get(f"/api/v1/intake/jobs/{job_id}", headers=admin_headers).json()
    assert job2["status"] == "completed"
    assert job2["draft_order_id"] == order_id

    # --- hop 4: still a draft — approving the reading is not the order ------
    draft = _order(client, order_id, admin_headers)
    assert draft["status"] == "draft", f"order self-confirmed: {draft['status']}"
    assert not draft.get("confirmed_by")
    assert not draft.get("confirmed_at")
    assert draft["customer_id"] == customer_id
    assert len(draft["lines"]) == 2, "the two ordered lines did not survive"

    # --- hop 5: a human confirms the order ----------------------------------
    r3 = client.post(f"/api/v1/orders/{order_id}/confirm", json={}, headers=admin_headers)
    assert r3.status_code == 200, f"{r3.status_code} {r3.text}"

    settled = _order(client, order_id, admin_headers)
    assert settled["status"] == "confirmed"
    assert settled["confirmed_by"] == _admin_user_id(), (
        "the confirmer recorded on the order is not the person who confirmed it"
    )
    assert _is_real_user(settled["confirmed_by"]), "confirmed_by is not a real user"
    assert settled["confirmed_at"], "no confirmation timestamp recorded"


# --- 2. Chatter is parked and creates nothing ---------------------------------

def test_wecom_chatter_is_parked_and_creates_no_order(
    client, admin_headers, require_review, require_human_confirmation, enforce
):
    """'你好' is not an order: it must not reach the inbox, let alone the orders.

    With mandatory review on, every junk message costs a person a glance, so
    Gate 1 has to catch them before they become work.
    """
    customer_id = _get_customer_id(client, admin_headers)
    orders_before = _count_orders()

    r = _post_wecom(
        client, msgid=_msgid("e2e-chatter"), content="你好", customer_id=customer_id
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    body = r.json()

    # The message is still *recorded* — parking hides it from the inbox, it does
    # not delete it. (Without this the "no order" assertion below would also
    # pass if the whole ingest silently failed.)
    assert body["document_id"], "the message was dropped rather than parked"
    with SessionLocal() as db:
        assert db.get(IntakeDocument, body["document_id"]) is not None

    job = client.get(f"/api/v1/intake/jobs/{body['job_id']}", headers=admin_headers).json()
    assert job["status"] == "parked", f"chatter entered the queue: {job['status']}"

    # The point of the whole test.
    assert _count_orders() == orders_before, "chatter created an Order row"

    # Parking also means we did not pay for a parse.
    r2 = client.get(
        f"/api/v1/intake/extractions/{body['job_id']}", headers=admin_headers
    )
    assert r2.status_code == 404, "a parked message was still parsed"

    # And the park was a decision, not an accident of status.
    verdict = _triage_verdict(body["document_id"])
    assert verdict.get("decision") == "not_order", verdict
    assert verdict.get("tier") == "tier0", verdict
    assert verdict.get("parked") is True, verdict


# --- 3. An attachment is never parked -----------------------------------------

def test_wecom_attachment_is_never_parked(
    client, admin_headers, require_review, enforce
):
    """A photo of a handwritten list has no body text — it is still an order.

    `tmp_path` is unusable in this sandbox (its basetemp setup raises
    PermissionError: EEXIST), so this uses `tempfile.TemporaryDirectory()`.
    """
    customer_id = _get_customer_id(client, admin_headers)

    with tempfile.TemporaryDirectory(prefix="e2e_att_") as d:
        f = Path(d) / "order.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\nfake-handwritten-order")
        r = _post_wecom(
            client,
            msgid=_msgid("e2e-image"),
            content="",
            customer_id=customer_id,
            msgtype="image",
            file_path=str(f),
        )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    body = r.json()

    job = _wait_for_job(client, body["job_id"], admin_headers)
    assert job["status"] != "parked", "an image order was parked by triage"
    assert job["status"] == "needs_review", (
        f"image order skipped the human gate: {job['status']}"
    )
    assert job["draft_order_id"] is None, "an order existed before anyone reviewed it"


# --- 4. A wrong park is recoverable -------------------------------------------

def test_parked_wecom_message_can_be_rescued_into_an_order(
    client, admin_headers, require_review, require_human_confirmation, enforce
):
    """Promote → re-queued → reviewed → draft → confirmed.

    Gate 1 is a classifier, so it is sometimes wrong. Parking must therefore be
    reversible: promoting re-runs the real pipeline on the original bytes the
    message arrived with.
    """
    customer_id = _get_customer_id(client, admin_headers)
    orders_before = _count_orders()

    r = _post_wecom(
        client, msgid=_msgid("e2e-rescue"), content="收到", customer_id=customer_id
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    parked = client.get(f"/api/v1/intake/jobs/{job_id}", headers=admin_headers).json()
    assert parked["status"] == "parked", f"expected a park to rescue: {parked['status']}"

    # A human says the classifier was wrong.
    p = client.post(f"/api/v1/intake/jobs/{job_id}/promote", headers=admin_headers)
    assert p.status_code == 200, p.text
    assert p.json()["status"] == "queued", "promote did not re-queue the job"

    # It is back in the queue and goes through the normal gates from here.
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "needs_review", (
        f"promoted job skipped human review: {job['status']} {job.get('error')}"
    )

    # The original bytes were parsed on promotion, not lost by the detour.
    ext = client.get(
        f"/api/v1/intake/extractions/{job_id}", headers=admin_headers
    ).json()
    assert ext["raw_output"]["lines"], "promoted job produced no parsed lines"

    r2 = client.post(f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers)
    assert r2.status_code == 200, r2.text
    order_id = r2.json()["order_id"]
    assert _count_orders() == orders_before + 1

    r3 = client.post(f"/api/v1/orders/{order_id}/confirm", json={}, headers=admin_headers)
    assert r3.status_code == 200, r3.text
    settled = _order(client, order_id, admin_headers)
    assert settled["status"] == "confirmed"
    assert _is_real_user(settled["confirmed_by"])


# --- 5. THE regression guard: approving the extraction is not the order -------

def test_approving_the_extraction_does_not_confirm_the_order(
    client, admin_headers, require_review, require_human_confirmation, pin_cutoff
):
    """A human read the order. Nobody *approved* the order. It stays a draft.

    Auto-confirm used to settle these by itself — including at 3am, with
    `confirmed_by=None` — locking contract prices and messaging the customer
    with nobody's sign-off. This is the assertion that must never be weakened.
    """
    # Worst case on purpose: the clock is before the cutoff and auto-confirm is
    # switched on in the settings table with a low-ish threshold. The
    # `orders_require_human_confirmation` rule has to beat all of it.
    pin_cutoff(True)
    r = client.put(
        "/api/v1/settings",
        json={"auto_confirm": {"enabled": True, "min_confidence": 0.50},
              "cutoff_time": "23:59"},
        headers=admin_headers,
    )
    # If this write ever fails the test would quietly stop proving anything.
    assert r.status_code == 200, f"could not enable auto_confirm: {r.text}"

    try:
        customer_id = _get_customer_id(client, admin_headers)
        orders_before = _count_orders()

        r = _post_wecom(
            client,
            msgid=_msgid("e2e-no-self-confirm"),
            content=ORDER_TEXT,
            customer_id=customer_id,
        )
        assert r.status_code == 201, r.text
        job_id = r.json()["job_id"]
        job = _wait_for_job(client, job_id, admin_headers)
        assert job["status"] == "needs_review", job["status"]

        # The human approves the *extraction*…
        r2 = client.post(
            f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers
        )
        assert r2.status_code == 200, r2.text
        order_id = r2.json()["order_id"]
        assert _count_orders() == orders_before + 1

        # …and that is all they approved.
        order = _order(client, order_id, admin_headers)
        assert order["status"] == "draft", (
            f"the order settled without a human confirming it: {order['status']}"
        )
        assert not order.get("confirmed_by"), "nobody confirmed this order"
        assert not order.get("confirmed_at"), "nobody confirmed this order"

        # It is in the confirmation queue waiting for a person.
        queue = client.get("/api/v1/orders/confirm-count", headers=admin_headers).json()
        assert queue["awaiting_confirmation"] >= 1, (
            "the draft is not in the confirmation queue — nobody will find it"
        )

        # Only a person confirming it settles it.
        r3 = client.post(
            f"/api/v1/orders/{order_id}/confirm", json={}, headers=admin_headers
        )
        assert r3.status_code == 200, r3.text
        settled = _order(client, order_id, admin_headers)
        assert settled["status"] == "confirmed"
        assert settled["confirmed_by"] == _admin_user_id()
        assert settled["confirmed_at"]
    finally:
        client.put(
            "/api/v1/settings",
            json={"auto_confirm": {"enabled": True, "min_confidence": 0.95},
                  "cutoff_time": "18:00"},
            headers=admin_headers,
        )
