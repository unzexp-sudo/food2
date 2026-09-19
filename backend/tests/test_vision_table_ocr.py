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
from app.ai.adapters import (
    MistralOcrExtractor,
    VisionTableExtractor,
    get_extractor,
)
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
    # Read from settings rather than a literal: which vision model this account
    # is licensed for is a deployment fact, not a code invariant. Pinning the
    # literal here is what would hide an "Invalid model" 400.
    assert body["model"] == settings.pixtral_ocr_model


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


# --- the automatic rescue -----------------------------------------------------
#
# The vision extractor is switched on by `image_ocr_provider=pixtral`, but that
# switch alone leaves a choice between two bad defaults: "mistral" loses dense
# tables, "pixtral" forces EVERY photo through the slower, pricier, score-less
# model. So the OCR path rescues itself instead — it re-reads only the page that
# came back as a figure. These tests pin that the rescue fires on the pages that
# need it, stays silent on the ones that do not, and can never turn an intake
# failure into an intake outage.

# The real 14-row order's markdown, verbatim. The placeholder is a markdown LINK
# `[tbl-0.md](tbl-0.md)` — on the raw line it is not a filename at all, which is
# why the reference has to be counted after markdown normalisation.
FIGURE_ONLY_MARKDOWN = (
    "# 广东崇元绿色食品有限公司\n\n"
    "采购单位: 广东来赫生餐饮有限公司\n\n"
    "打印时间: 2026-09-02 20:49:01\n\n"
    "任务数: 14\n\n"
    "[tbl-0.md](tbl-0.md)"
)


def _ocr_page(markdown: str, figures: int = 0) -> dict:
    return {
        "pages": [{
            "index": 0,
            "markdown": markdown,
            "images": [{"id": f"img-{i}.jpeg"} for i in range(figures)],
        }],
        "model": "mistral-ocr-latest",
        "usage_info": {"pages_processed": 1},
    }


@pytest.fixture
def rescue_on(monkeypatch):
    """OCR provider, with the rescue armed. The default in production."""
    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "image_ocr_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "vision_fallback_enabled", True)


def _capture_both(monkeypatch, ocr_payload: dict, chat_payload: dict) -> list[dict]:
    """Route faked POSTs by URL: /ocr gets one answer, chat gets the other."""
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        return _FakeResponse(
            chat_payload if "chat/completions" in url else ocr_payload
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_a_figure_only_page_is_re_read_and_the_rows_come_back(
    tmp_path, monkeypatch, rescue_on
):
    """The whole point of the rescue: 14 rows lost to a cropped figure should
    come back as rows, not as a note telling someone to transcribe them."""
    calls = _capture_both(
        monkeypatch,
        _ocr_page(FIGURE_ONLY_MARKDOWN, figures=1),
        _reply({"lines": [
            {"line_no": 1, "product_name": "土豆", "total_quantity": 50, "unit": "斤"},
            {"line_no": 2, "product_name": "大白菜", "total_quantity": 30, "unit": "斤"},
        ]}),
    )

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert [(l.product_name, l.quantity) for l in result.lines] == [
        ("土豆", 50.0),
        ("大白菜", 30.0),
    ]
    # Both reads happened, and the reviewer can see why the second was needed.
    assert any("/ocr" in c["url"] for c in calls)
    assert any("chat/completions" in c["url"] for c in calls)
    assert "figure" in result.parser_notes
    # 任务数 was the header field the old code emitted as a real product.
    assert "任务数" not in [l.product_name for l in result.lines]


def test_the_rescue_fires_on_a_reference_when_the_vendor_returns_no_images(
    tmp_path, monkeypatch, rescue_on
):
    """Exactly the production failure.

    `include_image_base64` is off, so pages carry no `images` array and the
    figure count is 0. The rescue originally keyed on that count, so on the real
    14-row order it never ran and the page stayed at four header lines. The
    `tbl-0.md` reference in the markdown is what identifies the page.
    """
    calls = _capture_both(
        monkeypatch,
        _ocr_page(FIGURE_ONLY_MARKDOWN, figures=0),
        _reply({"lines": [
            {"line_no": 1, "product_name": "土豆", "total_quantity": 50, "unit": "斤"},
            {"line_no": 2, "product_name": "大白菜", "total_quantity": 30, "unit": "斤"},
        ]}),
    )

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert [(l.product_name, l.quantity) for l in result.lines] == [
        ("土豆", 50.0),
        ("大白菜", 30.0),
    ]
    assert any("chat/completions" in c["url"] for c in calls)


def test_the_rescue_does_not_fire_when_the_ocr_read_real_quantities(
    tmp_path, monkeypatch, rescue_on
):
    """A photo can carry a stamp or logo figure alongside a table that WAS read.
    Rescuing that would pay for a vision call to re-read a page we already have
    — and would replace confident, scored lines with unscored ones."""
    calls = _capture_both(
        monkeypatch,
        _ocr_page("土豆 50斤\n大白菜 30斤", figures=2),
        _reply({"lines": []}),
    )

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="note.png"
    )

    assert [(l.product_name, l.quantity) for l in result.lines] == [
        ("土豆", 50.0),
        ("大白菜", 30.0),
    ]
    assert not any("chat/completions" in c["url"] for c in calls)


