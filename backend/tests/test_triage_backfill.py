"""Gate 1 backfill — cleaning up what shadow mode already let through.

Switching enforcement on only governs messages that arrive *afterwards*. The
inbox keeps showing everything ingested while the gate was in "shadow", so the
operator looks at the same junk and concludes the filter still does not work —
which is how this was reported. These tests reproduce that exact sequence:

    post under shadow  →  switch to enforce  →  run the backfill

The asymmetry that shapes every assertion here: parking is cheap to get wrong
in the *noisy* direction and expensive in the *quiet* one. A message wrongly
left in the inbox costs one glance; a real order wrongly hidden is lost
revenue. So the backfill is conservative by construction — dry run by default,
Tier 0 only, and it refuses to touch anything a human has already decided.

`require_review` is requested throughout, because that is production
(`settings.intake_require_human_review` defaults to True) and it is what puts a
junk message in the inbox as `needs_review` — the state in the bug report.
Without it this suite's autouse `_legacy_auto_approve` would let a junk message
auto-create a draft order and end `completed`, which is a different (and much
worse) situation than the one being fixed.
"""
from __future__ import annotations

import uuid

import pytest


def _customer_id(code: str = "C001") -> str:
    from app.core.database import SessionLocal
    from app.models import Customer

    with SessionLocal() as db:
        return db.query(Customer).filter(Customer.code == code).one().id


def _msgid() -> str:
    """Unique per call.

    The fixture DB is per-session, so a reused msgid hits the idempotency
    anchor and returns the FIRST message's document — which silently makes a
    later test assert against the wrong row.
    """
    return f"bf-{uuid.uuid4().hex[:12]}"


def _post(client, content: str, msgid: str, **extra):
    payload = {
        "msgid": msgid,
        "msgtype": "text",
        "content": content,
        "source_type": "text",
        "customer_id": _customer_id(),
        **extra,
    }
    return client.post(
        "/api/v1/intake/wecom",
        json=payload,
        headers={"X-ERP-Service-Key": "dev-service-key"},
    )


