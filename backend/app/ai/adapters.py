"""AI extractor adapters.

- MockExtractor: deterministic, offline — parses text/excel/csv/pdf/image into
  raw lines {product_name, quantity, unit, notes}. This is the tested path and
  the demo provider (no API key needed). For an IMAGE or a scanned PDF it does
  not read the document at all; it returns canned lines and flags them.
- MistralOcrExtractor: real OCR for images + scanned PDFs via Mistral's
  document AI endpoint. Reads the page, returns markdown, parsed by the same
  line parser the typed-text path uses.
- AliyunQwenExtractor: two-stage Aliyun OCR -> Qwen-VL structuring.
- OpenAIExtractor: stub that would call settings.openai_base_url with the model.
  Since no key is configured in the demo, it falls back to MockExtractor output
  with a parser_notes note. Tests use mock only.

The real parsing logic lives in MockExtractor so the demo works without any
external API.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime as _dt
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger("erp.ai.adapters")


@dataclass
class RawLine:
    """A raw extracted line before SKU normalization."""
    product_name: str
    quantity: float | None = None
    unit: str | None = None
    notes: str | None = None
    # Order economics — needed for math validation + draft order lines.
    unit_price: float | None = None
    amount: float | None = None
    # Sub-customer / menu-code breakdown rows that ride along with this line.
    # Populated only by `structured_parser.parse_supplier_order`; the legacy
    # line parser leaves this empty. Each entry is a dict matching the
    # `StructuredBreakdown.to_dict()` shape.
    breakdowns: list[dict] = field(default_factory=list)
    # Optional header metadata from structured orders (supplier, task_count, …).
    header: dict | None = None
    # --- Quality-assurance fields (populated by vision/OCR extractors) --------
    # 0.0–1.0 confidence for THIS line (overall). None when unknown (legacy
    # mock path). The review gate treats None as "unverified" -> flagged.
    confidence: float | None = None
    # Per-field confidence so the UI can highlight the exact weak cell:
    # {"product_name": 0.9, "quantity": 0.6, "unit_price": None, ...}
    field_confidences: dict | None = None
    # True when the line was detected as struck-through / annotated cancelled
    # (e.g. "已关", "作废", crossed-out). Always routed to human review.
    cancelled: bool = False
    # Human-readable reasons this line was flagged (empty = clean).
    review_reasons: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    """Output of an extractor — raw lines + parser notes."""
    lines: list[RawLine] = field(default_factory=list)
    parser_notes: str = ""
    doc_type: str = ""
    # When a structured supplier-order table was parsed, the full
    # header + breakdowns are preserved here so downstream callers (the
    # intake pipeline, ERP staff reviewing the draft order) can see them.
    structured: dict | None = None
    # --- Quality-assurance fields ---------------------------------------------
    # Detected layout family: "printed_form_numbers" (products pre-printed,
    # only weights handwritten — easiest), "printed_product_handwritten_values"
    # (product names printed, qty/price handwritten), "fully_handwritten",
    # "mixed" (multiple notes / partial), or "" when undetermined.
    form_type: str = ""
    form_type_confidence: float | None = None
    # Quantity-weighted overall confidence across lines (None = unknown).
    overall_confidence: float | None = None
    # The hard rule: when True, the document MUST be reviewed by a human before
    # any order is created/submitted. Never auto-confirm.
    requires_human_review: bool = False
    # Lines the extractor believes were cancelled/strikethrough — surfaced so a
    # human can confirm removal rather than silently dropping them.
    cancelled_lines: list[dict] = field(default_factory=list)
    # Path to the original image on disk (for the side-by-side review UI).
    image_path: str | None = None


# --- Text parsing -------------------------------------------------------------

# Chinese unit characters we recognize inline (e.g. "土豆50斤", "大米2袋").
_CN_UNIT_CHARS = "斤公斤千克箱袋包个份只"
# English unit words commonly appearing after a number.
_EN_UNIT_WORDS = r"(?:kg|kilo|kilogram|box|boxes|bag|bags|piece|pieces|pcs|jin|份|个|只|箱|袋|包|斤|公斤|千克)"

# A line like "土豆 50斤" or "土豆50斤" or "tomato 5kg" or "大米 2袋"
# Group 1 = product name, group 2 = quantity, group 3 = unit.
_LINE_RE = re.compile(
    r"^\s*(?P<name>[^0-9\s，,、]+(?:\s+[^0-9\s，,、]+)*?)\s*"
    r"(?:(?P<qty>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>[A-Za-z\u4e00-\u9fff]{1,4})?)?\s*$"
)

# Fallback: name followed by number with no separators at all ("土豆50斤").
_TIGHT_RE = re.compile(
    r"^(?P<name>[^\d]+?)(?P<qty>\d+(?:\.\d)?)(?P<unit>[A-Za-z\u4e00-\u9fff]{1,4})?$"
)


def parse_text_lines(text: str) -> list[RawLine]:
    """Parse free text into RawLine list.

    Splits on newlines, commas (CN and EN), and Chinese enumeration "、".
    For each fragment tries the primary regex (name + qty + unit), then a tight
    regex (no spaces), then falls back to treating the whole fragment as a
    product name with no quantity.
    """
    if not text:
        return []

    # Split on newlines, full-width comma, ASCII comma, Chinese enumeration,
    # and semicolons.
    parts = re.split(r"[\n\r，,；;、]+", text)
    lines: list[RawLine] = []

    for part in parts:
        s = part.strip()
        if not s:
            continue

        # Try primary regex
        m = _LINE_RE.match(s)
        if m and m.group("name"):
            name = m.group("name").strip()
            qty = m.group("qty")
            unit = m.group("unit")
            if qty:
                lines.append(RawLine(
                    product_name=name,
                    quantity=float(qty),
                    unit=unit if unit else None,
                ))
                continue

        # Tight regex (e.g. "土豆50斤")
        m2 = _TIGHT_RE.match(s)
        if m2 and m2.group("name") and m2.group("qty"):
            lines.append(RawLine(
                product_name=m2.group("name").strip(),
                quantity=float(m2.group("qty")),
                unit=m2.group("unit") if m2.group("unit") else None,
            ))
            continue

        # Fallback: bare product name (no quantity). Skip pure numbers.
        if not re.fullmatch(r"\d+(?:\.\d+)?", s):
            lines.append(RawLine(product_name=s))

    return lines


# --- Excel/CSV parsing -------------------------------------------------------

# Column name keywords for header detection.
_COL_PRODUCT = {"product", "item", "name", "product_name", "item_name", "商品", "品名", "名称"}
_COL_QTY = {"qty", "quantity", "amount", "count", "数量", "份量"}
_COL_UNIT = {"unit", "uom", "单位"}
_COL_NOTES = {"notes", "remark", "note", "备注", "说明"}


def _detect_columns(header: list[str]) -> dict[str, int]:
    """Map header cells to logical column indexes."""
    mapping: dict[str, int] = {}
    for i, cell in enumerate(header):
        key = (cell or "").strip().lower()
        if not key:
            continue
        if key in _COL_PRODUCT:
            mapping["product"] = i
        elif key in _COL_QTY:
            mapping["qty"] = i
        elif key in _COL_UNIT:
            mapping["unit"] = i
        elif key in _COL_NOTES:
            mapping["notes"] = i
    return mapping


def parse_csv_lines(content: bytes) -> list[RawLine]:
    """Parse CSV bytes into RawLine list. Tries utf-8 then gbk encoding."""
    text: str | None = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = content.decode("utf-8", errors="replace")

    reader = list(csv.reader(io.StringIO(text)))
    return _rows_to_lines(reader)


def parse_xlsx_lines(file_path: str) -> list[RawLine]:
    """Parse an .xlsx file into RawLine list using openpyxl."""
    try:
        from openpyxl import load_workbook
    except ImportError:  # openpyxl is in requirements.txt, but be safe
        return []

    wb = load_workbook(filename=file_path, read_only=True, data_only=True)
    rows: list[list[str]] = []
    try:
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            rows.append([("" if c is None else str(c)) for c in row])
    finally:
        wb.close()
    return _rows_to_lines(rows)


def _rows_to_lines(rows: list[list[str]]) -> list[RawLine]:
    """Convert spreadsheet rows (list of lists) to RawLine list.

    Heuristic: if the first non-empty row looks like a header (contains a
    product-name keyword), use column detection; otherwise assume positional
    columns [product, qty, unit, ...].
    """
    # Drop fully empty rows
    rows = [[(c or "").strip() for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return []

    lines: list[RawLine] = []

    # Detect header
    header = rows[0]
    col_map = _detect_columns(header)
    if col_map:
        data_rows = rows[1:]
        p_idx = col_map.get("product", 0)
        q_idx = col_map.get("qty")
        u_idx = col_map.get("unit")
        n_idx = col_map.get("notes")
    else:
        # Positional: first col = product, then qty, then unit, then notes
        data_rows = rows
        p_idx = 0
        q_idx = 1 if len(rows[0]) > 1 else None
        u_idx = 2 if len(rows[0]) > 2 else None
        n_idx = 3 if len(rows[0]) > 3 else None

    for r in data_rows:
        if not r or p_idx >= len(r):
            continue
        name = r[p_idx]
        if not name:
            continue
        # Skip if the cell is a number-only header repeat
        if re.fullmatch(r"\d+(?:\.\d+)?", name):
            continue

        qty = None
        if q_idx is not None and q_idx < len(r) and r[q_idx]:
            try:
                qty = float(r[q_idx])
            except ValueError:
                # Could be "50斤" — extract number
                m = re.search(r"(\d+(?:\.\d+)?)", r[q_idx])
                if m:
                    qty = float(m.group(1))
        unit = None
        if u_idx is not None and u_idx < len(r) and r[u_idx]:
            unit = r[u_idx]
        notes = None
        if n_idx is not None and n_idx < len(r) and r[n_idx]:
            notes = r[n_idx]

        lines.append(RawLine(product_name=name, quantity=qty, unit=unit, notes=notes))

    return lines


# --- PDF parsing --------------------------------------------------------------

def parse_pdf_lines(file_path: str) -> tuple[list[RawLine], str]:
    """Extract text from a PDF via pypdf, then run the text parser.

    Returns (lines, note). If pypdf yields no text (scanned PDF), returns
    ([], "scanned_pdf_no_text").
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return [], "pypdf not available"

    try:
        reader = PdfReader(file_path)
    except Exception:
        return [], "pdf_read_error"

    text_parts: list[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t:
            text_parts.append(t)

    if not any(text_parts):
        return [], "scanned_pdf_no_text"

    full = "\n".join(text_parts)
    return parse_text_lines(full), "pdf_text_extracted"


# --- Mock OCR for images ------------------------------------------------------

def mock_ocr_lines(filename: str | None) -> list[RawLine]:
    """Simulated OCR for image inputs.

    Returns a canned list of plausible lines derived from the filename or a
    default demo list. Real OCR is not available in the mock provider.
    """
    # If the filename contains a product hint, use it; otherwise default demo.
    default_lines = [
        RawLine(product_name="土豆", quantity=50, unit="斤"),
        RawLine(product_name="大白菜", quantity=30, unit="斤"),
        RawLine(product_name="五花肉", quantity=20, unit="斤"),
    ]
    return default_lines


# --- Extractor interface ------------------------------------------------------

class MockExtractor:
    """Deterministic, offline extractor.

    For text/email_body: parse real lines via regex.
    For excel/csv: parse rows via openpyxl/csv.
    For pdf: pypdf text extract; if no text (scanned), fall back to mock lines.
    For image: mock OCR (canned lines).

    When the incoming `raw_text` (or the text extracted from a PDF) looks
    like one of the Chinese supplier-order table formats we know about
    (广东誉元绿色 / 公司采购订单 / etc.), dispatch to `structured_parser` so
    the line totals AND the sub-customer breakdowns both come through.
    """

    def extract(self, *, source_type: str, raw_text: str | None = None,
                file_path: str | None = None, original_filename: str | None = None) -> ExtractionResult:
        notes: list[str] = []
        lines: list[RawLine] = []
        doc_type = source_type
        structured: dict | None = None
        # True when the mock provider could not READ the document and fell back
        # to canned lines. Such a document must never auto-approve.
        #
        # The canned lines (土豆 50斤 / 大白菜 30斤 / 五花肉 20斤) are plausible
        # products for this business, so a reviewer skimming them cannot tell
        # they were never read off the page. What normally parks them is
        # `settings.intake_require_human_review` — but that blanket gate is
        # exactly what gets switched OFF to automate intake, and switching it
        # off must not start approving documents nobody read. So the extractor
        # asserts it itself, and the assertion survives the setting.
        fabricated = False

        if source_type in ("text", "email_body"):
            text = raw_text or ""
            structured = _structured_for(text)
            if structured is not None:
                lines = _structured_to_raw_lines(structured)
                doc_type = "supplier_order_table"
                notes.append(f"structured_order lines={len(lines)} variant={structured.get('variant')}")
            else:
                lines = parse_text_lines(text)
                notes.append(f"{source_type}_parsed_lines={len(lines)}")
                doc_type = "typed_text" if source_type == "text" else "email_body"
        elif source_type == "excel":
            if file_path and Path(file_path).suffix.lower() in (".csv", ".txt"):
                with open(file_path, "rb") as f:
                    lines = parse_csv_lines(f.read())
            elif file_path and Path(file_path).suffix.lower() in (".xlsx", ".xls"):
                lines = parse_xlsx_lines(file_path)
            else:
                # Excel source_type but no file or unknown ext — try raw_text as CSV
                if raw_text:
                    lines = parse_csv_lines(raw_text.encode("utf-8"))
            notes.append(f"excel_rows_extracted={len(lines)}")
            doc_type = "spreadsheet"
        elif source_type == "pdf":
            if file_path and Path(file_path).exists():
                pdf_lines, note = parse_pdf_lines(file_path)
                if note == "scanned_pdf_no_text":
                    pdf_lines = mock_ocr_lines(original_filename)
                    notes.append("pdf scanned, fell back to mock OCR")
                    fabricated = True
                else:
                    notes.append("pdf text extracted")
                # If the PDF text extracted into a structured supplier-order
                # table, reparse it with the structured parser. We do this on
                # the joined text rather than the per-page lines so multi-page
                # tables still come through.
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(file_path)
                    full = "\n".join((p.extract_text() or "") for p in reader.pages)
                except Exception:
                    full = ""
                struct = _structured_for(full) if full else None
                if struct is not None:
                    pdf_lines = _structured_to_raw_lines(struct)
                    structured = struct
                    doc_type = "supplier_order_table"
                    notes.append(f"structured_order lines={len(pdf_lines)}")
                    # The text layer parsed into a real order table, so the
                    # canned fallback above is no longer what the lines are.
                    fabricated = False
                lines = pdf_lines
            else:
                notes.append("pdf file missing")
            doc_type = doc_type or "pdf"
        elif source_type == "image":
            # Real OCR (Tesseract / LLM vision) is NOT in the mock provider —
            # the deterministic fallback returns canned demo lines.
            lines = mock_ocr_lines(original_filename)
            # If an OCR'd text payload was attached via raw_text, dispatch.
            struct = _structured_for(raw_text or "")
            if struct is not None:
                lines = _structured_to_raw_lines(struct)
                structured = struct
                doc_type = "supplier_order_table"
                notes.append(f"structured_order lines={len(lines)} variant={struct.get('variant')}")
            else:
                notes.append("image OCR not available in mock provider")
                doc_type = "handwritten_note" if original_filename and "note" in original_filename.lower() else "image"
                # Nothing looked at the pixels. Flag it here rather than
                # relying on the global gate.
                fabricated = True
        else:
            notes.append(f"unknown source_type={source_type}")

        return ExtractionResult(
            lines=lines,
            parser_notes="; ".join(notes),
            doc_type=doc_type,
            structured=structured,
            # A fabricated reading is a hard "must be seen by a human" — it is
            # not a confidence question, so no threshold or setting may clear it.
            requires_human_review=fabricated,
        )


class OpenAIExtractor:
    """Stub for OpenAI-compatible provider.

    Would call {openai_base_url}/chat/completions with settings.openai_model,
    asking for the JSON schema from EXECUTIVE_SUMMARY.md. Since no API key is
    configured in the demo, falls back to MockExtractor output with a note.
    """

    def __init__(self) -> None:
        self._mock = MockExtractor()

    def extract(self, *, source_type: str, raw_text: str | None = None,
                file_path: str | None = None, original_filename: str | None = None) -> ExtractionResult:
        # TODO: implement real OpenAI-compatible API call when a key is present.
        # For now, delegate to the mock extractor and annotate.
        result = self._mock.extract(
            source_type=source_type,
            raw_text=raw_text,
            file_path=file_path,
            original_filename=original_filename,
        )
        result.parser_notes = (result.parser_notes + "; OpenAI provider stubbed, used mock").lstrip("; ")
        return result


# --- Form-type taxonomy --------------------------------------------------------
# Layout families a handwritten note can fall into. Used by the form-type
# classifier (Qwen-VL) and the review gate.
FORM_TYPES = {
    "printed_form_numbers",               # products pre-printed; only weights handwritten (easiest)
    "printed_product_handwritten_values", # product names printed; qty/price handwritten
    "fully_handwritten",                  # everything handwritten (hardest)
    "mixed",                             # several notes / cut-off / partial
}

# Packaging annotations that appear inside the quantity column (e.g. "6箱").
# These break naive qty×price math and always need a human to confirm the
# box→piece multiplier.
_PACKAGING_UNITS = {"箱", "盒", "包", "瓶", "袋", "扎", "提", "件", "条", "桶", "罐"}


def _is_packaging_annotation(text: str | None) -> bool:
    if not text:
        return False
    return any(u in text for u in _PACKAGING_UNITS)


# --- Math + review validation (pure, no IO) -----------------------------------

def validate_line_math(line: RawLine, *, abs_tol: float | None = None) -> list[str]:
    """Return review reasons if a line's numbers are internally inconsistent.

    Compares quantity × unit_price against amount. Skips when any of the three
    is missing (we can't check what we didn't read).
    """
    if abs_tol is None:
        abs_tol = float(getattr(settings, "ocr_math_abs_tol", 1.0))
    if line.quantity is None or line.unit_price is None or line.amount is None:
        return []
    expected = line.quantity * line.unit_price
    if abs(expected - line.amount) > max(abs_tol, abs(expected) * 0.02):
        return [
            f"amount mismatch: {line.quantity}×{line.unit_price}="
            f"{expected:.2f} but amount={line.amount}"
        ]
    return []


def validate_order_total(
    result: ExtractionResult, stated_total: float | None = None, *, abs_tol: float | None = None
) -> list[str]:
    """Compare the sum of (non-cancelled) line amounts to the stated total."""
    if abs_tol is None:
        abs_tol = float(getattr(settings, "ocr_math_abs_tol", 1.0))
    computed = sum((l.amount or 0.0) for l in result.lines if not l.cancelled)
    if stated_total is not None and abs(computed - stated_total) > max(abs_tol, abs(stated_total) * 0.01):
        return [f"order total mismatch: sum(lines)={computed:.2f} but stated={stated_total}"]
    return []


def apply_review_gate(
    result: ExtractionResult,
    *,
    field_floor: float | None = None,
    review_threshold: float | None = None,
) -> ExtractionResult:
    """Flag lines + the whole document for human review.

    Mutates and returns `result`. The hard rule: the document is marked
    `requires_human_review = True` if ANY of the following hold:
      - a line field is below `field_floor`,
      - a line is cancelled,
      - math (line amount / order total) is inconsistent,
      - the overall (quantity-weighted) confidence is below `review_threshold`,
      - **the document is a recognized handwritten note (any FORM_TYPES layout)**.

    The last clause encodes the user's "0 assumptions" rule: OCR is never 100%
    certain on handwriting, so a handwritten note is ALWAYS routed to a human
    and never auto-submitted. No order may be auto-submitted while the flag is set.
    """
    if field_floor is None:
        field_floor = float(getattr(settings, "ocr_field_confidence_floor", 0.80))
    if review_threshold is None:
        review_threshold = float(getattr(settings, "ocr_review_threshold", 0.95))

    reasons: list[str] = []
    worst = 1.0

    # Hard policy (the "0 assumptions" rule): a recognized handwritten note is
    # NEVER auto-submitted. OCR is never 100% certain on handwriting, so the
    # document is always routed to a human. Confidence below only decides *which
    # fields get highlighted* for the reviewer, not whether review happens.
    is_handwritten = result.form_type in FORM_TYPES

    for line in result.lines:
        lr: list[str] = []
        # Per-field confidence floor.
        fc = line.field_confidences or {}
        for fld, val in fc.items():
            if isinstance(val, (int, float)) and val < field_floor:
                lr.append(f"low confidence on {fld} ({val:.2f})")
                worst = min(worst, float(val))
        # Unknown line confidence => unverified => flag.
        if line.confidence is None:
            lr.append("line confidence unknown (unverified)")
            worst = min(worst, field_floor - 0.01)
        elif line.confidence < field_floor:
            lr.append(f"line confidence low ({line.confidence:.2f})")
            worst = min(worst, line.confidence)
        # Cancellation detection.
        if line.cancelled:
            lr.append("line marked cancelled (strikethrough/已关) — confirm removal")
        # Packaging annotation inside the quantity (box vs piece ambiguity).
        if _is_packaging_annotation(line.notes) or _is_packaging_annotation(line.unit):
            lr.append("packaging unit in quantity (箱/盒/包…) — verify multiplier")
        # Math consistency.
        lr += validate_line_math(line)
        line.review_reasons = lr
        if lr:
            reasons.append(f"line '{line.product_name}': " + "; ".join(lr))

    # Order-level total check (from structured header if present).
    stated = result.structured.get("total") if result.structured else None
    reasons += validate_order_total(result, stated)

    # Hard policy (the "0 assumptions" rule): a recognized handwritten note is
    # ALWAYS routed to a human regardless of how high the confidence is. OCR is
    # never 100% certain on handwriting, so we never auto-submit. Confidence
    # below only controls which fields get highlighted, not whether review runs.
    result.requires_human_review = bool(reasons) or is_handwritten or (
        result.overall_confidence is not None and result.overall_confidence < review_threshold
    )
    if is_handwritten and not reasons:
        reasons.append("handwritten note — mandatory human verification (no auto-submit)")
    if result.overall_confidence is None:
        result.overall_confidence = round(worst, 4)
    else:
        result.overall_confidence = round(min(result.overall_confidence, worst), 4)
    if reasons:
        result.parser_notes = (result.parser_notes + "; " + "; ".join(reasons)).strip("; ")
    return result


# --- HTTP helpers --------------------------------------------------------------

def _image_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _parse_json_strict(text: str) -> dict:
    """Parse a JSON object out of an LLM response (strip ```json fences)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: grab the first {...} span.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
        raise


# --- Qwen-VL (Alibaba DashScope, OpenAI-compatible) ---------------------------

class QwenVLFormTypeClassifier:
    """Cheap vision call that tags the layout family of a note.

    Used to route easy (pre-printed) notes to the fast path and hard
    (fully handwritten) notes to the thorough path before we pay for OCR.
    """

    SYSTEM = (
        "You classify Chinese handwritten delivery notes. Reply with exactly one "
        "of these four labels and nothing else:\n"
        "printed_form_numbers — products are pre-printed, only numbers/weights handwritten\n"
        "printed_product_handwritten_values — product names printed, quantities/prices handwritten\n"
        "fully_handwritten — product names AND numbers are handwritten\n"
        "mixed — several notes in one photo, or a partial/cut-off note"
    )

    def classify(self, image_path: str) -> tuple[str, float | None]:
        if not settings.qwen_api_key:
            raise RuntimeError("Qwen API key not configured (ERP_QWEN_API_KEY)")
        b64 = _image_b64(image_path)
        payload = {
            "model": settings.qwen_form_type_model,
            "messages": [
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Classify this delivery note."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]},
            ],
            "temperature": 0,
        }
        data = self._chat(payload)
        label = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
        for ft in FORM_TYPES:
            if ft in label:
                return ft, None
        return "mixed", None

    @staticmethod
    def _chat(payload: dict) -> dict:
        import httpx

        url = f"{settings.qwen_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.qwen_api_key}",
            "Content-Type": "application/json",
        }
        resp = httpx.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()


class AliyunHandwritingExtractor:
    """Full-page handwritten Chinese OCR via Aliyun `RecognizeAdvanced`.

    Returns RawLine list with per-word confidence taken from the OCR response.
    For non-image sources it falls back to the deterministic mock parser.
    """

    def extract(
        self,
        *,
        source_type: str,
        raw_text: str | None = None,
        file_path: str | None = None,
        original_filename: str | None = None,
    ) -> ExtractionResult:
        if source_type != "image" or not file_path:
            # We only OCR images; everything else uses the deterministic parser.
            return MockExtractor().extract(
                source_type=source_type,
                raw_text=raw_text,
                file_path=file_path,
                original_filename=original_filename,
            )
        if not (settings.aliyun_access_key_id and settings.aliyun_access_key_secret):
            raise RuntimeError(
                "Aliyun OCR not configured: set ERP_ALIYUN_ACCESS_KEY_ID and "
                "ERP_ALIYUN_ACCESS_KEY_SECRET"
            )
        raw = self._recognize(file_path)
        lines, overall = self._to_lines(raw)
        return ExtractionResult(
            lines=lines,
            doc_type="handwritten_note",
            image_path=file_path,
            overall_confidence=overall,
            parser_notes=(
                f"aliyun RecognizeAdvanced lines={len(lines)} "
                f"conf={overall:.2f}" if overall is not None else "aliyun RecognizeAdvanced"
            ),
        )

    # --- Aliyun ROA (ACS) signed request ------------------------------------
    def _recognize(self, file_path: str, action: str = "RecognizeAdvanced") -> dict:
        endpoint = settings.aliyun_ocr_endpoint
        host = endpoint
        path = f"/2021-07-07/{action[0].lower()}{action[1:]}"  # /2021-07-07/recognizeAdvanced
        body = json.dumps({"Content": _image_b64(file_path)}).encode("utf-8")
        content_md5 = base64.b64encode(hashlib.md5(body).digest()).decode("ascii")
        content_type = "application/json;charset=utf-8"
        accept = "application/json"
        date_str = _dt.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        nonce = uuid.uuid4().hex

        signed_headers = {
            "x-acs-action": action,
            "x-acs-version": "2021-07-07",
            "x-acs-date": date_str,
            "x-acs-signature-nonce": nonce,
            "host": host,
        }
        canonical_headers = "".join(
            f"{k.lower()}:{v.strip()}\n" for k, v in sorted(signed_headers.items())
        )
        string_to_sign = "\n".join([
            "POST", accept, content_md5, content_type, date_str,
            canonical_headers + path,
        ])
        key = (settings.aliyun_access_key_secret + "&").encode("utf-8")
        sig = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
        signature = base64.b64encode(sig).decode("ascii")

        headers = {
            "Authorization": f"acs {settings.aliyun_access_key_id}:{signature}",
            "Accept": accept,
            "Content-Type": content_type,
            "Content-MD5": content_md5,
            "Date": date_str,
            "Host": host,
            "x-acs-action": action,
            "x-acs-version": "2021-07-07",
            "x-acs-date": date_str,
            "x-acs-signature-nonce": nonce,
        }
        import httpx

        url = f"https://{host}{path}"
        resp = httpx.post(url, headers=headers, content=body, timeout=30)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _to_lines(raw: dict) -> tuple[list[RawLine], float | None]:
        """Map an Aliyun response into RawLine list + an overall confidence."""
        data = raw.get("Data") or raw.get("data") or {}
        content = data.get("Content") or data.get("content") or ""
        lines = parse_text_lines(content) if content else []
        infos = (
            data.get("WordInfoList")
            or data.get("WordInfos")
            or data.get("OrgWordsInfo")
            or []
        )
        confs = [
            w.get("Confidence")
            for w in infos
            if isinstance(w, dict) and isinstance(w.get("Confidence"), (int, float))
        ]
        overall = round(sum(confs) / len(confs), 4) if confs else None
        for ln in lines:
            ln.confidence = overall
            ln.field_confidences = (
                {k: overall for k in ("product_name", "quantity", "unit", "unit_price", "amount")}
                if overall is not None
                else None
            )
        return lines, overall


class AliyunQwenExtractor:
    """Two-stage pipeline: Aliyun OCR (text + table) → Qwen-VL (structure + QA).

    Stage 1 recovers the text and a global confidence from Aliyun.
    Stage 2 sends the image + OCR text to Qwen-VL with a strict JSON schema,
    asking for per-field confidence and cancellation detection. Stage 3 runs the
    review gate. The result is never auto-submitted while flagged.
    """

    STRUCTURE_PROMPT = (
        "Extract this Chinese handwritten delivery note into strict JSON. "
        "Respond with ONLY a JSON object (no prose, no markdown):\n"
        '{"lines":[{"product_name":str,"quantity":number|null,"unit":str|null,'
        '"unit_price":number|null,"amount":number|null,"cancelled":bool,'
        '"confidence":number0to1,"field_confidences":{"product_name":0to1,'
        '"quantity":0to1,"unit":0to1,"unit_price":0to1,"amount":0to1}}],'
        '"total":number|null,"cancelled_lines":[...],"overall_confidence":number0to1}\n'
        "Rules:\n"
        "- cancelled=true ONLY if the line is struck through or marked 已关/作废/删除.\n"
        "- If a field is illegible, use null and set its field_confidence low (e.g. 0.3).\n"
        "- Never invent characters — transcribe exactly what is written.\n"
        "- Preserve Chinese product names verbatim."
    )

    def extract(
        self,
        *,
        source_type: str,
        raw_text: str | None = None,
        file_path: str | None = None,
        original_filename: str | None = None,
    ) -> ExtractionResult:
        # Stage 1: OCR text + global confidence.
        ocr = AliyunHandwritingExtractor().extract(
            source_type=source_type,
            raw_text=raw_text,
            file_path=file_path,
            original_filename=original_filename,
        )
        # Stage 2: form-type + structured extraction via Qwen-VL.
        if file_path and settings.qwen_api_key:
            try:
                form_type, _ = QwenVLFormTypeClassifier().classify(file_path)
                ocr.form_type = form_type
            except Exception as exc:  # noqa: BLE001
                ocr.parser_notes = (ocr.parser_notes + f"; form-type classify failed: {exc}").strip("; ")
            structured = self._structure(file_path, ocr)
            if structured:
                ocr.structured = structured
                ocr.lines = self._merge_lines(ocr.lines, structured.get("lines", []))
                ocr.overall_confidence = structured.get("overall_confidence", ocr.overall_confidence)
                ocr.cancelled_lines = structured.get("cancelled_lines", []) or []
        # Stage 3: confidence gate.
        return apply_review_gate(ocr)

    def _structure(self, image_path: str, ocr: ExtractionResult) -> dict | None:
        if not settings.qwen_api_key:
            raise RuntimeError("Qwen API key not configured (ERP_QWEN_API_KEY)")
        ocr_text = "\n".join(
            f"{l.product_name} {l.quantity or ''} {l.unit or ''} "
            f"{l.unit_price or ''} {l.amount or ''}" for l in ocr.lines
        )
        b64 = _image_b64(image_path)
        payload = {
            "model": settings.qwen_model,
            "messages": [
                {"role": "system", "content": self.STRUCTURE_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": f"OCR text (may be imperfect):\n{ocr_text}\n\nExtract the order."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        data = QwenVLFormTypeClassifier._chat(payload)
        content = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "")
        try:
            return _parse_json_strict(content)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _merge_lines(ocr_lines: list[RawLine], structured: list[dict]) -> list[RawLine]:
        """Prefer Qwen's structured fields but keep OCR lines as the backbone."""
        out: list[RawLine] = []
        for i, sl in enumerate(structured):
            base = ocr_lines[i] if i < len(ocr_lines) else RawLine(product_name="")
            out.append(RawLine(
                product_name=sl.get("product_name") or base.product_name,
                quantity=sl.get("quantity", base.quantity),
                unit=sl.get("unit", base.unit),
                unit_price=sl.get("unit_price", base.unit_price),
                amount=sl.get("amount", base.amount),
                notes=base.notes,
                breakdowns=base.breakdowns,
                header=base.header,
                confidence=sl.get("confidence", base.confidence),
                field_confidences=sl.get("field_confidences", base.field_confidences),
                cancelled=bool(sl.get("cancelled", base.cancelled)),
                review_reasons=base.review_reasons,
            ))
        return out


# --- Mistral OCR (document AI) ------------------------------------------------

# Magic bytes -> media type for the `data:` URI we hand to Mistral. Same lesson
# as the gateway's attachment naming: the filename is a claim, the bytes are the
# fact. It matters more here, because the media type is part of the request —
# a PNG announced as image/jpeg is a rejected call, not a mislabelled file.
_IMAGE_MEDIA_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)

