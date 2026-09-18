"""The two things a reviewer can do to a `needs_review` extraction.

Before this existed the review screen had exactly one button, "Confirm &
submit". That made the queue a trap: an operator holding a DUPLICATE — the
single most common reason a WeCom order must not be created twice — had to
either create the duplicate order or leave the row sitting there forever. A
queue you cannot clear stops being read, and a queue that stops being read is
how a real order gets missed.

So there are two exits now, and they are tested together because they are two
halves of one decision:

  * correct it and confirm  (`POST .../confirm-review`, optional body)
  * refuse it with a reason (`POST .../reject`)

The other thing these tests defend is the boundary between the machine and the
person. The extraction is the record of what the extractor read; the reviewer's
corrections are a different artefact. Folding one into the other would destroy
the only evidence of how often the extractor is wrong — which is the number that
decides whether it is worth trusting at all. Every edit test therefore asserts
BOTH that the order changed and that the extraction did not.
"""
from __future__ import annotations

import time

from tests.conftest import admin_headers, client  # noqa: F401  (pytest fixtures)

DEMO_TEXT = "土豆 50斤\n大白菜 30斤\n五花肉 20斤"

_JOB_TIMEOUT = 10

# A 1x1 PNG. Only the extension and the bytes matter here — the extractor under
# test is the mock, which does not decode images, and the point of the test is
# the media type the FILE endpoint reports, not the picture.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


class _FixedExtractor:
    """An extractor that returns exactly the lines a test asks for.

    The mock adapter's canned lines are fine for most of the suite, but these
    tests are about a reviewer correcting a SPECIFIC line number, so the input
    has to be fixed rather than incidental.
    """

    def __init__(self, lines):
        self._lines = lines

    def extract(self, *, source_type, raw_text=None, file_path=None, original_filename=None):
        from app.ai.adapters import ExtractionResult

        return ExtractionResult(
            lines=list(self._lines),
            doc_type=source_type,
            form_type="structured",
            form_type_confidence=1.0,
            overall_confidence=0.95,
            requires_human_review=True,
            parser_notes="fixed extraction for review tests",
        )


def _lines(*specs):
    """`_lines(("土豆", 50, "斤"), ("大白菜", 30, "斤"))` → RawLine objects."""
    from app.ai.adapters import RawLine

    return [RawLine(product_name=n, quantity=q, unit=u) for n, q, u in specs]


def _use_extractor(monkeypatch, *specs):
    monkeypatch.setattr(
        "app.ai.pipeline.get_extractor", lambda: _FixedExtractor(_lines(*specs))
    )


