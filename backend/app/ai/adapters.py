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

import csv
import io
import re
from dataclasses import dataclass, field
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
    # Sub-customer / menu-code breakdown rows that ride along with this line.
    # Populated only by `structured_parser.parse_supplier_order`; the legacy
    # line parser leaves this empty. Each entry is a dict matching the
    # `StructuredBreakdown.to_dict()` shape.
    breakdowns: list[dict] = field(default_factory=list)
    # Optional header metadata from structured orders (supplier, task_count, …).
    header: dict | None = None


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


def get_extractor():
    """Factory: returns MockExtractor when settings.ai_provider == 'mock'."""
    provider = (settings.ai_provider or "mock").lower()
    if provider == "openai" and settings.openai_api_key:
        return OpenAIExtractor()
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