_EXT_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
}


def _media_type_for(content: bytes, filename: str | None = None) -> str:
    """Decide the media type of an image from its bytes, then its name.

    Mistral accepts AVIF and TIFF as well, so the extension fallback is broader
    than the sniff table — but the sniff table wins whenever it matches.
    """
    for magic, mime in _IMAGE_MEDIA_TYPES:
        if content.startswith(magic):
            return mime
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    suffix = Path(filename or "").suffix.lower()
    return _EXT_MEDIA_TYPES.get(suffix, "image/jpeg")


_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# The filename INSIDE an image placeholder, e.g. the `tbl-0.md` in
# `![](tbl-0.md)`. `_MD_IMAGE_RE` deliberately blanks the whole placeholder,
# because nothing in it may reach the line parser — so reading the name back out
# needs a pattern of its own.
_MD_IMAGE_TARGET_RE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")

# A filename the vendor invents for a figure it cropped out of the page,
# e.g. `tbl-0.md`, `img-1.jpeg`. These are NOT page content: when the OCR
# endpoint decides a dense table is a figure it emits one of these in place of
# the table, and the line parser would happily read `tbl-0.md` as a product
# name at quantity 0. Observed live on a real 14-row supplier order.
_VENDOR_FIGURE_NAME_RE = re.compile(
    r"^(?:tbl|table|img|image|fig|figure)[-_]?\d+\.(?:md|png|jpe?g|webp|gif|bmp)$",
    re.IGNORECASE,
)