def _wait_for_job(client, job_id: str, headers: dict, timeout: float = _JOB_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/v1/intake/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, f"job get failed: {r.status_code} {r.text}"
        last = r.json()
        if last["status"] in ("completed", "failed", "needs_review", "rejected"):
            return last
        time.sleep(0.05)
    return last


def _customer_id(client, headers, code: str = "C001") -> str:
    r = client.get("/api/v1/customers", headers=headers)
    assert r.status_code == 200, r.text
    for c in r.json()["items"]:
        if c["code"] == code:
            return c["id"]
    raise AssertionError(f"Customer {code} not found in seed data")


def _product_id(client, headers, name_zh: str) -> str:
    r = client.get(
        "/api/v1/products", params={"page_size": 100}, headers=headers
    )
    assert r.status_code == 200, r.text
    for p in r.json()["items"]:
        if p["name_zh"] == name_zh:
            return p["id"]
    raise AssertionError(f"Product {name_zh} not found in seed catalog")


def _count_orders() -> int:
    from app.core.database import SessionLocal
    from app.models import Order

    with SessionLocal() as db:
        return db.query(Order).count()


def _order_lines(order_id: str) -> list[dict]:
    """Read the order's lines from the DATABASE, not from a serializer.

    A dialog closing, or a response body, is not proof that a value was stored.
    This is what the server actually has.
    """
    from app.core.database import SessionLocal
    from app.models import OrderLine

    with SessionLocal() as db:
        rows = (
            db.query(OrderLine)
            .filter(OrderLine.order_id == order_id)
            .order_by(OrderLine.line_no)
            .all()
        )
        return [
            {
                "line_no": r.line_no,
                "quantity": r.quantity,
                "product_id": r.product_id,
                "product_display": r.product_display,
                "unit_id": r.unit_id,
                "confidence": r.confidence,
                "match_method": r.match_method,
            }
            for r in rows
        ]


def _extraction_raw(client, headers, job_id: str) -> dict:
    r = client.get(f"/api/v1/intake/extractions/{job_id}", headers=headers)
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    return r.json()["raw_output"]


def _submit(client, headers, customer_id: str, text: str = DEMO_TEXT) -> str:
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": text},
        headers=headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    return r.json()["job_id"]


def _awaiting_review(client, headers, monkeypatch, *specs) -> tuple[str, str]:
    """Submit a document whose extraction is fixed, and wait for the gate.

    Returns (job_id, document_id).
    """
    _use_extractor(monkeypatch, *specs)
    customer_id = _customer_id(client, headers)
    job_id = _submit(client, headers, customer_id)
    job = _wait_for_job(client, job_id, headers)
    assert job["status"] == "needs_review", f"expected the gate, got {job['status']}"
    return job_id, job["document_id"]


# ---------------------------------------------------------------------------
# Rejecting: the exit the queue was missing
# ---------------------------------------------------------------------------

def test_rejecting_a_duplicate_creates_no_order(client, admin_headers, require_review, monkeypatch):
    """The headline case. A duplicate must be refusable, not confirmable-only."""
    job_id, _doc_id = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )
    before = _count_orders()

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/reject",
        json={"reason": "duplicate"},
        headers=admin_headers,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    body = r.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "duplicate"
    assert _count_orders() == before, "a rejection must never create an order"


def test_the_rejection_reason_is_readable_from_the_inbox(
    client, admin_headers, require_review, monkeypatch
):
    """`rejected` alone does not say WHY, and why is what the next person needs.

    Someone will eventually ask "why did this customer's order never arrive?"
    and the row has to answer it — otherwise they order it again by hand and
    the duplicate the reviewer refused happens anyway, just later.
    """
    job_id, doc_id = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤")
    )
    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/reject",
        json={"reason": "duplicate", "note": "same as ORD-1042, sent twice"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text

    doc = client.get(f"/api/v1/intake/documents/{doc_id}", headers=admin_headers).json()
    assert doc["rejection"] is not None, "the document must carry the rejection"
    assert doc["rejection"]["reason"] == "duplicate"
    assert "ORD-1042" in doc["rejection"]["note"]
    assert doc["rejection"]["by"], "who refused it has to be recorded"

    # And on the LIST payload — that is the screen the reason is read from.
    listed = client.get(
        "/api/v1/intake/documents",
        params={"status": "rejected", "page_size": 100},
        headers=admin_headers,
    ).json()
    row = next((i for i in listed["items"] if i["id"] == doc_id), None)
    assert row is not None, "a rejected document must still be findable"
    assert row["rejection"]["reason"] == "duplicate"


def test_a_rejection_reason_must_come_from_the_list(
    client, admin_headers, require_review, monkeypatch
):
    """Free text cannot be counted, and "how many were duplicates?" will be asked."""
    job_id, _ = _awaiting_review(client, admin_headers, monkeypatch, ("土豆", 50, "斤"))
    before = _count_orders()

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/reject",
        json={"reason": "because I said so"},
        headers=admin_headers,
    )
    assert r.status_code == 400, f"expected a refusal, got {r.status_code}"
    assert "duplicate" in r.text, "the message must name the codes that are allowed"
    assert _count_orders() == before

    # ...and the job is untouched, so the reviewer can try again properly.
    job = client.get(f"/api/v1/intake/jobs/{job_id}", headers=admin_headers).json()
    assert job["status"] == "needs_review"


