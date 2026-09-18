"""Conversation → customer identity (docs/IDENTITY_IMPLEMENTATION_SPEC.md §2).

The invariant these tests exist to protect:

    many chats may point at one customer, but one chat may NEVER point at two

and its consequence: an order from an unknown conversation is *held*, never
guessed at. An unbound chat is a delay; a mis-bound chat is a truck at the
wrong address with an invoice attached.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models import Customer, CustomerIdentity, IntakeDocument, Order

from tests.conftest import admin_headers, client, warehouse_headers  # noqa: F401
from tests.test_mandatory_review import _get_customer_id, _wait_for_job

SERVICE_HEADERS = {"X-ERP-Service-Key": "dev-service-key"}
ORDER_TEXT = "土豆 50斤\n大白菜 30斤"


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _post_wecom(client, *, external_userid: str | None, customer_id: str | None = None,
                content: str = ORDER_TEXT, chat_id: str | None = None,
                contact_alias: str | None = None, contact_name: str | None = None,
                corp_name: str | None = None):
    payload = {
        "msgid": _uniq("wm-identity"),
        "msgtype": "text",
        "content": content,
        "external_userid": external_userid,
        "chat_id": chat_id,
    }
    if customer_id:
        payload["customer_id"] = customer_id
    # Contact metadata the gateway forwards on handoff. Display only — it names
    # the conversation, it never resolves a customer.
    if contact_alias is not None:
        payload["contact_alias"] = contact_alias
    if contact_name is not None:
        payload["contact_name"] = contact_name
    if corp_name is not None:
        payload["corp_name"] = corp_name
    return client.post("/api/v1/intake/wecom", json=payload, headers=SERVICE_HEADERS)


def _document_meta(document_id: str) -> dict:
    with SessionLocal() as db:
        doc = db.get(IntakeDocument, document_id)
        return dict(doc.document_meta or {})


def _count_orders() -> int:
    with SessionLocal() as db:
        return db.query(Order).count()


# ---------------------------------------------------------------------------
# 1. The database, not the application, enforces one customer per chat
# ---------------------------------------------------------------------------

def test_second_confirmed_identity_for_the_same_chat_cannot_be_inserted(
    client,  # noqa: F811 — boots the schema
):
    """The partial unique index is the actual guarantee.

    Application code can be bypassed, refactored or called from a shell; the
    index cannot. This test inserts rows directly, with no service in the way,
    so it proves the database refuses the second binding.
    """
    with SessionLocal() as db:
        customers = db.query(Customer).order_by(Customer.code).limit(2).all()
        assert len(customers) == 2
        first_id, second_id = customers[0].id, customers[1].id

        value = _uniq("wmExtConflict")
        db.add(CustomerIdentity(
            kind="wecom_external_userid", value=value,
            customer_id=first_id, status="confirmed",
        ))
        db.commit()

        db.add(CustomerIdentity(
            kind="wecom_external_userid", value=value,
            customer_id=second_id, status="confirmed",
        ))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------------------------------------------------------------------------
# 1b. Orders arrive in GROUP CHATS — the room is the identity, not the sender
# ---------------------------------------------------------------------------

def test_a_group_chat_is_keyed_on_the_room_not_the_sender():
    """The room belongs to the customer; its members change.

    Keying on `external_userid` would identify one person inside the room, so a
    single customer would fragment into a separate binding for every staff
    member who happened to write — and a colleague writing in the same group
    would resolve to nobody, or worse, to whatever that person was bound to.
    """
    from app.services.identity.service import chat_key

    assert chat_key(external_userid="wmZhangSan", chat_id="wrCanteen") == (
        "wecom_chat_id", "wrCanteen",
    )
    # A 1:1 conversation has no room, so it falls back to the contact.
    assert chat_key(external_userid="wmZhangSan", chat_id=None) == (
        "wecom_external_userid", "wmZhangSan",
    )
    # No stable identifier at all → nothing to bind on. Never the display name.
    assert chat_key(external_userid=None, chat_id=None) is None


def test_a_group_message_is_held_against_its_chat_id(client):  # noqa: F811
    chat = _uniq("wrGroup")
    r = _post_wecom(client, external_userid=_uniq("wmExt"), chat_id=chat)
    assert r.status_code in (200, 201), r.text

    block = _document_meta(r.json()["document_id"])["identity"]
    assert block["status"] == "unbound"
    assert block["kind"] == "wecom_chat_id", (
        "a group order was keyed on the sender instead of the room"
    )
    assert block["value"] == chat


def test_binding_the_room_resolves_a_different_sender(
    client, admin_headers  # noqa: F811
):
    """The point of keying on the room: binding it covers everyone in it."""
    chat = _uniq("wrGroup")
    first = _post_wecom(client, external_userid=_uniq("wmBuyer"), chat_id=chat)
    assert first.status_code in (200, 201), first.text

    customer_id = _get_customer_id(client, admin_headers)
    r = client.post(
        "/api/v1/identity/bind",
        json={"kind": "wecom_chat_id", "value": chat, "customer_id": customer_id},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["released"] >= 1

    # A *different* person writing in the same group now resolves too.
    second = _post_wecom(client, external_userid=_uniq("wmColleague"), chat_id=chat)
    assert second.status_code in (200, 201), second.text
    assert second.json()["customer_id"] == customer_id

    block = _document_meta(second.json()["document_id"])["identity"]
    assert block["status"] == "bound"
    assert block["kind"] == "wecom_chat_id"
    assert block["method"] == "identity"


def test_the_index_is_partial_so_proposals_may_coexist(
    client,  # noqa: F811 — boots the schema
):
    """Only *confirmed* rows are unique — the whole point of a partial index.

    Two chats suggesting different customers is normal; two confirmed bindings
    is not. If this ever fails, someone widened the index and pending
    suggestions will start colliding.
    """
    with SessionLocal() as db:
        customers = db.query(Customer).order_by(Customer.code).limit(2).all()
        first_id, second_id = customers[0].id, customers[1].id
        value = _uniq("wmExtProposed")

        db.add(CustomerIdentity(
            kind="wecom_external_userid", value=value,
            customer_id=first_id, status="confirmed",
        ))
        db.add(CustomerIdentity(
            kind="wecom_external_userid", value=value,
            customer_id=second_id, status="proposed",
        ))
        db.commit()


def test_binding_a_chat_to_a_second_customer_is_refused(
    client, admin_headers  # noqa: F811
):
    """Same guarantee, through the service, with a message a human can read."""
    with SessionLocal() as db:
        customers = db.query(Customer).order_by(Customer.code).limit(2).all()
        first_id, second_id = customers[0].id, customers[1].id

    value = _uniq("wmExtBindTwice")
    r = client.post(
        "/api/v1/identity/bind",
        json={"kind": "wecom_external_userid", "value": value, "customer_id": first_id},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    r2 = client.post(
        "/api/v1/identity/bind",
        json={"kind": "wecom_external_userid", "value": value, "customer_id": second_id},
        headers=admin_headers,
    )
    assert r2.status_code == 409, r2.text


# ---------------------------------------------------------------------------
# 2. Unknown conversations are held, not processed
# ---------------------------------------------------------------------------

def test_unbound_document_cannot_be_confirmed_into_an_order(
    client, admin_headers, require_review  # noqa: F811
):
    """The hole this work closes: an order with no customer behind it.

    Today such a message would sail through to a draft order. After this, it is
    held at the review gate and no amount of confirming will move it until a
    human binds the conversation.
    """
    external_userid = _uniq("wmExtUnbound")
    orders_before = _count_orders()

    r = _post_wecom(client, external_userid=external_userid)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["customer_id"] is None, "an unknown chat resolved to a customer"

    meta = _document_meta(body["document_id"])
    assert meta["identity"]["status"] == "unbound"
    assert meta["identity"]["kind"] == "wecom_external_userid"

    job = _wait_for_job(client, body["job_id"], admin_headers)
    assert job["status"] == "needs_review", job
    assert job["draft_order_id"] is None

    # The gate: confirming the extraction must not produce an order.
    r2 = client.post(
        f"/api/v1/intake/jobs/{body['job_id']}/confirm-review",
        headers=admin_headers,
    )
    assert r2.status_code == 400, r2.text
    assert _count_orders() == orders_before, "an unbound document became an order"


def test_unbound_chats_appear_in_the_queue_grouped_by_chat(
    client, admin_headers, require_review  # noqa: F811
):
    """One row per conversation, with a waiting count — not one row per message."""
    external_userid = _uniq("wmExtQueue")
    for _ in range(2):
        r = _post_wecom(client, external_userid=external_userid)
        assert r.status_code == 201, r.text

    q = client.get("/api/v1/identity/unbound", headers=admin_headers)
    assert q.status_code == 200, q.text
    items = q.json()["items"]
    row = next(
        (i for i in items if i["value"] == external_userid), None
    )
    assert row is not None, f"chat {external_userid} is not in the queue: {items}"
    assert row["waiting_count"] == 2, row
    assert row["kind"] == "wecom_external_userid"
    assert row["last_message_at"], row


def test_binding_releases_held_documents_and_they_carry_the_customer(
    client, admin_headers, require_review  # noqa: F811
):
    """Bind once → every held document for that chat gets its customer."""
    external_userid = _uniq("wmExtRelease")
    customer_id = _get_customer_id(client, admin_headers)
    orders_before = _count_orders()

    r = _post_wecom(client, external_userid=external_userid)
    assert r.status_code == 201, r.text
    document_id = r.json()["document_id"]
    job_id = r.json()["job_id"]
    _wait_for_job(client, job_id, admin_headers)

    r2 = client.post(
        "/api/v1/identity/bind",
        json={
            "kind": "wecom_external_userid",
            "value": external_userid,
            "customer_id": customer_id,
            "evidence": {"contact_name": "陈师傅", "msgid": "wm-evidence-1"},
        },
        headers=admin_headers,
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["released"] == 1, r2.json()
    assert r2.json()["confirmed_by"], "the bind has no name on it"

    meta = _document_meta(document_id)
    assert meta["identity"]["status"] == "bound"
    assert meta["identity"]["customer_id"] == customer_id

    # And now the same confirm that was refused before goes through.
    r3 = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers
    )
    assert r3.status_code == 200, r3.text
    order_id = r3.json()["order_id"]

    with SessionLocal() as db:
        order = db.get(Order, order_id)
        assert order.customer_id == customer_id, "released order has no customer"
    assert _count_orders() == orders_before + 1


def test_unbind_returns_every_order_that_used_the_binding(
    client, admin_headers, require_review  # noqa: F811
):
    """Reversal is safe because it produces a list, not a silent rewrite."""
    external_userid = _uniq("wmExtUnbind")
    customer_id = _get_customer_id(client, admin_headers)

    r = _post_wecom(client, external_userid=external_userid)
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    _wait_for_job(client, job_id, admin_headers)

    bind = client.post(
        "/api/v1/identity/bind",
        json={
            "kind": "wecom_external_userid",
            "value": external_userid,
            "customer_id": customer_id,
        },
        headers=admin_headers,
    )
    assert bind.status_code == 201, bind.text
    identity_id = bind.json()["id"]

    review = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers
    )
    assert review.status_code == 200, review.text
    order_id = review.json()["order_id"]

    r2 = client.post(
        f"/api/v1/identity/{identity_id}/unbind",
        json={"reason": "wrong account — this is the night counter"},
        headers=admin_headers,
    )
    assert r2.status_code == 200, r2.text
    assert order_id in r2.json()["affected_orders"], (
        f"unbind did not report the order that used it: {r2.json()}"
    )

    # History is not rewritten: the confirmed order keeps its customer.
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        assert order.customer_id == customer_id
        identity = db.get(CustomerIdentity, identity_id)
        assert identity.status == "rejected"


def test_a_confirmed_binding_outranks_the_customer_the_gateway_claims(
    client, admin_headers, require_review  # noqa: F811
):
    """The ERP owns the ledger. A gateway assertion cannot override a bind."""
    with SessionLocal() as db:
        customers = db.query(Customer).order_by(Customer.code).limit(2).all()
        bound_id, other_id = customers[0].id, customers[1].id

    external_userid = _uniq("wmExtOverride")
    bind = client.post(
        "/api/v1/identity/bind",
        json={
            "kind": "wecom_external_userid",
            "value": external_userid,
            "customer_id": bound_id,
        },
        headers=admin_headers,
    )
    assert bind.status_code == 201, bind.text

    r = _post_wecom(client, external_userid=external_userid, customer_id=other_id)
    assert r.status_code == 201, r.text
    assert r.json()["customer_id"] == bound_id, (
        "the gateway's customer beat the ERP binding"
    )


# ---------------------------------------------------------------------------
# 3. Company proposals are proposals
# ---------------------------------------------------------------------------

def test_ingest_survives_a_missing_company_extractor(
    client, admin_headers, monkeypatch  # noqa: F811
):
    """`app/ai/company_extract.py` is another agent's file and may not exist.

    A missing extractor degrades to "no proposal" — it must never be able to
    lose a customer's order.
    """
    import app.services.intake.company_proposal as proposal

    monkeypatch.setattr(proposal, "_extractor", lambda: None)

    r = _post_wecom(client, external_userid=_uniq("wmExtNoExtractor"))
    assert r.status_code == 201, r.text
    assert "company_proposal" not in _document_meta(r.json()["document_id"])


def test_company_proposal_endpoint_returns_a_proposal_or_null(
    client, admin_headers  # noqa: F811
):
    """Read-only: the endpoint proposes, it never attaches a customer."""
    r = _post_wecom(client, external_userid=_uniq("wmExtProposal"))
    assert r.status_code == 201, r.text
    document_id = r.json()["document_id"]

    r2 = client.get(
        f"/api/v1/intake/documents/{document_id}/company-proposal",
        headers=admin_headers,
    )
    assert r2.status_code == 200, r2.text
    proposal = r2.json()
    if proposal is None:
        return  # nothing proposed (no extractor, or nothing found) — fine
    for field in ("name", "address", "phone", "contact", "tax_id"):
        assert field in proposal, proposal
        assert "confidence" in proposal[field] and "method" in proposal[field]
    assert "source_kind" in proposal and "raw_excerpt" in proposal


# ---------------------------------------------------------------------------
# 4. Access control
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/identity/unbound"),
        ("post", "/api/v1/identity/bind"),
        ("get", "/api/v1/identity/bindings"),
    ],
)
def test_identity_endpoints_require_ops_or_admin(
    client, warehouse_headers, method, path  # noqa: F811
):
    """Binding a chat to an account is an ops decision, not a warehouse one."""
    call = getattr(client, method)
    r = call(path, json={}, headers=warehouse_headers) if method == "post" else call(
        path, headers=warehouse_headers
    )
    assert r.status_code == 403, f"{method} {path} → {r.status_code} {r.text}"


# ---------------------------------------------------------------------------
# 5. The inbox can SEE the binding state (docs/INTAKE_BINDING_UX_PLAN.md §S0)
# ---------------------------------------------------------------------------
#
# The gate itself is server-side and correct. But until these fields were
# serialised the UI had no way to know a row was held: `customer_id` alone
# renders "—" for *both* "no customer" and "a customer whose name we never
# sent". The reviewer's only signal was a banner telling them to go and bind a
# conversation they could not identify from the list.

# Deliberately NOT the whole `document_meta["identity"]` block: that also holds
# identity_id, confirmed_by and released_by. This is the subset the inbox needs,
# pinned so nothing extra can creep onto a widely-read list endpoint.
#
# `display_name` / `corp_name` / `alias` were added deliberately, not by drift:
# `status: unbound` says a decision is needed but not who is on the other end,
# so a held row could only be identified by its `chat_key`. These are the same
# three fields `/identity/unbound` already returns, so the list endpoint
# discloses nothing new — and none of them ever resolves a customer.
IDENTITY_KEYS = {
    "status", "chat_key", "kind", "value", "method", "reason",
    "display_name", "corp_name", "alias",
}


def _get_document(client, headers, document_id: str) -> dict:
    r = client.get(f"/api/v1/intake/documents/{document_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_an_unbound_document_tells_the_ui_it_is_unbound(
    client, admin_headers, require_review  # noqa: F811
):
    """The row must say *unbound* and carry the chat_key to act on.

    This is what lets the inbox offer a "needs customer" affordance instead of a
    dead "—", and what tells the review drawer which conversation to bind.
    """
    chat = _uniq("wrChatUnbound")
    r = _post_wecom(client, external_userid=None, chat_id=chat)
    assert r.status_code == 201, r.text

    doc = _get_document(client, admin_headers, r.json()["document_id"])
    assert doc["customer_id"] is None
    assert doc["identity"]["status"] == "unbound"
    assert doc["identity"]["chat_key"] == f"wecom_chat_id:{chat}"
    assert doc["identity"]["kind"] == "wecom_chat_id"
    assert doc["identity"]["value"] == chat
    # Nothing to render a name from — and the row says so rather than guessing.
    assert doc["customer_name_en"] is None
    assert doc["customer_name_zh"] is None


def test_a_bound_document_serialises_the_customer_name(
    client, admin_headers, require_review  # noqa: F811
):
    """The Customer column reads a NAME, not just an id.

    `IntakePage` renders `pickName(lang, customer_name_en, customer_name_zh)`
    while `_doc_out` sent neither, so even a correctly bound order showed "—".
    Binding alone would not have fixed the column.
    """
    chat = _uniq("wrChatBound")
    customer_id = _get_customer_id(client, admin_headers)
    with SessionLocal() as db:
        customer = db.get(Customer, customer_id)
        expected_en, expected_zh = customer.name_en, customer.name_zh
    assert expected_en, "the seed customer has no name to assert against"

    r = _post_wecom(client, external_userid=None, chat_id=chat)
    assert r.status_code == 201, r.text
    document_id = r.json()["document_id"]

    bind = client.post(
        "/api/v1/identity/bind",
        json={"kind": "wecom_chat_id", "value": chat, "customer_id": customer_id},
        headers=admin_headers,
    )
    assert bind.status_code == 201, bind.text
    assert bind.json()["released"] == 1, bind.json()

    doc = _get_document(client, admin_headers, document_id)
    assert doc["customer_id"] == customer_id
    assert doc["customer_name_en"] == expected_en
    assert doc["customer_name_zh"] == expected_zh
    assert doc["identity"]["status"] == "bound"
    # `identity` is the method that means "a human bound this chat HERE" —
    # as opposed to upstream_asserted / parent_inherited.
    assert doc["identity"]["method"] == "identity"


def test_the_inbox_list_carries_the_name_and_the_binding_state(
    client, admin_headers, require_review  # noqa: F811
):
    """The list is what the column actually reads, so it must carry both fields."""
    chat = _uniq("wrChatList")
    customer_id = _get_customer_id(client, admin_headers)
    r = _post_wecom(client, external_userid=None, chat_id=chat)
    assert r.status_code == 201, r.text
    document_id = r.json()["document_id"]

    bind = client.post(
        "/api/v1/identity/bind",
        json={"kind": "wecom_chat_id", "value": chat, "customer_id": customer_id},
        headers=admin_headers,
    )
    assert bind.status_code == 201, bind.text

    listing = client.get(
        "/api/v1/intake/documents",
        params={"customer_id": customer_id, "page_size": 100},
        headers=admin_headers,
    )
    assert listing.status_code == 200, listing.text
    rows = [i for i in listing.json()["items"] if i["id"] == document_id]
    assert rows, "the bound document is missing from the inbox list"

    row = rows[0]
    assert row["customer_name_en"], "the Customer column has nothing to render"
    assert row["identity"]["status"] == "bound"
    assert row["identity"]["chat_key"] == f"wecom_chat_id:{chat}"


def test_a_held_row_says_who_is_talking(
    client, admin_headers, require_review  # noqa: F811
):
    """`unbound` is only actionable if the reviewer can tell WHO is unbound.

    Without these the inbox could say nothing but "needs a customer" next to a
    `chat_key`, and the operator had to identify the conversation from memory.
    They are contact metadata forwarded by the gateway: they name the
    conversation for a human and never resolve a customer.
    """
    chat = _uniq("wrChatNamed")
    r = _post_wecom(
        client,
        external_userid=None,
        chat_id=chat,
        contact_alias="陈记饭店",
        contact_name="陈经理",
        corp_name="陈记餐饮有限公司",
    )
    assert r.status_code == 201, r.text
    document_id = r.json()["document_id"]

    doc = _get_document(client, admin_headers, document_id)
    assert doc["identity"]["status"] == "unbound"
    assert doc["identity"]["alias"] == "陈记饭店"
    assert doc["identity"]["display_name"] == "陈经理"
    assert doc["identity"]["corp_name"] == "陈记餐饮有限公司"
    # Naming the conversation must not bind it.
    assert doc["customer_id"] is None

    # The list is what renders the row, so it has to carry them too.
    listing = client.get(
        "/api/v1/intake/documents",
        params={"unbound_only": True, "page_size": 100},
        headers=admin_headers,
    )
    assert listing.status_code == 200, listing.text
    rows = [i for i in listing.json()["items"] if i["id"] == document_id]
    assert rows, "the held document is missing from the unbound inbox list"
    assert rows[0]["identity"]["alias"] == "陈记饭店"


def test_a_conversation_we_know_nothing_about_still_reports_the_keys(
    client, admin_headers, require_review  # noqa: F811
):
    """Absent contact metadata is `None`, never a missing key or a guess.

    A plausible-looking invented name is how a wrong bind starts, and a missing
    key would make the UI read `undefined` and render an empty cell.
    """
    chat = _uniq("wrChatAnon")
    r = _post_wecom(client, external_userid=None, chat_id=chat)
    assert r.status_code == 201, r.text

    doc = _get_document(client, admin_headers, r.json()["document_id"])
    assert doc["identity"]["status"] == "unbound"
    assert doc["identity"]["alias"] is None
    assert doc["identity"]["display_name"] is None
    assert doc["identity"]["corp_name"] is None


def test_the_serialised_identity_block_is_a_fixed_shape(
    client, admin_headers, require_review  # noqa: F811
):
    """Pin the contract so the full meta block cannot leak onto the list endpoint."""
    chat = _uniq("wrChatShape")
    r = _post_wecom(client, external_userid=None, chat_id=chat)
    assert r.status_code == 201, r.text

    doc = _get_document(client, admin_headers, r.json()["document_id"])
    assert set(doc["identity"]) == IDENTITY_KEYS, doc["identity"]


def test_the_inbox_can_filter_to_documents_that_need_a_customer(
    client, admin_headers, require_review  # noqa: F811
):
    """`unbound_only` is what makes the blocked rows findable in one click.

    Server-side on purpose: a browser-side filter would show page 1 of 20 and
    silently hide every match on the pages it never fetched.
    """
    held_chat = _uniq("wrChatFilterHeld")
    bound_chat = _uniq("wrChatFilterBound")
    customer_id = _get_customer_id(client, admin_headers)

    held = _post_wecom(client, external_userid=None, chat_id=held_chat)
    assert held.status_code == 201, held.text
    bound = _post_wecom(client, external_userid=None, chat_id=bound_chat)
    assert bound.status_code == 201, bound.text

    bind = client.post(
        "/api/v1/identity/bind",
        json={"kind": "wecom_chat_id", "value": bound_chat, "customer_id": customer_id},
        headers=admin_headers,
    )
    assert bind.status_code == 201, bind.text

    r = client.get(
        "/api/v1/intake/documents",
        params={"unbound_only": True, "page_size": 100},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = {i["id"] for i in items}

    assert held.json()["document_id"] in ids, "the held document is missing"
    assert bound.json()["document_id"] not in ids, "a bound document leaked in"
    # Everything returned really is held — the filter must not be a hint.
    assert all(i["identity"]["status"] == "unbound" for i in items), items


def test_the_review_count_separates_held_rows_from_the_rest(
    client, admin_headers, require_review  # noqa: F811
):
    """The banner needs to be able to say the queue is BLOCKED, not busy.

    "17 waiting for review" is true and useless when all 17 are held — it reads
    as a queue with work in it when it is a queue with one action behind it.
    """
    chat = _uniq("wrChatCount")
    r = _post_wecom(client, external_userid=None, chat_id=chat)
    assert r.status_code == 201, r.text
    _wait_for_job(client, r.json()["job_id"], admin_headers)

    counts = client.get("/api/v1/intake/review-count", headers=admin_headers)
    assert counts.status_code == 200, counts.text
    body = counts.json()

    assert "unbound" in body, body
    assert body["unbound"] >= 1, body
    # The held row is counted in both, which is what lets the banner report
    # "N waiting · M need a customer first" without contradicting itself.
    assert body["pending_review"] >= 1, body
