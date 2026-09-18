"""Mistral OCR extractor — the request we send, and the failure we refuse.

The interesting assertions here are not "does it parse markdown". They are:

  * the media type in the `data:` URI comes from the BYTES, not the filename;
  * a PDF with a text layer is never sent to a paid vision model;
  * **a failed call raises, and never returns the canned mock lines** — the
    failure mode this codebase already paid for once (a document nobody read
    that looks read, because the fallback output is plausible real products).
"""
from __future__ import annotations

import httpx
import pytest

from app.ai import adapters
from app.ai.adapters import MistralOcrExtractor, get_extractor
from app.core.config import settings

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 32


class _FakeResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )


def _page(markdown: str, score: float | None = None) -> dict:
    page: dict = {"index": 0, "markdown": markdown, "images": []}
    if score is not None:
        page["confidence_scores"] = {"average_page_confidence_score": score}
    return page


def _payload(markdown: str, score: float | None = None) -> dict:
    return {
        "pages": [_page(markdown, score)],
        "model": "mistral-ocr-latest",
        "usage_info": {"pages_processed": 1},
    }


@pytest.fixture
def mistral_on(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")


def _capture(monkeypatch, payload: dict, status_code: int = 200) -> list[dict]:
    """Patch httpx.post, recording every request. Returns the call log."""
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(payload, status_code)

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


# --- the request we send ------------------------------------------------------

def test_an_image_goes_as_a_data_uri_typed_from_its_bytes(tmp_path, monkeypatch, mistral_on):
    """A PNG stored under a `.jpg` name must still be announced as image/png —
    the media type is part of the request, so a wrong one is a rejected call."""
    path = tmp_path / "note.jpg"
    path.write_bytes(PNG_BYTES)
    calls = _capture(monkeypatch, _payload("土豆 50斤"))

    MistralOcrExtractor().extract(
        source_type="image", file_path=str(path), original_filename="note.jpg"
    )

    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.mistral.ai/v1/ocr"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"
    document = calls[0]["json"]["document"]
    assert document["type"] == "image_url"
    assert document["image_url"].startswith("data:image/png;base64,")
    assert calls[0]["json"]["model"] == "mistral-ocr-latest"


def test_a_jpeg_is_still_a_jpeg(tmp_path, monkeypatch, mistral_on):
    path = tmp_path / "note.jpg"
    path.write_bytes(JPEG_BYTES)
    calls = _capture(monkeypatch, _payload("土豆 50斤"))

    MistralOcrExtractor().extract(
        source_type="image", file_path=str(path), original_filename="note.jpg"
    )

    assert calls[0]["json"]["document"]["image_url"].startswith("data:image/jpeg;base64,")


def test_a_scanned_pdf_goes_as_a_document_chunk_with_its_name(tmp_path, monkeypatch, mistral_on):
    """The PDF has no text layer, so it IS a scan and must be OCR'd. It carries
    `document_name` so the vendor-side record names the file instead of a wall
    of base64."""
    path = tmp_path / "scan.pdf"
    path.write_bytes(PDF_BYTES)
    monkeypatch.setattr(adapters, "parse_pdf_lines", lambda p: ([], "scanned_pdf_no_text"))
    calls = _capture(monkeypatch, _payload("大白菜 30斤"))

    MistralOcrExtractor().extract(
        source_type="pdf", file_path=str(path), original_filename="scan.pdf"
    )

    assert len(calls) == 1
    document = calls[0]["json"]["document"]
    assert document["type"] == "document_url"
    assert document["document_url"].startswith("data:application/pdf;base64,")
    assert document["document_name"] == "scan.pdf"


def test_a_pdf_with_a_text_layer_is_never_sent_to_mistral(tmp_path, monkeypatch, mistral_on):
    """pypdf already gives us the exact characters for free. Sending it to a
    paid vision model is waste, not extra accuracy."""
    path = tmp_path / "invoice.pdf"
    path.write_bytes(PDF_BYTES)
    monkeypatch.setattr(adapters, "parse_pdf_lines", lambda p: (
        [adapters.RawLine(product_name="土豆", quantity=50, unit="斤")],
        "pdf_text_extracted",
    ))
    calls = _capture(monkeypatch, _payload("SHOULD NOT BE USED"))

    result = MistralOcrExtractor().extract(
        source_type="pdf", file_path=str(path), original_filename="invoice.pdf"
    )

    assert calls == []
    assert [l.product_name for l in result.lines] == ["土豆"]


def test_non_ocr_sources_go_straight_to_the_deterministic_parser(monkeypatch, mistral_on):
    calls = _capture(monkeypatch, _payload("SHOULD NOT BE USED"))

    result = MistralOcrExtractor().extract(source_type="text", raw_text="土豆 50斤")

    assert calls == []
    assert [l.product_name for l in result.lines] == ["土豆"]


# --- what we do with the answer ----------------------------------------------

def test_markdown_becomes_the_same_lines_typed_text_would(tmp_path, monkeypatch, mistral_on):
    path = tmp_path / "note.png"
    path.write_bytes(PNG_BYTES)
    _capture(monkeypatch, _payload("土豆 50斤\n大白菜 30斤\n五花肉 20斤"))

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=str(path), original_filename="note.png"
    )

    assert [(l.product_name, l.quantity, l.unit) for l in result.lines] == [
        ("土豆", 50.0, "斤"),
        ("大白菜", 30.0, "斤"),
        ("五花肉", 20.0, "斤"),
    ]
    assert result.doc_type == "ocr_image"
    assert result.image_path == str(path)