def test_other_requires_a_note(client, admin_headers, require_review, monkeypatch):
    """`other` with nothing attached records a decision nobody can interpret."""
    job_id, _ = _awaiting_review(client, admin_headers, monkeypatch, ("土豆", 50, "斤"))

    bare = client.post(
        f"/api/v1/intake/jobs/{job_id}/reject",
        json={"reason": "other"},
        headers=admin_headers,
    )
    assert bare.status_code == 400, bare.text

    with_note = client.post(
        f"/api/v1/intake/jobs/{job_id}/reject",
        json={"reason": "other", "note": "customer rang to cancel"},
        headers=admin_headers,
    )
    assert with_note.status_code == 200, with_note.text


def test_a_rejected_job_cannot_then_be_confirmed(
    client, admin_headers, require_review, monkeypatch
):
    """Rejection is a decision, not a suggestion. Confirm must not undo it."""
    job_id, _ = _awaiting_review(client, admin_headers, monkeypatch, ("土豆", 50, "斤"))
    client.post(
        f"/api/v1/intake/jobs/{job_id}/reject",
        json={"reason": "not_an_order"},
        headers=admin_headers,
    )
    before = _count_orders()

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers
    )
    assert r.status_code == 400, f"a rejected job must not become an order: {r.status_code}"
    assert "rejected" in r.text
    assert _count_orders() == before


def test_only_a_job_awaiting_review_can_be_rejected(
    client, admin_headers, require_review, monkeypatch
):
    """Otherwise a settled job could be quietly re-labelled after the fact."""
    job_id, _ = _awaiting_review(client, admin_headers, monkeypatch, ("土豆", 50, "斤"))
    ok = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers
    )
    assert ok.status_code == 200, ok.text

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/reject",
        json={"reason": "duplicate"},
        headers=admin_headers,
    )
    assert r.status_code == 400, r.text
    assert "completed" in r.text


def test_rejecting_clears_the_row_from_the_review_queue(
    client, admin_headers, require_review, monkeypatch
):
    """The badge counts work waiting for a person. A refusal is done work."""
    job_id, _ = _awaiting_review(client, admin_headers, monkeypatch, ("土豆", 50, "斤"))
    queued = client.get("/api/v1/intake/review-count", headers=admin_headers).json()
    assert queued["pending_review"] >= 1

    client.post(
        f"/api/v1/intake/jobs/{job_id}/reject",
        json={"reason": "duplicate"},
        headers=admin_headers,
    )
    after = client.get("/api/v1/intake/review-count", headers=admin_headers).json()
    assert after["pending_review"] == queued["pending_review"] - 1


def test_a_rejection_leaves_the_extraction_and_the_document_intact(
    client, admin_headers, require_review, monkeypatch
):
    """The parse is evidence of what the message said; a rejection is a
    decision about it. Erasing the first to record the second would make the
    audit trail useless for asking whether the extractor is any good."""
    job_id, doc_id = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )
    raw_before = _extraction_raw(client, admin_headers, job_id)

    client.post(
        f"/api/v1/intake/jobs/{job_id}/reject",
        json={"reason": "duplicate"},
        headers=admin_headers,
    )

    assert _extraction_raw(client, admin_headers, job_id) == raw_before
    job = client.get(f"/api/v1/intake/jobs/{job_id}", headers=admin_headers).json()
    assert job["draft_order_id"] is None, "a rejection must not point at an order"

    # The source is still downloadable — it is the evidence.
    f = client.get(f"/api/v1/intake/documents/{doc_id}/file", headers=admin_headers)
    assert f.status_code == 200


# ---------------------------------------------------------------------------
# Correcting: the reviewer is allowed to disagree with the machine
# ---------------------------------------------------------------------------

def test_a_corrected_quantity_reaches_the_order(
    client, admin_headers, require_review, monkeypatch, pin_cutoff
):
    """The case that started this: the note says 45, the OCR read 30."""
    pin_cutoff(False)
    job_id, _ = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"lines": [{"line_no": 2, "quantity": 45}]},
        headers=admin_headers,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text}"

    lines = _order_lines(r.json()["order_id"])
    assert [l["quantity"] for l in lines] == [50.0, 45.0]
    assert lines[0]["quantity"] == 50.0, "the untouched line must not drift"