# Labels that belong to the PAGE, not to a row of it. A quantity under one of
# these is page furniture wearing a number — a remark, a total, a task count —
# not a line anyone ordered.
_PAGE_FURNITURE_RE = re.compile(
    r"(备注|说明|收货单位|送货单位|采购单位|供货单位|客户|日期|打印时间|"
    r"任务数|合计|小计|总计|地址|电话|存根|包装包|经手人|送货单)",
)

# How many genuine order rows the surviving text must contain before the page
# is trusted as read, and the figure rescue left alone.
#
# ONE. Not two: a real single-row note ("土豆 50斤") is a legitimate document,
# and discarding it because the OCR also cropped a stamp or a logo out of the
# same page would be throwing away the one row that WAS read.
#
# The old rule was "a number followed by a unit anywhere in the text", and one
# stray match was enough to call the page read. Observed live on a second real
# document, a 送货单 photographed on a phone: the OCR returned its table as
# `[tbl-0.md](tbl-0.md)`, and the only quantity on the page sat inside
# `备注: - 旺仔牛奶 少2盒,不次补;` — "Wangzai milk, short 2 boxes, will not
# re-supply". A REMARK vetoed the rescue, so the page kept twelve lines of
# furniture, including its serial number `No 6096321` emitted as a product at
# quantity 6,096,321 — a confident-looking line for goods nobody ordered.
_MIN_READ_ROWS_TO_TRUST_TEXT = 1


