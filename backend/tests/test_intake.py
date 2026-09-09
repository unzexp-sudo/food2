"""Tests for the AI intake module.

Covers (per the contract):
- text submit → job completes → draft order created with correct lines & confidences
- excel submit (CSV)
- image submit (mock OCR)
- retry
- job/document listing
- file download
- extraction immutable output
- role guard
- multipart form submit

Demo text: "土豆 50斤\n大白菜 30斤\n五花肉 20斤\n大米 2袋"
Seed has: Potato 土豆 (VG001), Chinese Cabbage 大白菜 (VG002), Pork Belly 五花肉 (MT001), Rice 大米 (RG001)
Customer C001 (佛山第一小学) has aliases: 土豆, potato, 洋芋, 大白菜, 五花肉, 大米
"""
from __future__ import annotations

import time

from tests.conftest import admin_headers, client, ops_headers

DEMO_TEXT = "土豆 50斤\n大白菜 30斤\n五花肉 20斤\n大米 2袋"

# Max time to poll a background job before giving up (background tasks run
# synchronously in TestClient, so this is a safety net).
_JOB_TIMEOUT = 10


def _wait_for_job(client, job_id: str, headers: dict, timeout: float = _JOB_TIMEOUT) -> dict:
    """Poll GET /jobs/{id} until status is completed or failed."""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = client.get(f"/api/v1/intake/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, f"job get failed: {r.status_code} {r.text}"
        last = r.json()
        if last["status"] in ("completed", "failed"):
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


def _get_draft_order(client, job: dict, headers: dict) -> dict:
    """Fetch the draft order created by a completed intake job.

    Falls back to a direct DB lookup if the orders API endpoint isn't
    implemented yet (orders module may still be a stub).
    """
    order_id = job["draft_order_id"]
    assert order_id, f"Job has no draft_order_id: {job}"
    r = client.get(f"/api/v1/orders/{order_id}", headers=headers)
    if r.status_code == 200:
        return r.json()
    # Fall back to a direct DB lookup
    from app.core.database import SessionLocal
    from app.models import Order, OrderLine
    with SessionLocal() as db:
        o = db.get(Order, order_id)
        if o is None:
            raise AssertionError(f"Order {order_id} not found in DB")
        lines = (
            db.query(OrderLine)
            .filter(OrderLine.order_id == order_id)
            .order_by(OrderLine.line_no)
            .all()
        )
        return {
            "id": o.id,
            "order_number": o.order_number,
            "customer_id": o.customer_id,
            "status": o.status,
            "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
            "source_type": o.source_type,
            "intake_document_id": o.intake_document_id,
            "overall_confidence": o.overall_confidence,
            "lines": [
                {
                    "line_no": ln.line_no,
                    "raw_text": ln.raw_text,
                    "product_id": ln.product_id,
                    "product_display": ln.product_display,
                    "quantity": ln.quantity,
                    "unit_id": ln.unit_id,
                    "confidence": ln.confidence,
                    "match_method": ln.match_method,
                }
                for ln in lines
            ],
        }


# ---------------------------------------------------------------------------
# 1. Text submit — the headline demo
# ---------------------------------------------------------------------------
def test_text_submit_creates_draft_order(client, admin_headers, pin_cutoff):
    # Auto-confirm only fires before the daily cutoff; pin it off so this
    # test is deterministic at any hour of the day.
    pin_cutoff(False)
    customer_id = _get_customer_id(client, admin_headers, "C001")

    r = client.post(
        "/api/v1/intake/submit",
        json={
            "customer_id": customer_id,
            "source_type": "text",
            "raw_text": DEMO_TEXT,
            "delivery_date": "2026-09-08",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    body = r.json()
    assert "document_id" in body
    assert "job_id" in body
    job_id = body["job_id"]
    doc_id = body["document_id"]

    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "completed", f"Job failed: {job.get('error')}"
    assert job["draft_order_id"] is not None

    order = _get_draft_order(client, job, admin_headers)
    assert order["status"] == "draft"
    assert order["source_type"] == "text"
    assert order["intake_document_id"] == doc_id
    assert order["overall_confidence"] is not None

    lines = order["lines"]
    assert len(lines) == 4, f"expected 4 lines, got {len(lines)}: {lines}"

    # All four should match via customer aliases (C001 has 土豆, 大白菜, 五花肉, 大米)
    for ln in lines:
        assert ln["product_id"] is not None, f"line unmatched: {ln}"
        assert ln["match_method"] == "alias_exact", f"expected alias_exact, got {ln['match_method']}: {ln}"
        assert ln["confidence"] == 1.0

    # Spot-check the first line (土豆 = Potato, 50斤)
    l1 = lines[0]
    assert l1["line_no"] == 1
    assert l1["quantity"] == 50
    # raw_text should contain 土豆
    assert "土豆" in (l1["raw_text"] or "")

    # Overall confidence should be 1.0 (all alias_exact)
    assert order["overall_confidence"] == 1.0


def test_text_submit_unmatched_product_low_confidence(client, admin_headers):
    """A product not in the catalog should be unmatched with low confidence."""
    customer_id = _get_customer_id(client, admin_headers, "C001")
    text = "土豆 50斤\n未知食材 10斤"

    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": text},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "completed"

    order = _get_draft_order(client, job, admin_headers)
    lines = order["lines"]
    assert len(lines) == 2
    # First line matched, second unmatched
    assert lines[0]["match_method"] == "alias_exact"
    assert lines[1]["match_method"] == "unmatched"
    assert lines[1]["product_id"] is None
    assert lines[1]["confidence"] == 0.3
    # Overall confidence < 1.0
    assert order["overall_confidence"] < 1.0


# ---------------------------------------------------------------------------
# 2. Excel submit (CSV)
# ---------------------------------------------------------------------------
def test_csv_submit(client, admin_headers):
    customer_id = _get_customer_id(client, admin_headers, "C001")
    csv_content = "product,quantity,unit\n土豆,50,斤\n大白菜,30,斤\n大米,2,袋\n"
    files = {
        "file": ("order.csv", csv_content.encode("utf-8"), "text/csv"),
    }
    data = {
        "customer_id": customer_id,
        "source_type": "excel",
    }
    r = client.post(
        "/api/v1/intake/submit",
        data=data,
        files=files,
        headers=admin_headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    job_id = r.json()["job_id"]
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "completed", f"Job failed: {job.get('error')}"

    order = _get_draft_order(client, job, admin_headers)
    lines = order["lines"]
    assert len(lines) == 3
    assert lines[0]["match_method"] == "alias_exact"
    assert lines[0]["quantity"] == 50
    assert lines[2]["quantity"] == 2


# ---------------------------------------------------------------------------
# 3. Image submit (mock OCR)
# ---------------------------------------------------------------------------
def test_image_submit_mock_ocr(client, admin_headers):
    customer_id = _get_customer_id(client, admin_headers, "C001")
    # An empty PNG file — the mock extractor ignores the bytes and returns canned lines.
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    files = {"file": ("note.png", png_bytes, "image/png")}
    data = {
        "customer_id": customer_id,
        "source_type": "image",
    }
    r = client.post(
        "/api/v1/intake/submit",
        data=data,
        files=files,
        headers=admin_headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    job_id = r.json()["job_id"]
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "completed", f"Job failed: {job.get('error')}"

    order = _get_draft_order(client, job, admin_headers)
    lines = order["lines"]
    assert len(lines) >= 1
    # Mock OCR returns canned lines (土豆 50斤 etc.) which should match aliases
    for ln in lines:
        assert ln["product_id"] is not None

    # The extraction should note that image OCR is mock.
    r2 = client.get(f"/api/v1/intake/extractions/{job_id}", headers=admin_headers)
    assert r2.status_code == 200
    ext = r2.json()
    assert "mock" in (ext["parser_notes"] or "").lower()


# ---------------------------------------------------------------------------
# 4. Retry
# ---------------------------------------------------------------------------
def test_retry_completed_job(client, admin_headers):
    customer_id = _get_customer_id(client, admin_headers, "C001")
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=admin_headers,
    )
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "completed"
    assert job["retry_count"] == 0

    r2 = client.post(f"/api/v1/intake/jobs/{job_id}/retry", headers=admin_headers)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["retry_count"] == 1
    assert body["status"] == "queued"

    job2 = _wait_for_job(client, job_id, admin_headers)
    assert job2["status"] == "completed"
    assert job2["retry_count"] == 1


# ---------------------------------------------------------------------------
# 5. Document & job listing
# ---------------------------------------------------------------------------
def test_list_documents_and_jobs(client, admin_headers):
    customer_id = _get_customer_id(client, admin_headers, "C001")
    # Submit a doc so there's at least one.
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": "土豆 10斤"},
        headers=admin_headers,
    )
    assert r.status_code == 201
    doc_id = r.json()["document_id"]

    # List documents
    r1 = client.get("/api/v1/intake/documents", headers=admin_headers)
    assert r1.status_code == 200
    docs = r1.json()
    assert docs["total"] >= 1
    assert any(d["id"] == doc_id for d in docs["items"])

    # Filter by customer_id
    r2 = client.get(f"/api/v1/intake/documents?customer_id={customer_id}", headers=admin_headers)
    assert r2.status_code == 200
    for d in r2.json()["items"]:
        assert d["customer_id"] == customer_id

    # Single document
    r3 = client.get(f"/api/v1/intake/documents/{doc_id}", headers=admin_headers)
    assert r3.status_code == 200
    d = r3.json()
    assert d["id"] == doc_id
    assert d["job_id"] is not None  # embedded job summary

    # List jobs
    r4 = client.get("/api/v1/intake/jobs", headers=admin_headers)
    assert r4.status_code == 200
    jobs = r4.json()
    assert jobs["total"] >= 1

    # Filter by status=completed
    r5 = client.get("/api/v1/intake/jobs?status=completed", headers=admin_headers)
    assert r5.status_code == 200
    for j in r5.json()["items"]:
        assert j["status"] == "completed"


def test_document_not_found(client, admin_headers):
    r = client.get("/api/v1/intake/documents/nonexistent-id", headers=admin_headers)
    assert r.status_code == 404


def test_job_not_found(client, admin_headers):
    r = client.get("/api/v1/intake/jobs/nonexistent-id", headers=admin_headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 6. File download
# ---------------------------------------------------------------------------
def test_download_original_file(client, admin_headers):
    customer_id = _get_customer_id(client, admin_headers, "C001")
    text = "土豆 50斤"
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": text},
        headers=admin_headers,
    )
    assert r.status_code == 201
    doc_id = r.json()["document_id"]

    r2 = client.get(f"/api/v1/intake/documents/{doc_id}/file", headers=admin_headers)
    assert r2.status_code == 200
    # Content should be the raw_text we submitted (written as a .txt file).
    body = r2.content
    assert "土豆".encode("utf-8") in body


# ---------------------------------------------------------------------------
# 7. Extraction (immutable raw AI output)
# ---------------------------------------------------------------------------
def test_extraction_immutable_output(client, admin_headers):
    customer_id = _get_customer_id(client, admin_headers, "C001")
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=admin_headers,
    )
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "completed"

    r2 = client.get(f"/api/v1/intake/extractions/{job_id}", headers=admin_headers)
    assert r2.status_code == 200
    ext = r2.json()
    assert ext["job_id"] == job_id
    assert "raw_output" in ext
    raw = ext["raw_output"]
    assert "lines" in raw
    assert len(raw["lines"]) == 4
    assert raw["overall_confidence"] is not None
    assert "parser_notes" in raw
    # The raw_output lines should include matched_product_id for matched lines
    for ln in raw["lines"]:
        assert "raw_text" in ln
        assert "confidence" in ln
        assert "match_method" in ln


# ---------------------------------------------------------------------------
# 8. Role guard
# ---------------------------------------------------------------------------
def test_driver_cannot_submit(client):
    """Drivers should not be able to submit intake (only ops/admin)."""
    from tests.conftest import _auth_headers
    driver_headers = _auth_headers("driver@erp.local")
    r = client.post(
        "/api/v1/intake/submit",
        json={"source_type": "text", "raw_text": "土豆 10斤"},
        headers=driver_headers,
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 9. Multipart form submit
# ---------------------------------------------------------------------------
def test_multipart_form_submit(client, admin_headers):
    customer_id = _get_customer_id(client, admin_headers, "C001")
    data = {
        "customer_id": customer_id,
        "source_type": "text",
        "raw_text": DEMO_TEXT,
    }
    r = client.post(
        "/api/v1/intake/submit",
        data=data,
        headers=admin_headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    job_id = r.json()["job_id"]
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "completed"


# ---------------------------------------------------------------------------
# 10. "0 assumptions" gate: a flagged handwritten note must NOT auto-create an
#     order; a human confirms it via confirm-review, which then creates it
#     (excluding any cancelled lines).
# ---------------------------------------------------------------------------

class _FlaggedHandwrittenExtractor:
    """Stand-in for the real aliyun_qwen extractor on a low-confidence note."""

    def extract(self, *, source_type, raw_text=None, file_path=None, original_filename=None):
        from app.ai.adapters import ExtractionResult, RawLine

        lines = [
            RawLine(product_name="土豆", quantity=50, unit="斤", cancelled=False),
            RawLine(product_name="大白菜", quantity=30, unit="斤", cancelled=False),
            # Customer cancelled this line (e.g. struck through / 已关).
            RawLine(product_name="五花肉", quantity=20, unit="斤", cancelled=True, notes="已关"),
        ]
        return ExtractionResult(
            lines=lines,
            doc_type="handwritten_note",
            form_type="mixed",
            form_type_confidence=0.9,
            overall_confidence=0.6,
            requires_human_review=True,
            cancelled_lines=[{"index": 2, "product_name": "五花肉"}],
            parser_notes="simulated flagged handwritten note",
        )


def test_flagged_handwritten_blocks_order_then_confirm_creates_it(
    client, admin_headers, pin_cutoff, monkeypatch
):
    # Force the pipeline to use our flagged extractor.
    monkeypatch.setattr("app.ai.pipeline.get_extractor", lambda: _FlaggedHandwrittenExtractor())
    # Pin auto-confirm OFF so the new draft order stays in "draft" instead of
    # being transitioned to "pending_confirmation" by the real-clock cutoff
    # logic (the WeCom notify path then tries to call out to a gateway that
    # isn't running in tests).
    pin_cutoff(False)
    customer_id = _get_customer_id(client, admin_headers, "C001")

    r = client.post(
        "/api/v1/intake/submit",
        json={
            "customer_id": customer_id,
            "source_type": "image",
            "raw_text": "handwritten note",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    job_id = r.json()["job_id"]

    job = _wait_for_job(client, job_id, admin_headers)
    # The hard rule: NO order is created automatically for a flagged note.
    assert job["status"] == "needs_review", f"expected needs_review, got {job['status']}: {job.get('error')}"
    assert job["draft_order_id"] is None, "flagged note must not auto-create an order"

    # The extraction payload carries the QA fields for the review screen.
    r2 = client.get(f"/api/v1/intake/extractions/{job_id}", headers=admin_headers)
    assert r2.status_code == 200
    raw = r2.json()["raw_output"]
    assert raw.get("requires_human_review") is True
    assert raw.get("form_type") == "mixed"
    assert raw.get("is_handwritten") is True
    assert raw.get("cancelled_lines") == [{"index": 2, "product_name": "五花肉"}]
    # Per-line cancellation flag is present so the UI can highlight it.
    cancelled = [ln for ln in raw["lines"] if ln.get("cancelled")]
    assert len(cancelled) == 1
    assert "五花肉" in (cancelled[0].get("matched_product_name") or "") or "五花肉" in (
        cancelled[0].get("raw_text") or ""
    )

    # Confirm-review creates the order (excluding the cancelled line).
    r3 = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers
    )
    assert r3.status_code == 200, f"{r3.status_code} {r3.text}"
    body = r3.json()
    assert body["order_id"]
    assert body["status"] == "draft"

    # After confirm, the job is completed and the order exists.
    job2 = client.get(f"/api/v1/intake/jobs/{job_id}", headers=admin_headers).json()
    assert job2["status"] == "completed"
    assert job2["draft_order_id"] == body["order_id"]

    order = _get_draft_order(client, job2, admin_headers)
    # Cancelled line excluded: 3 parsed, 2 ordered.
    assert len(order["lines"]) == 2, f"cancelled line should be excluded: {order['lines']}"
    product_names = " ".join(ln["raw_text"] for ln in order["lines"])
    assert "五花肉" not in product_names, "cancelled line must not appear in the order"


def test_confirm_review_rejects_non_review_job(client, admin_headers, monkeypatch):
    """confirm-review must refuse a job that isn't awaiting review."""
    customer_id = _get_customer_id(client, admin_headers, "C001")
    r = client.post(
        "/api/v1/intake/submit",
        json={"customer_id": customer_id, "source_type": "text", "raw_text": DEMO_TEXT},
        headers=admin_headers,
    )
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "completed"  # clean text note auto-completes

    r2 = client.post(
        f"/api/v1/intake/jobs/{job_id}/confirm-review", headers=admin_headers
    )
    assert r2.status_code == 400, "confirm-review must reject a non-review job"
