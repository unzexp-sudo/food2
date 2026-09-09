"""AI extractor adapters.

- MockExtractor: deterministic, offline — parses text/excel/csv/pdf/image into
  raw lines {product_name, quantity, unit, notes}. This is the tested path and
  the demo provider (no API key needed).
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
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime as _dt
from pathlib import Path
from typing import Any

from app.core.config import settings


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
        else:
            notes.append(f"unknown source_type={source_type}")

        return ExtractionResult(
            lines=lines,
            parser_notes="; ".join(notes),
            doc_type=doc_type,
            structured=structured,
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


def get_extractor():
    """Factory: returns MockExtractor when settings.ai_provider == 'mock'."""
    provider = (settings.ai_provider or "mock").lower()
    if provider == "openai" and settings.openai_api_key:
        return OpenAIExtractor()
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