def _order_rows_in(lines: list[RawLine]) -> int:
    """Rows that look like order rows: a quantity AND a unit, on a real name.

    All three are needed. A quantity on its own is a page number or a serial
    ("No 6096321" parses as 6,096,321). A quantity with a unit is still not
    enough if the name is the page talking about itself — 备注, 任务数, 合计.
    """
    return sum(
        1
        for ln in lines
        if ln.quantity is not None
        and str(ln.unit or "").strip()
        and not _PAGE_FURNITURE_RE.search(str(ln.product_name or ""))
    )


def _normalize_md_line(raw_line: str) -> str:
    """Strip the markdown furniture that would otherwise be read as a product.

    Shared with `_figure_reference_count` so the two agree on what a line IS:
    counting references against the raw markdown missed the real document, whose
    placeholder was a link and only becomes `tbl-0.md` after this runs.
    """
    line = _MD_IMAGE_RE.sub(" ", raw_line)
    line = _MD_LINK_RE.sub(r"\1", line)
    return line.replace("**", "").lstrip("#").strip()


def _figure_reference_count(markdown: str) -> int:
    """How many lines are the vendor's placeholder for a table it cropped out.

    Counted on the normalised line, because the vendor spells this three ways and
    all three mean the same thing:

      * `[tbl-0.md](tbl-0.md)` — a LINK. This is what the real 14-row order
        actually contained, and it is why counting the raw line failed: on its
        own the raw line is not a filename at all.
      * `tbl-0.md` — the bare name.
      * `![](tbl-0.md)` — an image placeholder.

    This is the only evidence that a table was dropped. The vendor's `images`
    array is requested without base64, so on the real document it came back
    EMPTY and a count based on it was zero.
    """
    count = 0
    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        image_target = _MD_IMAGE_TARGET_RE.search(line)
        if image_target and _VENDOR_FIGURE_NAME_RE.fullmatch(
            (image_target.group(1) or "").strip()
        ):
            count += 1
            continue
        if _VENDOR_FIGURE_NAME_RE.fullmatch(_normalize_md_line(line)):
            count += 1
    return count