def test_a_correction_does_not_rewrite_the_extraction(
    client, admin_headers, require_review, monkeypatch, pin_cutoff
):
    """The gap between read and confirmed is the extractor's error rate.

    It is the only measurement of whether the AI is any good, so it has to
    survive the correction that fixes the order.
    """
    pin_cutoff(False)
    job_id, doc_id = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )
    before = _extraction_raw(client, admin_headers, job_id)
    assert before["lines"][1]["quantity"] == 30.0

    client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"lines": [{"line_no": 2, "quantity": 45}]},
        headers=admin_headers,
    )

    after = _extraction_raw(client, admin_headers, job_id)
    assert after["lines"][1]["quantity"] == 30.0, (
        "the machine's reading must stay exactly as it was recorded"
    )

    doc = client.get(f"/api/v1/intake/documents/{doc_id}", headers=admin_headers).json()
    review = client.get(
        f"/api/v1/intake/extractions/{job_id}", headers=admin_headers
    ).json()["human_review"]
    assert review is not None, "what the person changed must be recorded somewhere"
    assert review["corrected"][0]["line_no"] == 2
    assert review["corrected"][0]["quantity"] == {"from": 30.0, "to": 45}
    assert review["by"], "who corrected it has to be recorded"
    assert doc["rejection"] is None, "a confirm is not a rejection"


def test_a_missing_line_can_be_added(
    client, admin_headers, require_review, monkeypatch, pin_cutoff
):
    """The extractor missed a line entirely — the reviewer must be able to say so."""
    pin_cutoff(False)
    job_id, _ = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"added_lines": [{"product_name": "大米", "quantity": 2, "unit": "袋"}]},
        headers=admin_headers,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text}"

    lines = _order_lines(r.json()["order_id"])
    assert len(lines) == 3, "the added line must be on the order"
    added = lines[-1]
    assert added["product_id"] == _product_id(client, admin_headers, "大米")
    assert added["quantity"] == 2.0
    assert added["line_no"] == 3, "the added line continues the numbering"
    assert added["match_method"] == "human_added"


def test_a_wrong_line_can_be_removed(
    client, admin_headers, require_review, monkeypatch, pin_cutoff
):
    """Not the same as the customer's own strikethrough — this is the reviewer
    dropping a line the extractor invented."""
    pin_cutoff(False)
    job_id, _ = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"lines": [{"line_no": 1, "cancelled": True}]},
        headers=admin_headers,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text}"

    lines = _order_lines(r.json()["order_id"])
    assert len(lines) == 1
    assert lines[0]["product_id"] == _product_id(client, admin_headers, "大白菜")


def test_a_corrected_product_is_matched_again(
    client, admin_headers, require_review, monkeypatch, pin_cutoff
):
    """A misread product name must resolve to the SKU the reviewer meant, and
    the order must not keep claiming the machine's match."""
    pin_cutoff(False)
    job_id, _ = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"lines": [{"line_no": 1, "product_name": "五花肉"}]},
        headers=admin_headers,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text}"

    lines = _order_lines(r.json()["order_id"])
    assert lines[0]["product_id"] == _product_id(client, admin_headers, "五花肉")
    assert lines[0]["match_method"] == "human_edited"


def test_a_corrected_line_carries_no_invented_confidence(
    client, admin_headers, require_review, monkeypatch, pin_cutoff
):
    """A person's correction is not a confidence score.

    Leaving the extractor's number on a line the person rewrote would report a
    machine certainty about a value the machine never produced.
    """
    pin_cutoff(False)
    job_id, _ = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )
    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"lines": [{"line_no": 2, "quantity": 45}]},
        headers=admin_headers,
    )
    lines = _order_lines(r.json()["order_id"])
    assert lines[0]["confidence"] is not None, "an untouched line keeps its score"
    assert lines[1]["confidence"] is None, "a corrected line must not borrow one"