def test_page_confidence_is_carried_onto_every_line(tmp_path, monkeypatch, mistral_on):
    path = tmp_path / "note.png"
    path.write_bytes(PNG_BYTES)
    _capture(monkeypatch, _payload("土豆 50斤", score=0.97))

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=str(path), original_filename="note.png"
    )

    assert result.overall_confidence == 0.97
    assert all(l.confidence == 0.97 for l in result.lines)


def test_no_confidence_scores_leaves_the_document_flagged(tmp_path, monkeypatch, mistral_on):
    """Absent confidence reads as "unverified", which the gate treats as a
    reason to review. The degradation must fail safe, not approve.

    Note the gate does not leave `overall_confidence` as None — it fills in a
    sub-floor worst case — so the assertion that matters is the review flag."""
    path = tmp_path / "note.png"
    path.write_bytes(PNG_BYTES)
    _capture(monkeypatch, _payload("土豆 50斤"))

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=str(path), original_filename="note.png"
    )

    assert result.requires_human_review is True
    assert result.overall_confidence < settings.ocr_field_confidence_floor
    assert "unverified" in result.parser_notes


def test_a_rejected_confidence_field_retries_without_it(tmp_path, monkeypatch, mistral_on):
    """`confidence_scores_granularity` is optional. If the deployment rejects
    it we drop it rather than fail an intake over a nice-to-have — and say so."""
    path = tmp_path / "note.png"
    path.write_bytes(PNG_BYTES)
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse({"detail": "unknown field"}, status_code=400)
        return _FakeResponse(_payload("土豆 50斤"))

    monkeypatch.setattr(httpx, "post", fake_post)

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=str(path), original_filename="note.png"
    )

    assert len(calls) == 2
    assert "confidence_scores_granularity" in calls[0]
    assert "confidence_scores_granularity" not in calls[1]
    assert [l.product_name for l in result.lines] == ["土豆"]
    assert "confidence scores unavailable" in result.parser_notes


# --- the failure we refuse ----------------------------------------------------

def test_a_failed_call_raises_and_never_returns_canned_lines(tmp_path, monkeypatch, mistral_on):
    """THE important one. `mock_ocr_lines()` returns 土豆/大白菜/五花肉 — real
    catalog products — so silently substituting it for a failed OCR call
    produces a plausible, complete, entirely invented order. An error must stay
    an error."""
    path = tmp_path / "note.png"
    path.write_bytes(PNG_BYTES)

    def boom(*args, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "post", boom)

    with pytest.raises(httpx.ConnectError):
        MistralOcrExtractor().extract(
            source_type="image", file_path=str(path), original_filename="note.png"
        )

    # And the canned lines are not reachable from this path at all.
    assert [l.product_name for l in adapters.mock_ocr_lines("note.png")] == [
        "土豆", "大白菜", "五花肉",
    ]