def markdown_to_text(markdown: str) -> str:
    """Turn Mistral's markdown into plain lines the line parser can read.

    Markdown is not text, and the difference is not cosmetic:

    * `![img-0.jpeg](img-0.jpeg)` is a placeholder for a figure Mistral
      extracted. The line parser reads it as a PRODUCT NAME. Observed live: a
      photo of a phone's gallery view produced order lines literally named
      `![img-0.jpeg](img-0.jpeg)` and `1/3` (the gallery counter).
    * A markdown table splits the product name from its quantity with pipes, so
      `| 土豆 | 50 | 斤 |` arrives as one line whose "name" contains pipes. It
      is flattened to `土豆 50 斤`, which the existing parser reads correctly.
    * `|---|---|` separator rows and `**`/`#` decoration would otherwise end up
      inside a product name.
    * `tbl-0.md` is the vendor's name for a figure it cropped out of the page.
      It is not something a customer ordered, and it is not page text either.

    Anything the parser still cannot read falls through to the existing
    name-only behaviour, and the review gate flags it — this only removes noise,
    it never invents a quantity.
    """
    out: list[str] = []
    for raw_line in (markdown or "").splitlines():
        line = _normalize_md_line(raw_line)
        # A table separator row (`|---|---|`) carries no content at all.
        if line and "-" in line and re.fullmatch(r"[\s|:-]+", line):
            continue
        # A cropped-figure placeholder. Dropping it here is what stops
        # `tbl-0.md` from becoming a zero-quantity order line.
        if line and _VENDOR_FIGURE_NAME_RE.fullmatch(line):
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = " ".join(c for c in cells if c)
        if line:
            out.append(line)
    return "\n".join(out)