def test_an_edit_to_a_line_that_is_not_there_is_refused(
    client, admin_headers, require_review, monkeypatch
):
    """A silent no-op here would produce an order nobody actually reviewed."""
    job_id, _ = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )
    before = _count_orders()

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"lines": [{"line_no": 9, "quantity": 1}]},
        headers=admin_headers,
    )
    assert r.status_code == 400, f"expected a refusal, got {r.status_code}"
    assert "2 line(s)" in r.text, "the message must say what the server actually has"
    assert _count_orders() == before, "a refused confirm must create nothing"


def test_a_zero_quantity_is_refused_not_clamped(
    client, admin_headers, require_review, monkeypatch
):
    """`0` is a mis-tapped field, not a small order.

    Clamping it (the way `min={0.01}` on an input silently does) would record a
    number the reviewer never typed — strictly worse than refusing, because
    nobody is told.
    """
    job_id, _ = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )
    before = _count_orders()

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"lines": [{"line_no": 1, "quantity": 0}]},
        headers=admin_headers,
    )
    assert r.status_code == 400, f"expected a refusal, got {r.status_code}"
    assert "Line 1" in r.text and "remove the line" in r.text
    assert _count_orders() == before


def test_a_zero_quantity_on_an_added_line_is_refused(
    client, admin_headers, require_review, monkeypatch
):
    job_id, _ = _awaiting_review(client, admin_headers, monkeypatch, ("土豆", 50, "斤"))
    before = _count_orders()

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"added_lines": [{"product_name": "大米", "quantity": 0}]},
        headers=admin_headers,
    )
    assert r.status_code == 400, f"expected a refusal, got {r.status_code}"
    assert _count_orders() == before


def test_a_reviewer_cannot_correct_the_same_line_twice(
    client, admin_headers, require_review, monkeypatch
):
    """Two edits for one line means the client is confused, and picking one
    would be a guess about which number the person meant."""
    job_id, _ = _awaiting_review(client, admin_headers, monkeypatch, ("土豆", 50, "斤"))
    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"lines": [{"line_no": 1, "quantity": 10}, {"line_no": 1, "quantity": 20}]},
        headers=admin_headers,
    )
    assert r.status_code == 400, r.text
    assert "twice" in r.text


def test_confirming_with_an_empty_body_matches_no_body_at_all(
    client, admin_headers, require_review, monkeypatch, pin_cutoff
):
    """The new body is additive. `{}` and nothing must mean the same thing, or
    a client that always sends a body gets a different order from one that does
    not."""
    pin_cutoff(False)
    job_a, _ = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )
    with_body = client.post(
        f"/api/v1/intake/jobs/{job_a}/confirm-review", json={}, headers=admin_headers
    )
    assert with_body.status_code == 200, f"{with_body.status_code} {with_body.text}"

    job_b, _ = _awaiting_review(
        client, admin_headers, monkeypatch, ("土豆", 50, "斤"), ("大白菜", 30, "斤")
    )
    without_body = client.post(
        f"/api/v1/intake/jobs/{job_b}/confirm-review", headers=admin_headers
    )
    assert without_body.status_code == 200, without_body.text

    a = _order_lines(with_body.json()["order_id"])
    b = _order_lines(without_body.json()["order_id"])
    assert [l["quantity"] for l in a] == [l["quantity"] for l in b]
    assert [l["product_id"] for l in a] == [l["product_id"] for l in b]
    assert [l["match_method"] for l in a] == [l["match_method"] for l in b]


# ---------------------------------------------------------------------------
# Showing the source: the reviewer has to be able to SEE what they are checking
# ---------------------------------------------------------------------------