def test_an_empty_attachment_is_refused(tmp_path, monkeypatch, mistral_on):
    path = tmp_path / "empty.png"
    path.write_bytes(b"")
    calls = _capture(monkeypatch, _payload("土豆 50斤"))

    with pytest.raises(RuntimeError):
        MistralOcrExtractor().extract(
            source_type="image", file_path=str(path), original_filename="empty.png"
        )

    assert calls == []


def test_the_extractor_refuses_to_run_without_a_key(tmp_path, monkeypatch):
    """A missing key must not quietly become the mock provider."""
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "")
    path = tmp_path / "note.png"
    path.write_bytes(PNG_BYTES)

    with pytest.raises(RuntimeError, match="ERP_MISTRAL_API_KEY"):
        MistralOcrExtractor().extract(
            source_type="image", file_path=str(path), original_filename="note.png"
        )


# --- factory + health ---------------------------------------------------------

def test_the_factory_builds_mistral_when_configured(monkeypatch, mistral_on):
    assert isinstance(get_extractor(), MistralOcrExtractor)


def test_a_placeholder_key_does_not_count_as_configured(tmp_path, monkeypatch):
    """Railway rejects an empty variable value, so the variable gets seeded with
    a placeholder to be filled in later. That must not read as a working
    configuration — the API would be attempted and fail, and /api/health would
    claim photos are being read when they are not."""
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "REPLACE_ME_WITH_YOUR_MISTRAL_API_KEY")

    assert settings.mistral_ocr_is_configured is False
    assert settings.image_extraction_is_simulated is True

    path = tmp_path / "note.png"
    path.write_bytes(PNG_BYTES)
    with pytest.raises(RuntimeError, match="placeholder"):
        MistralOcrExtractor().extract(
            source_type="image", file_path=str(path), original_filename="note.png"
        )


def test_a_real_looking_key_is_not_mistaken_for_a_placeholder():
    """The check decides whether a credential is reported as configured, so a
    false positive would hide a working deployment. Only obvious markers count."""
    from app.core.config import Settings

    for key in ("sk-abc123def456", "Xk9Qm2Lp7Rt4Vw1Yz8Nb", "mistral-key-2026"):
        assert Settings(
            ai_provider="mistral", mistral_api_key=key
        ).mistral_ocr_is_configured is True


def test_the_factory_still_builds_mistral_without_a_key(monkeypatch):
    """The factory must NOT raise on a missing key, because the pipeline calls
    it for every document before it knows the source type.

    If it raised, selecting Mistral but not yet pasting the key would take down
    typed-text intake — orders that need no OCR and work fine today. The refusal
    belongs at the point OCR is actually required, which is what the extractor
    tests above assert.
    """
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "")

    assert isinstance(get_extractor(), MistralOcrExtractor)


def test_text_intake_keeps_working_while_mistral_is_unkeyed(monkeypatch):
    """The concrete consequence of the above: a typed order must still parse."""
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "")

    result = get_extractor().extract(source_type="text", raw_text="土豆 50斤")

    assert [(l.product_name, l.quantity) for l in result.lines] == [("土豆", 50.0)]


def test_a_typed_order_still_reaches_a_draft_order_with_mistral_selected(
    client, admin_headers, monkeypatch, pin_cutoff
):
    """End-to-end through the real pipeline, which is where the risk actually
    lives: the provider is flipped to Mistral before the key is pasted, and a
    customer's typed order arrives in the meantime.

    It must still become a draft order. A factory-level key check would have
    failed this job — taking down a feature that has nothing to do with OCR.
    """
    from tests.test_intake import DEMO_TEXT, _get_customer_id, _wait_for_job

    pin_cutoff(False)
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "")

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

    job = _wait_for_job(client, r.json()["job_id"], admin_headers)

    assert job["status"] == "completed", f"text intake broke: {job.get('error')}"
    assert job["draft_order_id"] is not None


def test_health_stops_calling_images_simulated_once_mistral_is_keyed(client, monkeypatch, mistral_on):
    body = client.get("/api/health").json()
    assert body["ai_provider"] == "mistral"
    assert body["image_extraction_is_simulated"] is False


def test_health_still_warns_when_mistral_is_selected_but_unkeyed(client, monkeypatch):
    """Selecting a real provider without credentials must not read as healthy:
    no photo is being read either way, and that is what the flag is for."""
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "")

    body = client.get("/api/health").json()
    assert body["image_extraction_is_simulated"] is True