class MistralOcrExtractor:
    """Reads a photo or a scanned PDF with Mistral's document AI endpoint.

    One call returns the page as markdown, which is then run through the SAME
    line parser the typed-text path uses — so a note Mistral reads becomes the
    same `RawLine` list a typed message produces, and everything downstream
    (SKU matching, the review gate, the draft order) is unchanged.

    Deliberately NOT a fallback chain. If the call fails, this raises: the
    intake job is marked `failed` with the reason, which is visible. Falling
    back to `mock_ocr_lines()` on error would recreate the exact failure this
    codebase already paid for once — a document nobody read that looks read.
    """

    def extract(
        self,
        *,
        source_type: str,
        raw_text: str | None = None,
        file_path: str | None = None,
        original_filename: str | None = None,
    ) -> ExtractionResult:
        # Only images and scanned PDFs need OCR. Typed text, email bodies and
        # spreadsheets are genuinely parsed by the deterministic extractor, and
        # paying a vision model to read a spreadsheet is waste, not accuracy.
        if source_type not in ("image", "pdf") or not file_path:
            return MockExtractor().extract(
                source_type=source_type,
                raw_text=raw_text,
                file_path=file_path,
                original_filename=original_filename,
            )

        if source_type == "pdf":
            # A PDF with a text layer is already machine-readable: pypdf gives
            # us the characters exactly, for free. Only a PDF with NO text layer
            # (a scan, i.e. photographs of paper) is worth sending to OCR.
            _, note = parse_pdf_lines(file_path)
            if note != "scanned_pdf_no_text":
                return MockExtractor().extract(
                    source_type=source_type,
                    raw_text=raw_text,
                    file_path=file_path,
                    original_filename=original_filename,
                )

        if not settings.mistral_ocr_is_configured:
            raise RuntimeError(
                "ai_provider=mistral but ERP_MISTRAL_API_KEY is empty or still a "
                "placeholder — refusing to fall back to the mock OCR, which would "
                "invent line items"
            )

        with open(file_path, "rb") as f:
            content = f.read()
        if not content:
            raise RuntimeError(f"attachment is empty, nothing to OCR: {file_path}")

        b64 = base64.b64encode(content).decode("ascii")
        if source_type == "image":
            chunk: dict[str, Any] = {
                "type": "image_url",
                "image_url": f"data:{_media_type_for(content, original_filename)};base64,{b64}",
            }
        else:
            chunk = {
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{b64}",
            }
            if original_filename:
                # Optional, but it makes the vendor-side logs and any error
                # message name the document instead of a wall of base64.
                chunk["document_name"] = original_filename

        markdown, confidence, page_min, pages, figures, degraded = self._ocr(chunk)

        notes = [f"mistral {settings.mistral_ocr_model} pages={pages}"]
        if degraded:
            notes.append(degraded)
        # The page AVERAGE hides a single badly-read character, and a single
        # badly-read digit is a wrong quantity on a real order. Observed live: a
        # page whose average was 0.92 had a worst-word score of 0.13. Surface it
        # so the reviewer knows to look closely rather than trusting the number.
        if page_min is not None and page_min < settings.ocr_field_confidence_floor:
            notes.append(f"lowest page confidence {page_min:.2f}")

        lines: list[RawLine] = []
        structured: dict | None = None
        doc_type = "ocr_image" if source_type == "image" else "ocr_pdf"

        # Count the figure references BEFORE markdown_to_text discards them.
        #
        # This is the only evidence there is. `figures` above counts the
        # vendor's `images` array, and we ask for that array without base64 —
        # so in the real failure it is EMPTY and `figures` is 0. Confirmed live
        # on 2026-09-19: a page whose table came back as `tbl-0.md` reported
        # zero figures and four header lines, so a guard keyed on `figures`
        # never fired. The reference in the markdown is the tell.
        figure_refs = _figure_reference_count(markdown)
        # `or`, not `+`: the array and the placeholders are two views of the SAME
        # figures. Summing them reported four figures for a page that had two.
        # The array is preferred when it is populated; when it is not — which is
        # the real failure, because we ask for it without base64 — the reference
        # count is the only evidence there is.
        figure_evidence = figures or figure_refs

        # Markdown is not text — see markdown_to_text. Same dispatch order as the
        # typed path: a recognized supplier-order table keeps its header and its
        # sub-customer breakdowns.
        text = markdown_to_text(markdown)
        struct = _structured_for(text)
        if struct is not None:
            lines = _structured_to_raw_lines(struct)
            structured = struct
            doc_type = "supplier_order_table"
            notes.append(f"structured_order lines={len(lines)}")
        else:
            lines = parse_text_lines(text)
            notes.append(f"ocr_lines={len(lines)}")

            # --- Figure-only page guard -----------------------------------
            # The OCR endpoint is allowed to answer a dense table with a
            # CROPPED FIGURE instead of characters. When it does, the markdown
            # holds the page header and nothing else, `include_image_base64` is
            # off so the figure is not in the response at all, and the legacy
            # line parser turns the header into order lines — a "task count of
            # 14" was emitted as a real product at quantity 14 on a live order.
            #
            # Detect it and drop those lines rather than showing the reviewer
            # five confident-looking rows that were never on the page. Falling
            # through with zero lines is deliberate: the empty-result branch
            # below already marks the document for review and names the reason,
            # so the reviewer is told to transcribe instead of being handed a
            # partly-invented order.
            #
            # Dropping the lines is the safe half. The useful half is re-reading
            # the page — see _vision_rescue — because "we noticed the page is
            # unreadable" does not put 14 rows back on the order.
            read_rows = _order_rows_in(lines)
            if figure_evidence and lines and read_rows < _MIN_READ_ROWS_TO_TRUST_TEXT:
                notes.append(
                    f"table returned as a figure instead of text "
                    f"({figure_refs} reference(s), {figures} image(s)) — "
                    f"discarding {len(lines)} non-order line(s) "
                    f"({read_rows} read row(s))"
                )
                lines = []
                rescued = self._vision_rescue(
                    source_type=source_type,
                    file_path=file_path,
                    original_filename=original_filename,
                    figures=figure_evidence,
                )
                if rescued is not None:
                    if rescued.lines:
                        # Success: the rescue owns the result, but keep the OCR
                        # trail in front of it so the reviewer can see WHY the
                        # vision model was asked at all.
                        rescued.parser_notes = "; ".join(
                            [n for n in notes if n] + [rescued.parser_notes]
                        ).strip("; ")
                        return rescued
                    notes.append(
                        f"vision re-read found no lines ({rescued.parser_notes})"
                    )
                else:
                    notes.append("vision re-read was not available")

        if confidence is not None:
            for ln in lines:
                ln.confidence = confidence
                ln.field_confidences = {
                    k: confidence
                    for k in ("product_name", "quantity", "unit", "unit_price", "amount")
                }

        result = apply_review_gate(ExtractionResult(
            lines=lines,
            parser_notes="; ".join(notes),
            doc_type=doc_type,
            structured=structured,
            overall_confidence=confidence,
            image_path=file_path if source_type == "image" else None,
        ))

        if not result.lines:
            # A document nobody could read a line out of must never auto-approve.
            # This is not hypothetical: a page of pure figures — a photo OF notes,
            # or a gallery screenshot — comes back with near-empty markdown and the
            # real content in the vendor's `images` array. `include_image_base64`
            # is off, so the content is not in the text at all. Without this, the
            # page confidence is high, no line carries a review reason, and the
            # gate would happily approve an EMPTY order.
            extra = "no line items could be read from the document"
            if figure_evidence:
                # "figure(s)" covers both the vendor's images array and a
                # placeholder reference in the markdown: either way the page
                # answered with a picture where the table should have been.
                extra += f" ({figure_evidence} figure(s) were extracted instead of text)"
            result.parser_notes = (result.parser_notes + "; " + extra).strip("; ")
            result.requires_human_review = True
        return result

    def _vision_rescue(
        self,
        *,
        source_type: str,
        file_path: str | None,
        original_filename: str | None,
        figures: int,
    ) -> ExtractionResult | None:
        """Re-read a page the OCR endpoint answered with a cropped figure.

        Returns None when no re-read was possible, so the caller keeps the safe
        empty-and-flag outcome rather than gaining a new way to fail.

        Only a photograph is rescued. A PDF is deliberately excluded: the vision
        extractor hands scanned PDFs straight back to this class, so rescuing one
        here would recurse.
        """
        if not settings.vision_fallback_enabled:
            return None
        if source_type != "image" or not file_path:
            return None
        # The original lives in the database; disk is a best-effort cache, so a
        # redeployed container may not have it. No file means no re-read.
        if not Path(file_path).exists():
            return None
        try:
            return VisionTableExtractor().extract(
                source_type=source_type,
                file_path=file_path,
                original_filename=original_filename,
            )
        except Exception as exc:  # noqa: BLE001
            # The rescue is a bonus, never a dependency. If the vision call is
            # unconfigured, refused, times out or is not licensed for the model,
            # the document must still land in review — exactly where it landed
            # before the rescue existed. Swallowing here is what keeps a vision
            # outage from becoming an intake outage.
            logger.error(
                "vision re-read failed after %d figure(s) on %s: %s",
                figures, original_filename or file_path, exc,
            )
            return None

    def _ocr(self, chunk: dict) -> tuple[str, float | None, float | None, int, int, str]:
        """POST one document to Mistral.

        Returns (markdown, average_confidence, worst_page_confidence, pages,
        figures, note).

        Confidence is None when the deployment will not return scores — and the
        review gate reads None as "unverified" and flags the document, so that
        degradation fails safe rather than silently approving.
        """
        import httpx

        url = f"{settings.mistral_base_url.rstrip('/')}/ocr"
        headers = {
            "Authorization": f"Bearer {settings.mistral_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": settings.mistral_ocr_model,
            "document": chunk,
            # We want the text. Images and bounding boxes would inflate the
            # response (and the invoice) for something nothing here reads.
            "include_image_base64": False,
            "include_blocks": False,
        }
        optional: dict[str, Any] = {
            # Page-level aggregate only: word granularity would bloat the
            # response with per-word scores we do not use.
            "confidence_scores_granularity": "page",
            # Without this a recognized table is replaced by a placeholder
            # reference rather than returned inline, so the line items would be
            # lost from the markdown entirely.
            "table_format": "markdown",
        }

        resp = httpx.post(url, headers=headers, json={**payload, **optional}, timeout=120)
        degraded = ""
        # Both of the above are optional request fields. If this deployment
        # rejects them, retry once without them rather than failing an intake
        # over a nice-to-have — and say so in the notes. A 400 is what an
        # unsupported field returns; a 401/403/429 is not retried.
        if resp.status_code == 400:
            resp = httpx.post(url, headers=headers, json=payload, timeout=120)
            degraded = "optional request fields rejected (retried without confidence scores/table format)"
        resp.raise_for_status()
        data = resp.json()

        pages = data.get("pages") or []
        markdown = "\n".join((p.get("markdown") or "") for p in pages)
        averages = [
            (p.get("confidence_scores") or {}).get("average_page_confidence_score")
            for p in pages
        ]
        minimums = [
            (p.get("confidence_scores") or {}).get("minimum_page_confidence_score")
            for p in pages
        ]
        vals = [float(v) for v in averages if isinstance(v, (int, float))]
        mins = [float(v) for v in minimums if isinstance(v, (int, float))]
        confidence = round(sum(vals) / len(vals), 4) if vals else None
        worst = min(mins) if mins else None
        # Figures the vendor pulled out of the page. When the markdown is nearly
        # empty and this is not, the real content was pictures, not text — and
        # with `include_image_base64` off it is not in the response at all.
        figures = sum(len(p.get("images") or []) for p in pages)
        return markdown, confidence, worst, len(pages), figures, degraded