def test_the_extraction_carries_the_source_it_was_read_from(
    client, admin_headers, require_review, monkeypatch
):
    """Without the source the review screen showed "No preview image for this
    source type" for a TEXT order — a true sentence about an image and a
    useless one about a note."""
    job_id, _ = _awaiting_review(client, admin_headers, monkeypatch, ("土豆", 50, "斤"))

    body = client.get(
        f"/api/v1/intake/extractions/{job_id}", headers=admin_headers
    ).json()
    assert body["source_type"] == "text"
    assert body["source_text"] == DEMO_TEXT
    assert body["raw_output"]["lines"], "the parse must still be there"
    assert body["rejection"] is None


def test_the_original_is_served_inline_with_a_usable_type(
    client, admin_headers, require_review, monkeypatch
):
    """An `<img>` or a PDF viewer handed `application/octet-stream` downloads
    the file instead of showing it — which is the whole failure being fixed."""
    job_id, doc_id = _awaiting_review(client, admin_headers, monkeypatch, ("土豆", 50, "斤"))

    inline = client.get(
        f"/api/v1/intake/documents/{doc_id}/file",
        params={"inline": 1},
        headers=admin_headers,
    )
    assert inline.status_code == 200, inline.text
    assert inline.headers["content-type"].startswith("text/plain")
    assert "inline" in inline.headers.get("content-disposition", "")
    assert inline.text == DEMO_TEXT, "the preview must be the actual source"

    # The download path is unchanged — an opaque type and an attachment.
    download = client.get(
        f"/api/v1/intake/documents/{doc_id}/file", headers=admin_headers
    )
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/octet-stream"
    assert "attachment" in download.headers.get("content-disposition", "")


def test_an_image_original_is_served_as_an_image(
    client, admin_headers, require_review
):
    """A photo order has to render as a photo, or the reviewer is comparing
    the parse against a download prompt."""
    r = client.post(
        "/api/v1/intake/submit",
        data={"source_type": "image", "customer_id": _customer_id(client, admin_headers)},
        files={"file": ("note.png", _TINY_PNG, "image/png")},
        headers=admin_headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    doc_id = r.json()["document_id"]

    inline = client.get(
        f"/api/v1/intake/documents/{doc_id}/file",
        params={"inline": 1},
        headers=admin_headers,
    )
    assert inline.status_code == 200, inline.text
    assert inline.headers["content-type"] == "image/png"
    assert inline.content == _TINY_PNG


def test_a_reviewer_removal_is_not_confused_with_the_customers_own(
    client, admin_headers, require_review, monkeypatch, pin_cutoff
):
    """Two different events with two different meanings.

    A line struck through on the note is the CUSTOMER saying "not this one". A
    line dropped in the drawer is the REVIEWER disagreeing with the extractor.
    An audit record that merges them cannot answer either question — and the
    second one is the measurement of the extractor's error rate.
    """
    pin_cutoff(False)
    from app.ai.adapters import RawLine

    monkeypatch.setattr(
        "app.ai.pipeline.get_extractor",
        lambda: _FixedExtractor([
            RawLine(product_name="土豆", quantity=50, unit="斤"),
            RawLine(product_name="大白菜", quantity=30, unit="斤"),
            # 已关 — the customer cancelled this one on the note itself.
            RawLine(product_name="五花肉", quantity=20, unit="斤", cancelled=True),
        ]),
    )
    customer_id = _customer_id(client, admin_headers)
    job_id = _submit(client, admin_headers, customer_id)
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "needs_review"

    r = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review",
        json={"lines": [{"line_no": 1, "cancelled": True}]},
        headers=admin_headers,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text}"

    # Only line 2 survives: the reviewer dropped 1, the note had already
    # dropped 3.
    lines = _order_lines(r.json()["order_id"])
    assert len(lines) == 1
    assert lines[0]["product_id"] == _product_id(client, admin_headers, "大白菜")

    review = client.get(
        f"/api/v1/intake/extractions/{job_id}", headers=admin_headers
    ).json()["human_review"]
    assert review["cancelled_on_note"] == [3], "the note's own cancellation"
    assert review["removed_by_reviewer"] == [1], "the reviewer's removal"
    assert review["removed_line_nos"] == [1, 3], "and the union of both"
