"""Vision table read (Pixtral chat) — the replacement for a figure-only OCR.

The /ocr endpoint answered a real 14-row supplier order with the page header and
a cropped-figure reference. `MistralOcrExtractor` now detects that and drops the
header lines, but detecting it is not the same as READING the page — which is
what this extractor is for. It hands the image to a vision chat model with an
explicit prompt, so there is nowhere for the table to be left out.

What these tests pin:

  * the image is sent as an image_url content part at temperature 0;
  * the reply's JSON becomes lines, and the page HEADER is not one of them;
  * an unreadable quantity stays None — never 0, because 0 reads as confirmed;
  * an unparseable reply is a failed read, never an empty order;
  * sources that need no vision (typed text, spreadsheets) never pay for one.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.ai import adapters
from app.ai.adapters import VisionTableExtractor, get_extractor
from app.core.config import settings

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
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


def _chat_reply(content: str, model: str = "pixtral-large-latest") -> dict:
    return {"choices": [{"message": {"content": content}}], "model": model}


def _reply(obj: dict, **kw) -> dict:
    return _chat_reply(json.dumps(obj, ensure_ascii=False), **kw)


@pytest.fixture
def pixtral_on(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "image_ocr_provider", "pixtral")
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")


def _capture(monkeypatch, payload: dict, status_code: int = 200) -> list[dict]:
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeResponse(payload, status_code)

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def _img(tmp_path, name: str = "order.png") -> str:
    path = tmp_path / name
    path.write_bytes(PNG_BYTES)
    return str(path)


# --- the request we send ------------------------------------------------------

def test_the_factory_returns_the_vision_extractor_when_selected(monkeypatch, pixtral_on):
    assert isinstance(get_extractor(), VisionTableExtractor)


def test_the_factory_still_returns_the_ocr_extractor_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "image_ocr_provider", "mistral")
    assert type(get_extractor()).__name__ == "MistralOcrExtractor"


def test_the_image_is_sent_as_a_vision_content_part(tmp_path, monkeypatch, pixtral_on):
    """The whole point: the model is given the IMAGE, not a markdown projection
    of it that may have dropped the table."""
    calls = _capture(monkeypatch, _reply({"lines": []}))

    VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert len(calls) == 1
    assert calls[0]["url"].endswith("/chat/completions")
    body = calls[0]["json"]
    content = body["messages"][0]["content"]
    kinds = [c["type"] for c in content]
    assert kinds == ["text", "image_url"]
    image_part = next(c for c in content if c["type"] == "image_url")
    assert image_part["image_url"].startswith("data:image/png;base64,")
    # Transcription, not composition: a model that is allowed to wander will
    # "correct" a quantity it misread into a plausible one.
    assert body["temperature"] == 0.0
    assert body["model"] == "pixtral-large-latest"


def test_the_prompt_forbids_inventing_a_product(tmp_path, monkeypatch, pixtral_on):
    """The instructions are part of the contract. If they ever drop the
    "never guess" rule, a misread cell becomes a fabricated line item."""
    calls = _capture(monkeypatch, _reply({"lines": []}))

    VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    prompt = calls[0]["json"]["messages"][0]["content"][0]["text"]
    assert "null" in prompt
    assert "Never guess" in prompt
    assert "采购单位" in prompt, "the header must be named as NOT a row"


# --- what we do with the answer ----------------------------------------------

def test_rows_become_lines_and_the_header_does_not(tmp_path, monkeypatch, pixtral_on):
    """The exact failure the OCR path had: 采购单位 / 打印时间 / 任务数 were
    emitted as order lines. Here they land in the header instead."""
    _capture(monkeypatch, _reply({
        "supplier": "广东崇元绿色食品有限公司",
        "purchase_unit": "广东来赫生餐饮有限公司",
        "print_time": "2026-09-02 20:49:01",
        "task_count": 14,
        "lines": [
            {"line_no": 1, "product_name": "中板豆腐", "total_quantity": 67.0,
             "total_unit": "板", "breakdowns": []},
            {"line_no": 2, "product_name": "老豆腐", "total_quantity": 20.0,
             "total_unit": "斤", "breakdowns": []},
        ],
    }))

    result = VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert [(l.product_name, l.quantity, l.unit) for l in result.lines] == [
        ("中板豆腐", 67.0, "板"),
        ("老豆腐", 20.0, "斤"),
    ]
    assert result.doc_type == "supplier_order_table"
    assert result.structured is not None
    assert result.structured["header"]["task_count"] == 14
    assert result.structured["header"]["purchase_unit"] == "广东来赫生餐饮有限公司"
    # The header is metadata, never a line.
    assert "广东崇元绿色食品有限公司" not in [l.product_name for l in result.lines]


def test_breakdowns_ride_along_with_their_line(tmp_path, monkeypatch, pixtral_on):
    """Per-customer delivery detail is the reason the structured shape exists;
    it must survive the trip through RawLine."""
    _capture(monkeypatch, _reply({
        "lines": [{
            "line_no": 1,
            "product_name": "中板豆腐",
            "total_quantity": 67.0,
            "total_unit": "板",
            "breakdowns": [
                {"customer_name": "客户的档口", "quantity": 40.0, "unit": "板"},
                {"customer_name": "客户B档口", "quantity": 27.0, "unit": "板"},
            ],
        }],
    }))

    result = VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    line = result.lines[0]
    assert len(line.breakdowns) == 2
    assert [b["customer_name"] for b in line.breakdowns] == ["客户的档口", "客户B档口"]
    assert [b["quantity"] for b in line.breakdowns] == [40.0, 27.0]


def test_an_unreadable_quantity_stays_none_and_is_counted(tmp_path, monkeypatch, pixtral_on):
    """The prompt tells the model to answer null rather than guess. Coercing
    that to 0 would be worse than nothing: 0 reads as a CONFIRMED quantity on a
    line that was never actually read."""
    _capture(monkeypatch, _reply({
        "lines": [
            {"line_no": 1, "product_name": "土豆", "total_quantity": 50.0,
             "total_unit": "斤", "breakdowns": []},
            {"line_no": 2, "product_name": "大白菜", "total_quantity": None,
             "total_unit": "斤", "breakdowns": []},
        ],
    }))

    result = VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    by_name = {l.product_name: l.quantity for l in result.lines}
    assert by_name["土豆"] == 50.0
    assert by_name["大白菜"] is None, "an unread quantity must not become 0"
    assert "1 line(s) missing a quantity" in result.parser_notes


def test_a_row_with_no_product_name_is_skipped(tmp_path, monkeypatch, pixtral_on):
    """A nameless row is not a line anyone can act on. Emitting a blank row for
    the reviewer to delete is noise."""
    _capture(monkeypatch, _reply({
        "lines": [
            {"line_no": 1, "product_name": "土豆", "total_quantity": 5.0,
             "total_unit": "斤", "breakdowns": []},
            {"line_no": 2, "product_name": "", "total_quantity": 9.0,
             "total_unit": "斤", "breakdowns": []},
            {"line_no": 3, "product_name": "   ", "total_quantity": 3.0,
             "total_unit": "斤", "breakdowns": []},
        ],
    }))

    result = VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert [l.product_name for l in result.lines] == ["土豆"]


def test_a_fenced_reply_is_still_parsed(tmp_path, monkeypatch, pixtral_on):
    """Models wrap JSON in ```json fences despite being told not to. A fence is
    formatting, not a failed read."""
    _capture(monkeypatch, _chat_reply(
        '```json\n{"lines": [{"line_no": 1, "product_name": "土豆",'
        ' "total_quantity": 50, "total_unit": "斤", "breakdowns": []}]}\n```'
    ))

    result = VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert [l.product_name for l in result.lines] == ["土豆"]


def test_an_unparseable_reply_is_a_failed_read_not_an_empty_order(
    tmp_path, monkeypatch, pixtral_on
):
    """A JSON error must never masquerade as "the page had no lines" — that
    would be an unread document presented as a read one."""
    _capture(monkeypatch, _chat_reply("I'm sorry, I cannot help with that."))

    result = VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert result.lines == []
    assert result.requires_human_review is True
    assert "not parseable" in result.parser_notes


def test_no_lines_at_all_is_flagged_for_review(tmp_path, monkeypatch, pixtral_on):
    _capture(monkeypatch, _reply({"lines": []}))

    result = VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert result.lines == []
    assert result.requires_human_review is True
    assert "no line items" in result.parser_notes


def test_a_vision_read_reports_no_confidence_so_every_line_is_flagged(
    tmp_path, monkeypatch, pixtral_on
):
    """A chat model gives no score. None is the honest value, and the review
    gate reads None as unverified — so nothing is auto-approved on faith."""
    _capture(monkeypatch, _reply({
        "lines": [{"line_no": 1, "product_name": "土豆", "total_quantity": 50.0,
                   "total_unit": "斤", "breakdowns": []}],
    }))

    result = VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert all(l.confidence is None for l in result.lines)
    assert result.requires_human_review is True


# --- what never pays for a vision call ---------------------------------------

def test_typed_text_never_reaches_the_vision_model(monkeypatch, pixtral_on):
    calls = _capture(monkeypatch, _reply({"lines": []}))

    result = VisionTableExtractor().extract(source_type="text", raw_text="土豆 50斤")

    assert calls == []
    assert [l.product_name for l in result.lines] == ["土豆"]


def test_a_scanned_pdf_falls_back_to_the_ocr_path(tmp_path, monkeypatch, pixtral_on):
    """Rendering a PDF to pages is separate work. Until then a scan goes to the
    OCR endpoint rather than regressing to nothing."""
    path = tmp_path / "scan.pdf"
    path.write_bytes(PDF_BYTES)
    monkeypatch.setattr(adapters, "parse_pdf_lines", lambda p: ([], "scanned_pdf_no_text"))
    calls = _capture(monkeypatch, {
        "pages": [{
            "index": 0,
            "markdown": "土豆 50斤",
            "images": [],
            "confidence_scores": {"average_page_confidence_score": 0.9},
        }],
    })

    result = VisionTableExtractor().extract(
        source_type="pdf", file_path=str(path), original_filename="scan.pdf"
    )

    assert calls[0]["url"].endswith("/ocr")
    assert [l.product_name for l in result.lines] == ["土豆"]


def test_an_empty_attachment_is_refused_rather_than_read(monkeypatch, pixtral_on, tmp_path):
    path = tmp_path / "empty.png"
    path.write_bytes(b"")
    _capture(monkeypatch, _reply({"lines": []}))

    with pytest.raises(RuntimeError, match="empty"):
        VisionTableExtractor().extract(
            source_type="image", file_path=str(path), original_filename="empty.png"
        )


def test_a_missing_key_is_refused_rather_than_falling_back_to_mock(tmp_path, monkeypatch):
    """Same rule as the OCR path: falling back to the mock invents line items,
    which is the one failure this codebase refuses to have."""
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "image_ocr_provider", "pixtral")
    monkeypatch.setattr(settings, "mistral_api_key", "")
    _capture(monkeypatch, _reply({"lines": []}))

    with pytest.raises(RuntimeError, match="ERP_MISTRAL_API_KEY"):
        VisionTableExtractor().extract(
            source_type="image", file_path=_img(tmp_path), original_filename="order.png"
        )