# --- Vision table read (Pixtral chat) ----------------------------------------

_VISION_TABLE_PROMPT = """\
You are transcribing a Chinese supplier purchase order (采购订单) from a photograph.

Reply with ONE JSON object and nothing else — no prose, no markdown fence:

{"supplier": <string or null>,
 "purchase_unit": <string or null>,
 "print_time": <string or null>,
 "task_count": <integer or null>,
 "lines": [{"line_no": <integer>,
            "product_name": <string>,
            "total_quantity": <number or null>,
            "total_unit": <string or null>,
            "notes": <string or null>,
            "breakdowns": [{"customer_name": <string or null>,
                            "quantity": <number or null>,
                            "unit": <string or null>,
                            "note": <string or null>}]}]}

Rules — follow them exactly:
1. `lines` holds ONLY rows of the order table. The page header (the supplier
   name, 采购单位, 打印时间, 任务数) is NOT a row; put it in the top-level
   fields instead. A row number alone is not a product.
2. One entry per product row. When a product row carries per-customer delivery
   detail, put each destination in `breakdowns`; otherwise `breakdowns` is [].
3. Copy numbers exactly as printed, including decimals.
4. If a cell is unreadable or missing, use null. Never guess, never invent a
   product, never fill a gap from what you expect to see.
5. If there is no order table in the image at all, return {"lines": []}.
6. Use these key names EXACTLY as written. Do not rename them, and do not add
   keys that are not listed — a destination/customer column belongs in
   `customer_name`, not in `recipient`, and the row's unit belongs in
   `total_unit`, not `unit`.
"""