def test_the_rescue_stays_off_when_it_is_switched_off(tmp_path, monkeypatch, rescue_on):
    """`vision_fallback_enabled=false` must mean exactly what it said before the
    rescue existed: flag the page, let a human transcribe it."""
    monkeypatch.setattr(settings, "vision_fallback_enabled", False)
    calls = _capture_both(
        monkeypatch,
        _ocr_page(FIGURE_ONLY_MARKDOWN, figures=1),
        _reply({"lines": [{"product_name": "土豆", "total_quantity": 50}]}),
    )

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert result.lines == []
    assert result.requires_human_review is True
    assert not any("chat/completions" in c["url"] for c in calls)


def test_a_pdf_is_never_rescued(tmp_path, monkeypatch, rescue_on):
    """Recursion guard. The vision extractor hands a scanned PDF straight back to
    MistralOcrExtractor, so rescuing a PDF here would loop forever."""
    path = tmp_path / "scan.pdf"
    path.write_bytes(PDF_BYTES)
    # No text layer, so this really is a scan and does reach the OCR path —
    # which is the only place the rescue could fire from.
    monkeypatch.setattr(
        adapters, "parse_pdf_lines", lambda p: ([], "scanned_pdf_no_text")
    )
    calls = _capture_both(
        monkeypatch,
        _ocr_page(FIGURE_ONLY_MARKDOWN, figures=1),
        _reply({"lines": [{"product_name": "土豆", "total_quantity": 50}]}),
    )

    result = MistralOcrExtractor().extract(
        source_type="pdf", file_path=str(path), original_filename="scan.pdf"
    )

    assert not any("chat/completions" in c["url"] for c in calls)
    assert result.lines == []
    assert result.requires_human_review is True


def test_a_failed_vision_call_leaves_the_document_flagged_not_broken(
    tmp_path, monkeypatch, rescue_on
):
    """The rescue is a bonus, never a dependency. An unlicensed model, a timeout
    or a 401 must degrade to the pre-rescue outcome — flagged for review — and
    must never raise into the intake pipeline."""
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url})
        if "chat/completions" in url:
            raise httpx.HTTPStatusError("HTTP 401", request=None, response=None)
        return _FakeResponse(_ocr_page(FIGURE_ONLY_MARKDOWN, figures=1))

    monkeypatch.setattr(httpx, "post", fake_post)

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert result.lines == []
    assert result.requires_human_review is True
    assert "not available" in result.parser_notes


def test_a_rescue_that_reads_nothing_is_still_flagged(tmp_path, monkeypatch, rescue_on):
    """A vision model that finds no rows is not an empty order — same rule as the
    OCR path. It must say so and stay in review."""
    _capture_both(
        monkeypatch,
        _ocr_page(FIGURE_ONLY_MARKDOWN, figures=1),
        _reply({"lines": []}),
    )

    result = MistralOcrExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert result.lines == []
    assert result.requires_human_review is True
    assert "no lines" in result.parser_notes


def test_a_page_missing_from_disk_is_not_rescued(tmp_path, monkeypatch, rescue_on):
    """Disk is a best-effort cache; the original lives in the database. If the
    bytes are gone there is nothing to hand the vision model, and that must be
    answered with None rather than an exception.

    Exercised on the guard directly: the OCR read itself opens the file first, so
    a genuinely vanished file never reaches the rescue in the normal flow.
    """
    calls = _capture_both(
        monkeypatch,
        _ocr_page(FIGURE_ONLY_MARKDOWN, figures=1),
        _reply({"lines": [{"product_name": "土豆", "total_quantity": 50}]}),
    )

    assert MistralOcrExtractor()._vision_rescue(
        source_type="image",
        file_path=str(tmp_path / "gone.png"),
        original_filename="order.png",
        figures=1,
    ) is None
    assert not any("chat/completions" in c["url"] for c in calls)


def test_a_reply_that_renames_the_fields_is_still_read(tmp_path, monkeypatch, pixtral_on):
    """The model does not honour the requested schema, and the reply is still
    correct — so it must not be discarded over a key name.

    Verbatim behaviour on the real 14-row order: `recipient` where the prompt
    asked for `customer_name`, `unit` for `total_unit`, `note` for `notes`, and
    the JSON wrapped in prose and a ```json fence. Every product, quantity and
    destination in it was right.
    """
    reply = (
        "Here is the transcription of the table in JSON format based on the "
        "provided image:\n\n```json\n"
        + json.dumps(
            {
                "supplier": "广东岳元绿色食品有限公司",
                "lines": [
                    {
                        "line_no": 2,
                        "product_name": "小豆腐",
                        "total_quantity": 524,
                        "unit": "斤",
                        "breakdowns": [
                            {"recipient": "佛山市潮连高级中学", "quantity": 25},
                            {"recipient": "广州市老人院", "quantity": 400, "note": "午餐"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )
    _capture(monkeypatch, _chat_reply(reply))

    result = VisionTableExtractor().extract(
        source_type="image", file_path=_img(tmp_path), original_filename="order.png"
    )

    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.product_name == "小豆腐"
    assert line.quantity == 524.0
    assert line.unit == "斤"
    # The per-customer split is the valuable half of this table; losing the
    # destination because it was called `recipient` would lose the delivery.
    assert [bd["customer_name"] for bd in line.breakdowns] == [
        "佛山市潮连高级中学",
        "广州市老人院",
    ]
    assert [bd["quantity"] for bd in line.breakdowns] == [25.0, 400.0]