def _job(client, headers, job_id: str) -> dict:
    r = client.get(f"/api/v1/intake/jobs/{job_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _backfill(client, headers, *, apply: bool) -> dict:
    r = client.post(
        f"/api/v1/intake/wecom/triage-backfill?apply={str(apply).lower()}",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _inbox_statuses(client, headers) -> list[str]:
    r = client.get("/api/v1/intake/documents", params={"page_size": 100}, headers=headers)
    assert r.status_code == 200, r.text
    return [d.get("job_status") for d in r.json()["items"]]


@pytest.fixture
def shadow(monkeypatch):
    """Post messages the way production did: classified, but hidden from nobody."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "intake_triage_mode", "shadow")
    return None


def _chatter(client, headers, *, msgid: str | None = None, content: str = "where is this account??"):
    """Ingest one junk message the way production did, and prove it landed.

    Returns (job_id, msgid). Asserts the pre-state, so a change to shadow mode
    or to the review gate shows up as a failure here rather than as a silently
    vacuous pass downstream.
    """
    msgid = msgid or _msgid()
    r = _post(client, content, msgid)
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    status = _job(client, headers, job_id)["status"]
    assert status != "parked", (
        "shadow mode parked a message — this fixture is no longer reproducing "
        "the production situation the backfill exists for"
    )
    assert status == "needs_review", (
        f"expected the junk message to be sitting in the review queue, got {status!r}"
    )
    return job_id, msgid


# --- Dry run is the default ---------------------------------------------------


def test_backfill_defaults_to_a_dry_run(client, admin_headers, shadow, require_review):
    """It hides rows, so it must not act on a list nobody has previewed."""
    job_id, _ = _chatter(client, admin_headers)

    report = _backfill(client, admin_headers, apply=False)

    assert report["applied"] is False
    assert report["parked_count"] == 0
    assert report["would_park_count"] >= 1, "the chatter was not even identified"
    assert job_id in {p["job_id"] for p in report["would_park"]}
    assert _job(client, admin_headers, job_id)["status"] != "parked", (
        "a dry run parked something"
    )


def test_the_dry_run_explains_itself(client, admin_headers, shadow, require_review):
    """A count with no reason is not reviewable."""
    msgid = _msgid()
    _chatter(client, admin_headers, msgid=msgid, content="where is this account??")

    entry = next(
        p for p in _backfill(client, admin_headers, apply=False)["would_park"]
        if p["msgid"] == msgid
    )
    assert entry["reasons"], "no reason recorded for a message about to be hidden"
    assert entry["excerpt"] == "where is this account??"


# --- Applying it --------------------------------------------------------------


def test_backfill_parks_the_chatter_and_clears_it_from_the_inbox(
    client, admin_headers, shadow, require_review
):
    job_id, _ = _chatter(client, admin_headers)
    assert "parked" not in _inbox_statuses(client, admin_headers)

    report = _backfill(client, admin_headers, apply=True)

    assert report["applied"] is True
    assert report["parked_count"] >= 1
    assert _job(client, admin_headers, job_id)["status"] == "parked"
    assert "parked" not in _inbox_statuses(client, admin_headers), (
        "a parked message is still in the inbox"
    )


def test_a_parked_message_is_still_reachable_and_promotable(
    client, admin_headers, shadow, require_review
):
    """The safety net: a wrong park must be recoverable, not fatal."""
    job_id, _ = _chatter(client, admin_headers)
    _backfill(client, admin_headers, apply=True)

    r = client.get(
        "/api/v1/intake/documents",
        params={"status": "parked", "page_size": 100},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert any(d.get("job_status") == "parked" for d in r.json()["items"]), (
        "a parked message cannot be audited — a wrong park would be unrecoverable"
    )

    p = client.post(f"/api/v1/intake/jobs/{job_id}/promote", headers=admin_headers)
    assert p.status_code == 200, p.text
    assert _job(client, admin_headers, job_id)["status"] != "parked"


def test_running_the_backfill_twice_changes_nothing_the_second_time(
    client, admin_headers, shadow, require_review
):
    """Idempotent: a re-run must not re-count or re-park."""
    _chatter(client, admin_headers)
    first = _backfill(client, admin_headers, apply=True)
    assert first["parked_count"] >= 1

    second = _backfill(client, admin_headers, apply=True)
    assert second["would_park_count"] == 0, "an already-parked message was counted again"


# --- The things it must never touch ------------------------------------------


def test_backfill_never_parks_a_real_order(client, admin_headers, shadow, require_review):
    """The regression that would cost real money."""
    msgid = _msgid()
    r = _post(client, "土豆 50斤\n大白菜 30斤", msgid)
    assert r.status_code == 201, r.text

    report = _backfill(client, admin_headers, apply=True)

    assert msgid not in {p["msgid"] for p in report["would_park"]}, (
        "a real order was listed for parking"
    )
    assert _job(client, admin_headers, r.json()["job_id"])["status"] != "parked"


def test_backfill_never_parks_an_order_phrased_as_a_question(
    client, admin_headers, shadow, require_review
):
    """The classifier's counterexample, at the backfill level.

    This message is classified `not_order` at Tier 1 with the same score as a
    genuine non-order. The backfill must inherit the Tier 0 restriction, or
    turning the gate on retroactively hides orders that were already read.
    """
    r = _post(client, "能送点土豆过来吗？", _msgid())
    assert r.status_code == 201, r.text

    _backfill(client, admin_headers, apply=True)

    assert _job(client, admin_headers, r.json()["job_id"])["status"] != "parked", (
        "the backfill parked an order phrased as a question"
    )


def test_backfill_leaves_a_confirmed_order_alone(client, admin_headers, shadow, require_review):
    """A `completed` job is an order a human already signed off.

    Parking it would hide work rather than noise, so the status guard is
    load-bearing — this plants the exact state that must be refused.
    """
    job_id, _ = _chatter(client, admin_headers)

    from app.core.database import SessionLocal
    from app.models import IntakeJob

    with SessionLocal() as db:
        job = db.get(IntakeJob, job_id)
        job.status = "completed"  # as if a human had confirmed it into an order
        db.commit()

    report = _backfill(client, admin_headers, apply=True)

    assert job_id not in {p["job_id"] for p in report["would_park"]}
    assert _job(client, admin_headers, job_id)["status"] == "completed", (
        "a confirmed order was parked"
    )


def test_backfill_respects_a_human_override(client, admin_headers, shadow, require_review):
    """A promoted message is a human saying "this was an order".

    Re-parking it would silently undo that decision on the next run, which is
    the one failure mode a backfill can have that a live gate cannot.
    """
    job_id, msgid = _chatter(client, admin_headers)

    from app.core.database import SessionLocal
    from app.models import IntakeDocument
    from sqlalchemy.orm.attributes import flag_modified

    with SessionLocal() as db:
        doc = (
            db.query(IntakeDocument)
            .filter(IntakeDocument.document_meta["wecom"]["msgid"].as_string() == msgid)
            .one()
        )
        meta = dict(doc.document_meta or {})
        wecom = dict(meta.get("wecom") or {})
        verdict = dict(wecom.get("triage") or {})
        verdict["overridden"] = True  # as `promote` records it
        wecom["triage"] = verdict
        meta["wecom"] = wecom
        doc.document_meta = meta
        flag_modified(doc, "document_meta")
        db.commit()

    report = _backfill(client, admin_headers, apply=True)

    assert job_id not in {p["job_id"] for p in report["would_park"]}
    assert _job(client, admin_headers, job_id)["status"] != "parked", (
        "a human override was reversed"
    )


def test_backfill_skips_a_message_that_was_never_classified(
    client, admin_headers, shadow, require_review
):
    """Ingested before triage existed → no verdict → nothing to act on.

    The backfill must not guess. It reports the gap instead, which is also how
    you find out how much of the backlog predates the classifier.
    """
    _job_id, msgid = _chatter(client, admin_headers)

    from app.core.database import SessionLocal
    from app.models import IntakeDocument
    from sqlalchemy.orm.attributes import flag_modified

    with SessionLocal() as db:
        doc = (
            db.query(IntakeDocument)
            .filter(IntakeDocument.document_meta["wecom"]["msgid"].as_string() == msgid)
            .one()
        )
        meta = dict(doc.document_meta or {})
        wecom = dict(meta.get("wecom") or {})
        wecom.pop("triage", None)
        meta["wecom"] = wecom
        doc.document_meta = meta
        flag_modified(doc, "document_meta")
        db.commit()

    report = _backfill(client, admin_headers, apply=False)

    assert report["unclassified"] >= 1, "an unjudged message was not reported as such"


# --- Re-judging a stale verdict ----------------------------------------------
#
# The recorded verdict is a snapshot of the classifier as it was at ingest time.
# The Tier 0 question rule was added after these messages were already
# classified, so in production "where is this account??" sits in the inbox
# stored as `tier: tier1, decision: not_order`. A backfill that trusted that
# snapshot would leave the one message the work was done for exactly where it
# is. These tests plant that exact snapshot.


def _document(client, headers, job_id: str) -> dict:
    job = _job(client, headers, job_id)
    r = client.get(f"/api/v1/intake/documents/{job['document_id']}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _overwrite_verdict(msgid: str, verdict: dict) -> None:
    """Rewrite the stored Gate 1 verdict, as an older classifier would have."""
    from app.core.database import SessionLocal
    from app.models import IntakeDocument
    from sqlalchemy.orm.attributes import flag_modified

    with SessionLocal() as db:
        doc = (
            db.query(IntakeDocument)
            .filter(IntakeDocument.document_meta["wecom"]["msgid"].as_string() == msgid)
            .one()
        )
        meta = dict(doc.document_meta or {})
        wecom = dict(meta.get("wecom") or {})
        wecom["triage"] = {**verdict, "excerpt": "where is this account??"}
        meta["wecom"] = wecom
        doc.document_meta = meta
        flag_modified(doc, "document_meta")
        db.commit()


STALE_TIER1 = {
    "decision": "not_order",
    "tier": "tier1",
    "score": -3,
    "reasons": ["question with no quantity — not an order"],
}


def test_backfill_rejudges_a_stale_tier1_verdict(client, admin_headers, shadow, require_review):
    """The reported message, in the state production actually stores it."""
    job_id, msgid = _chatter(client, admin_headers, content="where is this account??")
    _overwrite_verdict(msgid, STALE_TIER1)

    report = _backfill(client, admin_headers, apply=False)

    assert job_id in {p["job_id"] for p in report["would_park"]}, (
        "the stale Tier 1 snapshot was trusted, so the reported message was "
        "left in the inbox"
    )


def test_the_rejudged_verdict_records_what_it_superseded(client, admin_headers, shadow, require_review):
    """A verdict that changes must say what it changed from."""
    job_id, msgid = _chatter(client, admin_headers, content="where is this account??")
    _overwrite_verdict(msgid, STALE_TIER1)
    _backfill(client, admin_headers, apply=True)

    triage = _document(client, admin_headers, job_id)["triage"]
    assert triage["tier"] == "tier0", "the re-judged verdict was not stored"
    assert triage["parked"] is True
    assert triage["previous"]["tier"] == "tier1", (
        "the superseded verdict was not recorded — 'why did this change?' is "
        "no longer answerable from the row"
    )


def test_the_dry_run_says_which_rows_the_rules_moved_under(
    client, admin_headers, shadow, require_review
):
    """Two different reasons a row is parkable, and they must not look alike.

    A row is listed either because the gate was off when it arrived (the
    classifier agrees with what the row already says) or because the rules
    changed after it was judged (the classifier disagrees with the snapshot).
    Only the second one is a re-judgement, and an operator approving the list
    is entitled to see which rows those are.
    """
    job_id, msgid = _chatter(client, admin_headers, content="where is this account??")
    _overwrite_verdict(msgid, STALE_TIER1)

    entry = next(
        p for p in _backfill(client, admin_headers, apply=False)["would_park"]
        if p["job_id"] == job_id
    )

    assert entry["recorded"] == {"decision": "not_order", "tier": "tier1", "score": -3}, (
        "the dry run hid the fact that the verdict on record was superseded"
    )
    # The reason shown must be the CURRENT rule's, not the snapshot's. Echoing
    # the stored reason would make the list look like it agrees with the row
    # while acting on something else entirely.
    assert entry["reasons"] == ["a question with nothing ordered — not an order"], (
        "the dry run reported the superseded reason instead of the current one"
    )


def test_a_row_whose_verdict_did_not_change_reports_no_superseded_verdict(
    client, admin_headers, shadow, require_review
):
    """The field must be absent, not null-and-noisy, when nothing was replaced.

    Otherwise every ordinary park carries a `recorded` block and the one signal
    that means "the rules moved" is indistinguishable from the background.
    """
    job_id, _ = _chatter(client, admin_headers, content="hi")

    entry = next(
        p for p in _backfill(client, admin_headers, apply=False)["would_park"]
        if p["job_id"] == job_id
    )

    assert "recorded" not in entry, (
        "a row the classifier still agrees with was reported as re-judged"
    )


def test_a_backfill_park_says_it_was_a_backfill(client, admin_headers, shadow, require_review):
    """"Why did a day-old hi just vanish?" has to be answerable on the row.

    A message the gate parks as it arrives and one a cleanup parks days later
    look identical in the inbox otherwise, and only the second one is
    surprising to the person who had been looking at it.
    """
    job_id, _ = _chatter(client, admin_headers, content="hi")
    _backfill(client, admin_headers, apply=True)

    triage = _document(client, admin_headers, job_id)["triage"]
    assert triage["parked"] is True
    assert triage["parked_by"] == "backfill", (
        "a cleanup park is indistinguishable from one the gate made on arrival"
    )


def test_a_park_made_at_ingest_does_not_claim_to_be_a_backfill(
    client, admin_headers, require_review
):
    """The other direction, so the field means something.

    Enforced from the start, this message is parked as it arrives. If the
    ingest path ever wrote `parked_by`, the field would be true of every row
    and would stop distinguishing anything.
    """
    r = _post(client, "hi", _msgid())
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert _job(client, admin_headers, job_id)["status"] == "parked"

    triage = _document(client, admin_headers, job_id)["triage"]
    assert triage["parked_by"] is None, (
        "the gate claimed a cleanup pass parked this when it did so on arrival"
    )


def test_rejudging_does_not_park_a_long_order_that_opens_with_a_question(
    client, admin_headers, shadow, require_review
):
    """The risk re-judging introduces, and why the real text is used.

    The stored excerpt is truncated to 120 characters, so judging from it would
    see only the question. Judging the real text sees the quantities. This is
    the case that makes "just use the excerpt" unacceptable.
    """
    msgid = _msgid()
    r = _post(client, "几点送？\n土豆 50斤\n大白菜 30斤", msgid)
    assert r.status_code == 201, r.text

    report = _backfill(client, admin_headers, apply=True)

    assert msgid not in {p["msgid"] for p in report["would_park"]}, (
        "a real order was parked because only its opening question was judged"
    )
    assert _job(client, admin_headers, r.json()["job_id"])["status"] != "parked"