class VisionTableExtractor:
    """Reads a supplier-order photo with a vision chat model instead of /ocr.

    Why this exists: the `/ocr` document-AI endpoint is allowed to answer a
    dense table with a CROPPED FIGURE reference rather than characters. With
    `include_image_base64` off that figure is not in the response at all, so a
    14-row order arrived as five header fragments — including a literal
    `tbl-0.md` and a "task count" of 14 read as a quantity. `MistralOcrExtractor`
    now detects that case, but detecting it is not the same as reading the page.

    A vision chat model is given the image itself plus an explicit prompt, so
    there is nowhere for the table to be "left out": either it reads the rows or
    it says there are none.

    Trade-off, stated plainly: this costs more per page and takes longer than
    `/ocr`. It is worth it because a misread quantity on a food order is a
    delivery of the wrong goods, which is the one thing this system exists to
    prevent. Confidence is deliberately left None — a chat model gives no score,
    and the review gate reads None as "unverified", so every line is flagged.
    """

    def extract(
        self,
        *,
        source_type: str,
        raw_text: str | None = None,
        file_path: str | None = None,
        original_filename: str | None = None,
    ) -> ExtractionResult:
        # Only a photograph needs a vision read. Typed text, spreadsheets and
        # PDFs with a text layer are handled by the deterministic paths; paying
        # a vision model to re-read characters we already have is waste.
        if source_type == "pdf" and file_path:
            # A scanned PDF would need page rendering first, which is a separate
            # piece of work. Hand it to the OCR path so nothing regresses.
            return MistralOcrExtractor().extract(
                source_type=source_type,
                raw_text=raw_text,
                file_path=file_path,
                original_filename=original_filename,
            )
        if source_type != "image" or not file_path:
            return MockExtractor().extract(
                source_type=source_type,
                raw_text=raw_text,
                file_path=file_path,
                original_filename=original_filename,
            )

        if not settings.mistral_ocr_is_configured:
            raise RuntimeError(
                "image_ocr_provider=pixtral but ERP_MISTRAL_API_KEY is empty or "
                "still a placeholder — refusing to fall back to the mock OCR, "
                "which would invent line items"
            )

        with open(file_path, "rb") as f:
            content = f.read()
        if not content:
            raise RuntimeError(f"attachment is empty, nothing to read: {file_path}")

        b64 = base64.b64encode(content).decode("ascii")
        media_type = _media_type_for(content, original_filename)

        raw_reply, model = self._vision_read(b64, media_type)

        notes = [f"pixtral {model}"]
        try:
            payload = _parse_json_strict(raw_reply)
        except Exception as exc:  # noqa: BLE001
            # An unparseable answer is a failed read, not an empty order. Say so
            # and flag; never let a JSON error masquerade as "no lines found".
            note = f"vision reply was not parseable JSON: {exc}"
            logger.error("VisionTableExtractor: %s; reply=%r", note, raw_reply[:400])
            result = apply_review_gate(ExtractionResult(
                lines=[],
                parser_notes=f"{note}",
                doc_type="ocr_image",
                image_path=file_path,
            ))
            result.parser_notes = f"{result.parser_notes}; {note}".strip("; ")
            result.requires_human_review = True
            return result

        header = {
            k: payload.get(k)
            for k in ("supplier", "purchase_unit", "print_time", "task_count")
            if payload.get(k) is not None
        }
        lines: list[RawLine] = []
        structured_lines: list[dict] = []
        unreadable = 0

        for i, row in enumerate(payload.get("lines") or []):
            if not isinstance(row, dict):
                continue
            name = (row.get("product_name") or "").strip()
            if not name:
                # A row with no product name is not a row we can act on. Skip it
                # rather than emitting a blank line for someone to delete.
                continue
            qty = _first_float(row, "total_quantity", "quantity")
            if qty is None:
                unreadable += 1
            breakdowns: list[dict] = []
            for bd in row.get("breakdowns") or []:
                if not isinstance(bd, dict):
                    continue
                bd_name = _first_str(bd, *_BREAKDOWN_NAME_KEYS)
                breakdowns.append({
                    "raw": (bd_name or ""),
                    "customer_name": bd_name,
                    "customer_code": None,
                    "quantity": _first_float(bd, "quantity", "qty"),
                    "unit": _first_str(bd, "unit"),
                    "note": _first_str(bd, "note", "notes"),
                    "menu_code": None,
                    "menu_label": None,
                })
            try:
                line_no = int(row.get("line_no") or (i + 1))
            except (TypeError, ValueError):
                line_no = i + 1
            structured_lines.append({
                "line_no": line_no,
                "raw_text": name,
                "product_name": name,
                "total_quantity": qty,
                "total_unit": _first_str(row, "total_unit", "unit"),
                "breakdowns": breakdowns,
                "notes": _first_str(row, "notes", "note"),
                "extra": {},
            })
            lines.append(RawLine(
                product_name=name,
                quantity=qty,
                unit=_first_str(row, "total_unit", "unit"),
                notes=_first_str(row, "notes", "note"),
                breakdowns=breakdowns,
                header=header or None,
                # A chat model reports no confidence. None is the honest value:
                # the review gate treats it as unverified and flags the line.
                confidence=None,
            ))

        structured = None
        doc_type = "ocr_image"
        if structured_lines:
            structured = {
                "variant": "vision",
                "parser_notes": "pixtral vision read",
                "header": header,
                "lines": structured_lines,
            }
            doc_type = "supplier_order_table"
        notes.append(f"vision_lines={len(lines)}")
        if unreadable:
            notes.append(f"{unreadable} line(s) missing a quantity")

        result = apply_review_gate(ExtractionResult(
            lines=lines,
            parser_notes="; ".join(notes),
            doc_type=doc_type,
            structured=structured,
            overall_confidence=None,
            image_path=file_path,
        ))

        if not result.lines:
            extra = "no line items could be read from the document"
            result.parser_notes = (result.parser_notes + "; " + extra).strip("; ")
            result.requires_human_review = True
        return result

    def _vision_read(self, b64: str, media_type: str) -> tuple[str, str]:
        """POST one image to a vision chat model. Returns (reply, model)."""
        import httpx

        model = settings.pixtral_ocr_model
        url = f"{settings.mistral_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.mistral_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _VISION_TABLE_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": f"data:{media_type};base64,{b64}",
                        },
                    ],
                }
            ],
            # Extraction is transcription, not composition. A low temperature
            # keeps the model from "helpfully" normalising a quantity it misread.
            "temperature": 0.0,
        }
        resp = httpx.post(
            url, headers=headers, json=payload,
            timeout=settings.vision_ocr_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        content = ""
        if choices:
            content = ((choices[0] or {}).get("message") or {}).get("content") or ""
        if isinstance(content, list):
            # Some deployments return content parts instead of a plain string.
            content = "".join(
                (part.get("text") or "") for part in content if isinstance(part, dict)
            )
        return content, model


def _as_float(value: Any) -> float | None:
    """Coerce a JSON number to float, or None when it is missing/unreadable.

    The prompt tells the model to answer null rather than guess, so None here is
    a real signal — it is counted and surfaced, not silently turned into 0. A
    zero would be worse than nothing: it reads as a confirmed quantity.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Key names the vision model has actually used for one field. The prompt asks for
# a specific schema and the model does not reliably honour it: on the real 14-row
# order it answered `recipient` where the prompt said `customer_name`, and
# `unit`/`note` where the prompt said `total_unit`/`notes`. Re-prompting is not a
# fix — the reply is otherwise correct, and throwing away a good transcription
# because of a key name is worse than reading the name it chose.
_BREAKDOWN_NAME_KEYS = ("customer_name", "recipient", "customer", "destination", "name")


def _first_str(row: dict, *keys: str) -> str | None:
    """The first non-empty string among `keys`, else None."""
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_float(row: dict, *keys: str) -> float | None:
    """The first readable number among `keys`, else None."""
    for key in keys:
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None


def get_extractor():
    """Factory: returns MockExtractor when settings.ai_provider == 'mock'."""
    provider = (settings.ai_provider or "mock").lower()
    if provider == "openai" and settings.openai_api_key:
        return OpenAIExtractor()
    if provider == "mistral":
        # Deliberately does NOT check for the API key here, unlike aliyun_qwen
        # below. The pipeline calls this factory for EVERY document, before it
        # knows the source type — so a key check here turns "I have selected
        # Mistral but not pasted the key yet" into a total intake outage,
        # including the typed-text orders that need no OCR at all and currently
        # work. MistralOcrExtractor refuses at the point it actually needs to
        # read a page, which is the only moment the key matters. The unkeyed
        # state is still visible: /api/health reports
        # `image_extraction_is_simulated: true`.
        if (settings.image_ocr_provider or "").strip().lower() == "pixtral":
            return VisionTableExtractor()
        return MistralOcrExtractor()
    if provider == "aliyun_qwen":
        if not (
            settings.aliyun_access_key_id
            and settings.aliyun_access_key_secret
            and settings.qwen_api_key
        ):
            raise RuntimeError(
                "ai_provider=aliyun_qwen but credentials missing: set "
                "ERP_ALIYUN_ACCESS_KEY_ID, ERP_ALIYUN_ACCESS_KEY_SECRET, ERP_QWEN_API_KEY"
            )
        return AliyunQwenExtractor()
    # Default: mock (deterministic, offline)
    return MockExtractor()


# ---------------------------------------------------------------------------
# Structured supplier-order dispatch (Chinese tofu/caterer table format).
# Used by MockExtractor whenever the incoming text looks like one of the
# known procurement table variants. Kept here (not in structured_parser) so
# MockExtractor owns its own dispatch.
# ---------------------------------------------------------------------------


def _structured_for(text: str) -> dict | None:
    """Return the structured parsing result if the text is a supplier-order
    table, else None so callers fall through to the legacy line parser."""
    if not text:
        return None
    # Cheaper probe first — avoids invoking the parser on every chat message.
    if not any(marker in text for marker in (
        "任务数",
        "公司采购订单",
        "广东誉元",
        "采购单位",
        "采购总数",
        "-----",
    )):
        return None
    from app.ai.structured_parser import parse_supplier_order
    parsed = parse_supplier_order(text)
    if parsed.variant == "unknown" or not parsed.lines:
        return None
    return parsed.to_dict()


def _structured_to_raw_lines(structured: dict) -> list[RawLine]:
    """Convert a structured parser result into the legacy RawLine list,
    carrying each line's breakdowns on the side so the downstream pipeline
    can write them into `raw_output.lines[i].breakdowns`."""
    out: list[RawLine] = []
    for ln in structured.get("lines", []):
        rl = RawLine(
            product_name=ln.get("product_name") or "",
            quantity=ln.get("total_quantity"),
            unit=ln.get("total_unit"),
            notes=ln.get("notes"),
            breakdowns=ln.get("breakdowns") or [],
            header=structured.get("header"),
        )
        out.append(rl)
    return out
