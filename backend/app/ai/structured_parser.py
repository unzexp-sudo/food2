"""Deterministic parser for the Chinese supplier-order table formats observed
in the WeChat/WeCom procurement photos coming from 誉元绿色 (tofu/soy factory)
to 广东某生餐饮 (caterer aggregating for many sub-customer canteens).

Three observed variants (see tests/fixtures/sample_orders/*.txt for the
transcribed sources):

  Variant A / D — "广东誉元绿色食品有限公司" long table.
    Columns: 序号 | 商品名 | 总数 | 明细 (with optional right-edge notes column).
    One product line can span MANY 明细 rows; the 商品名 cell repeats (or is
    visually merged). Each 明细 cell holds one sub-customer with
    "{customer_name}{customer_code?} {qty}{unit}".

  Variant B — minimal "任务数" header.
    Columns: 序号 | 商品名 | 采购总数(基本单位) | 明细.
    Each 明细 cell holds newline-separated "{qty}{unit}*{[code menu]}" lines.
    Brackets carry a customer-or-menu code with a human label.

  Variant C — "公司采购订单" / 采购经办 / 预算采购金额 header.
    Columns: 序号 | 商品名 | 计划采购 | 实际采购 | 客户数 | 下单数量—客户编码—(商品要求) | 实收数量.
    Each 明细 cell holds one or more "----- " separated segments like
    "20斤-----186B-----(*)" or "20斤-----185A-----(*教职工)".
    Customer codes are 3–4 chars starting with a digit followed by letters.

The parser is intentionally rule-based (not LLM-driven): it must work offline,
must be deterministic, and the same input must always produce the same output.
When text extraction quality is poor, the parser falls back to a relaxed mode
and emits `parser_notes` describing what it skipped.

Design: every parsed line produces one `StructuredLine` whose `breakdowns`
`
list captures the delivery-routing detail. The downstream AI intake pipeline
`app.ai.pipeline.process_intake_job` already writes the lines into
`IntakeExtraction.raw_output.lines` (JSON), so breakdowns ride along
without a schema migration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass
class StructuredBreakdown:
    """One delivery-routing row inside an order line."""
    raw: str                       # the original 明细 fragment we matched
    customer_name: str | None = None
    customer_code: str | None = None
    quantity: float | None = None
    unit: str | None = None
    note: str | None = None        # right-edge notes like "午餐/切碎", "400g"
    menu_code: str | None = None   # bracket label like "A10锅钯" in variant B
    menu_label: str | None = None

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "customer_name": self.customer_name,
            "customer_code": self.customer_code,
            "quantity": self.quantity,
            "unit": self.unit,
            "note": self.note,
            "menu_code": self.menu_code,
            "menu_label": self.menu_label,
        }


@dataclass
class StructuredLine:
    """One ordered product row."""
    line_no: int
    raw_text: str
    product_name: str                # e.g. "中板豆腐（7斤/板）"
    total_quantity: float | None
    total_unit: str | None
    breakdowns: list[StructuredBreakdown] = field(default_factory=list)
    notes: str | None = None          # right-edge notes column for THIS line
    extra: dict = field(default_factory=dict)  # variant-specific extras

    def to_dict(self) -> dict:
        return {
            "line_no": self.line_no,
            "raw_text": self.raw_text,
            "product_name": self.product_name,
            "total_quantity": self.total_quantity,
            "total_unit": self.total_unit,
            "breakdowns": [b.to_dict() for b in self.breakdowns],
            "notes": self.notes,
            "extra": self.extra,
        }


@dataclass
class StructuredOrder:
    """Header + ordered lines."""
    header: dict[str, Any] = field(default_factory=dict)
    lines: list[StructuredLine] = field(default_factory=list)
    parser_notes: str = ""
    variant: str = "unknown"   # A | B | C | D
    raw_text: str = ""

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "parser_notes": self.parser_notes,
            "header": self.header,
            "lines": [ln.to_dict() for ln in self.lines],
        }


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# A trailing "{number}{chinese_unit}" — "26斤", "4.5斤", "8板", "3盒".
_QTY_UNIT_RE = re.compile(
    r"(?P<q>\d+(?:\.\d+)?)\s*(?P<unit>斤|公斤|千克|板|盒|袋|包|个|份|只|块|串|箱|g|kg|jin|box|bag|pcs)?",
    re.IGNORECASE,
)

# Customer code: a token like A46, A49-1, A8-6, C10, C38, B6-1, C3-2.
# 1 letter + digits, optional "-digit" suffix.
_CODE_AT_END_RE = re.compile(r"(?P<code>[A-Z]\d{1,3}(?:-\d+)?)")

# Bracket label: "[A10锅钯]" or "[猪脚饭2斤]" — code first (alnum), label can be CJK.
_BRACKET_RE = re.compile(r"\[(?P<code>[A-Za-z0-9]+)?(?P<label>[^\[\]]+?)?\]")

# Variant C dash separator.
_DASH_SEP = "----- "

# Line header "1 | ..." or "1 |…" — leading number, optional dash/bullets.
_LINE_NUM_RE = re.compile(r"^\s*(\d{1,3})\s*[|\.、]\s*(.+?)\s*$")

# Variant C row: "20斤-----186B-----(*)" or "30板-----183-----(*切小方块)".
_C_DASH_ROW_RE = re.compile(
    r"^\s*"
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>\S+?)"
    r"\s*-----"
    r"\s*(?P<code>[A-Z0-9]+)"
    r"(?:\s*-----\s*\(\s*\*?(?P<note>[^)]*?)\s*\))?"
    r"\s*$"
)

# Variant B row: "8板*[A10锅钯]" or "20串*[A06致兴]".
_B_ROW_RE = re.compile(
    r"^\s*(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>\S+?)"
    r"\*\[(?P<body>[^\[\]]+?)\]"
    r"\s*$"
)

# Bracket body "[A10锅钯]" -> code "A10" + label "锅钯".
# "[猪脚饭2斤]" has no leading alnum code, so it stays a pure label.
_B_BODY_RE = re.compile(r"^(?P<code>[A-Za-z]{1,3}\d{1,4})(?P<label>[\u4e00-\u9fff].*)?$")

# Unanchored twin of `_B_ROW_RE` for annotations that OCR joined onto the
# product row instead of putting them on their own continuation line.
_B_INLINE_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>\S+?)\*\[(?P<body>[^\[\]]+?)\]"
)

# A common header token regex used to harvest key-value pairs.
_KV_RE = re.compile(r"([\u4e00-\u9fffA-Za-z]+):\s*([^\s][^\n]*?)(?=\s{2,}|\s*[\u4e00-\u9fffA-Za-z]+:\s|\n|$)")

# "任务数:20" or "任务数 16"
_TASK_COUNT_RE = re.compile(r"任务数[:：]\s*(\d+)")
_PRINT_TIME_RE = re.compile(r"打印时间[:：]\s*([0-9\-:\s]+)")
_ESTIMATED_TOTAL_RE = re.compile(r"预算采购金额[:：]\s*([\d\.]+)")
_SUPPLIER_PHONE_RE = re.compile(r"供应商电话[:：]\s*(\S+)")
_SUPPLIER_NAME_RE = re.compile(r"供应商[:：]\s*([^\n]+?)(?:\s{2,}|\s*\d|$)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _find_qty_unit(text: str) -> tuple[float | None, str | None]:
    """Find a trailing {number}{unit} like '26斤' or '8板'. Greedy at end."""
    m = None
    for cand in re.finditer(r"(\d+(?:\.\d+)?)\s*([\u4e00-\u9fffA-Za-z]+)?", text):
        m = cand  # last one
    if m is None:
        return None, None
    return _to_float(m.group(1)), (m.group(2) or None)


def _header_kv(text: str) -> dict[str, str]:
    """Extract 'key: value' pairs from the header lines."""
    out: dict[str, str] = {}
    # Specific keys first
    for key, pattern in (
        ("task_count", _TASK_COUNT_RE),
        ("print_time", _PRINT_TIME_RE),
        ("estimated_total", _ESTIMATED_TOTAL_RE),
        ("supplier_phone", _SUPPLIER_PHONE_RE),
        ("supplier_name", _SUPPLIER_NAME_RE),
    ):
        m = pattern.search(text)
        if m:
            out[key] = m.group(1).strip()
    # Fallback generic KV for "采购单位:" etc.
    for m in _KV_RE.finditer(text):
        k = m.group(1).strip()
        v = m.group(2).strip()
        if k and v and k not in out and k not in ("明细", "总数"):
            out.setdefault(k, v)
    return out


def _split_rows(body: str) -> list[str]:
    """Split the table body into per-product rows.

    Rows in the source are separated by newline. We collapse runs of empty
    lines but preserve order.
    """
    out: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if s:
            out.append(s)
    return out


def _extract_customer_code(text: str) -> str | None:
    """Pick the customer code from a 明细 fragment.

    Strategy: a code is a short uppercase-letter+digits token optionally
    followed by -digits. The fragment ends with the code right before the
    quantity. Try the end-of-string first; fall back to a search.
    """
    m = _CODE_AT_END_RE.search(text.rstrip())
    if m:
        return m.group("code")
    m = re.search(r"\b([A-Z]\d{1,3}(?:-\d+)?)\b", text)
    if m:
        return m.group(1)
    return None


def _strip_code(text: str, code: str) -> str:
    return re.sub(rf"\s*{re.escape(code)}\s*", " ", text).strip(" 　\t")


# ---------------------------------------------------------------------------
# Variant detection
# ---------------------------------------------------------------------------


def detect_variant(text: str) -> str:
    if _S_DASH_SEP_ROW.search(text):
        return "C"
    if _B_BRACKET_ROW.search(text):
        return "B"
    if "采购单位" in text or "明细" in text or "任务数" in text:
        return "A"
    return "unknown"


# Precompiled "first matching row exists" probes used by detect_variant.
_S_DASH_SEP_ROW = re.compile(r"\d+(?:\.\d+)?\s*\S+\s*-----\s*[A-Z0-9]+")
_B_BRACKET_ROW = re.compile(r"\d+\s*\S+\s*\*\[")


# ---------------------------------------------------------------------------
# Variant A/D — long table with merged 商品名 cells and 明细 sub-customer lines
# ---------------------------------------------------------------------------


def _parse_variant_a(text: str) -> StructuredOrder:
    """The dominant format. Header + a row-per-line body where each 主行
    may span multiple 明细 sub-rows.

    Implementation strategy: walk lines top-to-bottom. Track the current
    product (sticky across rows where 序号+商品名 are missing). Each new
    序号 starts a new StructuredLine.
    """
    body = _strip_header_block(text)
    header = _header_kv(text)
    lines = _split_rows(body)

    structured: list[StructuredLine] = []
    line_no = 0
    cur: StructuredLine | None = None
    notes: list[str] = []

    for raw in lines:
        # Skip table header row.
        if raw.startswith("序号") or raw.startswith("|序号") or "商品名" in raw and "明细" in raw:
            continue

        # Skip task-count echo like "任务数:20" embedded in body.
        if _TASK_COUNT_RE.match(raw):
            continue

        m = _LINE_NUM_RE.match(raw)
        if m:
            line_no = int(m.group(1))
            after = m.group(2)
            cur = _parse_a_main_row(after)
            cur.line_no = line_no
            # Collect right-edge note if present (e.g., "中板豆腐 ... 7斤1板").
            cur.notes = _extract_right_note(cur.raw_text)
            structured.append(cur)
            # If the same row packs the qty AND a sub-customer breakdown
            # (some tables collapse a one-customer product into a single
            # 明细 cell), attach it now.
            _attach_inline_breakdown_if_any(cur, raw)
            continue

        # Continuation row: a 明细 sub-row for the current product.
        if cur is None:
            continue
        bd = _parse_a_breakdown_line(raw, cur)
        if bd is None:
            notes.append(f"unparsed 明细 fragment: {raw!r}")
            continue
        cur.breakdowns.append(bd)

    return StructuredOrder(
        header=header,
        lines=structured,
        parser_notes="; ".join(notes) or "variant A parsed",
        variant="A",
        raw_text=text,
    )


def _strip_header_block(text: str) -> str:
    """Drop the title + key:value block before the table starts.

    Heuristic: keep only lines from the first line that begins with a digit
    and a separator like '|' (variant A row pattern).
    """
    out_lines: list[str] = []
    started = False
    for line in text.splitlines():
        s = line.strip()
        if not started:
            if re.match(r"^\d+\s*[|\.]", s):
                started = True
            else:
                continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _parse_a_main_row(rest: str) -> StructuredLine:
    """Parse the columns after the leading line number.

    A main row is typically "{product_name} | {total_qty} | {detail fragment}".
    But OCR often collapses pipes; treat anything from start through the first
    '|' as product_name, then look for total qty before the second '|'.
    """
    parts = [p.strip() for p in rest.split("|")]
    if len(parts) < 2:
        # Could be only product on this row.
        return StructuredLine(
            line_no=0,
            raw_text=rest,
            product_name=parts or rest,
            total_quantity=None,
            total_unit=None,
        )

    product_name = parts[0]

    # Find the total qty+unit in the second column ("27斤", "326板").
    qty, unit = None, None
    if len(parts) >= 2 and parts[1]:
        qty, unit = _find_qty_unit(parts[1])

    return StructuredLine(
        line_no=0,
        raw_text=rest,
        product_name=product_name,
        total_quantity=qty,
        total_unit=unit,
    )


def _extract_right_note(text: str) -> str | None:
    """Pick a right-edge annotation like '7斤1板' or '午餐/切碎' out of the raw row.

    We pick the LAST comma-or-space-separated token that does NOT contain a
    customer-code-like prefix.
    """
    tokens = re.split(r"[|｜]", text)
    if len(tokens) < 2:
        return None
    last = tokens[-1].strip()
    if not last:
        return None
    # Avoid swallowing the 明细 column itself.
    if "斤" in last or "板" in last or "盒" in last or "/" in last and len(last) <= 12:
        return last
    return None


def _attach_inline_breakdown_if_any(cur: StructuredLine, raw: str) -> None:
    """For a one-line product whose 明细 sits in the same row."""
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 4:
        return
    detail = parts[3]
    if not detail:
        return
    bd = _parse_a_breakdown_line(detail, cur)
    if bd is not None and bd.customer_name:
        cur.breakdowns.append(bd)


def _parse_a_breakdown_line(text: str, parent: StructuredLine) -> StructuredBreakdown | None:
    """Parse one 明细 sub-row.

    A typical 明细 sub-row is:
        "佛山市政府（夜用台）A46 ... 26斤"
        "佛山市高明区沧江中学（学生菜类）A49-1 ... 55斤"
        "广州医科大学附属第一医院（海印院区）A44 ... 4板"
        "广州市老人院上水院区（老人菜素）A8-1 ... 16斤"
    The trailing "{qty}{unit}" can also be absent (pure customer name),
    in which case qty comes from the row total / other source.

    Returns None when nothing useful was extracted — caller logs it.
    """
    text = text.strip()
    if not text:
        return None

    # Strip optional right-edge note like "午餐/切碎".
    note: str | None = None
    note_m = re.search(r"[\|/][^|/]*$", text)
    if note_m:
        candidate = note_m.group(0).lstrip("/|").strip()
        if candidate and not re.search(r"\d", candidate):
            note = candidate
            text = text[: note_m.start()].strip()

    # Drop leading pipe artifacts from merged cells ("|| ... |customer...").
    text = text.lstrip(" 　\t|｜").strip()
    # Drop "..." separators that OCR often inserts between name and qty.
    text = re.sub(r"\.{2,}", " ", text).strip()
    text = re.sub(r"\s+", " ", text)

    code = _extract_customer_code(text)
    if code:
        text = _strip_code(text, code)

    qty, unit = _find_qty_unit(text)
    # If we found a trailing qty, strip it to expose the customer name.
    # Use `{qty:g}` so whole numbers render as "1" (not "1.0") and match
    # the OCR'd text.
    if qty is not None:
        qty_str = f"{qty:g}"
        unit_pattern = rf"(?:{re.escape(unit)})?" if unit else r"(?:[\u4e00-\u9fffA-Za-z]+)?"
        text = re.sub(rf"{qty_str}\s*{unit_pattern}\s*$", "", text).strip()
    # The remaining is the customer name. Drop trailing "...".
    text = re.sub(r"\s*\.{2,}\s*$", "", text).strip()
    customer_name = text or None

    return StructuredBreakdown(
        raw=text + (f" {code}" if code else ""),
        customer_name=customer_name,
        customer_code=code,
        quantity=qty,
        unit=unit,
        note=note,
    )


# ---------------------------------------------------------------------------
# Variant B — bracketed annotations
# ---------------------------------------------------------------------------


def _split_bracket_body(body: str) -> tuple[str | None, str | None]:
    """Split a variant-B bracket body into (customer_code, customer_name).

    "[A10锅钯]"  -> ("A10", "锅钯")
    "[猪脚饭2斤]" -> (None, "猪脚饭2斤")   # no leading alphanumeric code
    """
    body = (body or "").strip()
    if not body:
        return None, None
    bm = _B_BODY_RE.match(body)
    if not bm:
        return None, body
    return bm.group("code"), ((bm.group("label") or "").strip() or None)


def _add_b_breakdown(line: StructuredLine, qty: str, unit: str, body: str) -> None:
    code, label = _split_bracket_body(body)
    line.breakdowns.append(
        StructuredBreakdown(
            raw=f"{qty}{unit}*[{body}]",
            quantity=_to_float(qty),
            unit=unit,
            menu_code=code,
            menu_label=label,
            customer_code=code,
            customer_name=label,
        )
    )


def _parse_variant_b(text: str) -> StructuredOrder:
    header = _header_kv(text)
    body = _strip_header_block(text)
    structured: list[StructuredLine] = []
    line_no = 0
    cur: StructuredLine | None = None

    for raw in _split_rows(body):
        if raw.startswith("序号") or "商品名" in raw and "明细" in raw:
            continue
        if _TASK_COUNT_RE.match(raw):
            continue
        m = _LINE_NUM_RE.match(raw)
        if m:
            line_no = int(m.group(1))
            after = m.group(2)
            parts = [p.strip() for p in after.split("|")]
            product_name = parts[0] if parts else after
            qty, unit = (None, None)
            if len(parts) >= 2:
                qty, unit = _find_qty_unit(parts[1])
            cur = StructuredLine(
                line_no=line_no,
                raw_text=after,
                product_name=product_name,
                total_quantity=qty,
                total_unit=unit,
            )
            structured.append(cur)

            # OCR frequently joins the 明细 rows onto the product row, e.g.
            # "1 | 豆腐（小板） | 12板 | 8板*[A10锅钯] 2板*[猪脚饭2斤]".
            # Harvest any inline bracketed annotations before moving on so
            # they are not silently dropped.
            for im in _B_INLINE_RE.finditer(after):
                _add_b_breakdown(cur, im.group("qty"), im.group("unit"), im.group("body"))
            continue

        if cur is None:
            continue
        m2 = _B_ROW_RE.match(raw)
        if m2:
            _add_b_breakdown(cur, m2.group("qty"), m2.group("unit"), m2.group("body"))
        else:
            # Plain "{qty}{unit}" without brackets — record as a generic breakdown.
            qty, unit = _find_qty_unit(raw)
            if qty is not None:
                cur.breakdowns.append(
                    StructuredBreakdown(
                        raw=raw,
                        quantity=qty,
                        unit=unit,
                    )
                )

    return StructuredOrder(
        header=header,
        lines=structured,
        parser_notes="variant B parsed",
        variant="B",
        raw_text=text,
    )


# ---------------------------------------------------------------------------
# Variant C — 公司采购订单 with dashes
# ---------------------------------------------------------------------------


def _pick_detail_cell(parts: list[str]) -> str:
    """Return the 下单数量—客户编码—(商品要求) cell of a variant-C row.

    After the 序号 column is stripped a row is
    ``[商品名, 计划采购, 实际采购, 客户数, 明细, 实收数量]``. OCR/export
    artifacts routinely append a dangling ``|``, which makes ``split("|")``
    yield a trailing empty cell — so we must not blindly index ``parts[5]``.
    The 明细 cell is identified by its ``-----`` separator, falling back to
    its fixed 0-based index 4.
    """
    cells = [p.strip() for p in parts]
    for cell in cells:
        if "-----" in cell:
            return cell
    if len(cells) >= 5 and cells[4]:
        return cells[4]
    while cells and not cells[-1]:
        cells.pop()
    return cells[-1] if cells else ""


def _parse_variant_c(text: str) -> StructuredOrder:
    header = _header_kv(text)
    body = _strip_header_block(text)
    structured: list[StructuredLine] = []
    line_no = 0
    cur: StructuredLine | None = None
    notes: list[str] = []

    for raw in _split_rows(body):
        if raw.startswith("序号") or "商品名" in raw and ("计划采购" in raw or "客户编码" in raw):
            continue
        if _TASK_COUNT_RE.match(raw):
            continue

        m = _LINE_NUM_RE.match(raw)
        if m:
            line_no = int(m.group(1))
            after = m.group(2)
            parts = [p.strip() for p in after.split("|")]
            # 商品名 (col 1), 计划采购 (col 2), 实际采购 (col 3), 客户数 (col 4), 下单数量… (col 5), 实收 (col 6)
            product_name = parts[0] if parts else after
            planned = _find_qty_unit(parts[1]) if len(parts) >= 2 else (None, None)
            actual = _find_qty_unit(parts[2]) if len(parts) >= 3 else (None, None)
            n_customers: int | None = None
            if len(parts) >= 4 and parts[3].strip().isdigit():
                n_customers = int(parts[3].strip())

            cur = StructuredLine(
                line_no=line_no,
                raw_text=after,
                product_name=product_name,
                total_quantity=actual[0] or planned[0],
                total_unit=actual[1] or planned[1],
                extra={
                    "planned_quantity": planned[0],
                    "planned_unit": planned[1],
                    "actual_quantity": actual[0],
                    "actual_unit": actual[1],
                    "customer_count": n_customers,
                },
            )
            structured.append(cur)

            detail = _pick_detail_cell(parts)
            if detail:
                # Split the detail cell into per-customer segments. Each
                # segment begins with "{qty}{unit}" and is followed by
                # `-----` separators. We find them all with a single
                # finditer that anchors on the qty+unit prefix.
                seg_re = re.compile(
                    r"\d+(?:\.\d+)?\s*\S+?\s*-----\s*[A-Za-z0-9]+"
                    r"(?:\s*-----\s*\(\s*\*?[^)]*?\s*\))?"
                )
                matched = 0
                for m in seg_re.finditer(detail):
                    seg = m.group(0).strip()
                    bd_m = _C_DASH_ROW_RE.match(seg)
                    if bd_m:
                        note_txt = (bd_m.group("note") or "").strip()
                        # "(*)" means "no special requirement" — drop it, but
                        # keep real requirements such as "(*切小方块)".
                        if note_txt in ("", "*"):
                            note_txt = None
                        else:
                            note_txt = note_txt.lstrip("*").strip() or None
                        cur.breakdowns.append(
                            StructuredBreakdown(
                                raw=seg,
                                quantity=_to_float(bd_m.group("qty")),
                                unit=bd_m.group("unit"),
                                customer_code=bd_m.group("code") or None,
                                note=note_txt,
                            )
                        )
                    else:
                        rel = re.search(
                            r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>\S+).*?(?P<code>[A-Z0-9]+)",
                            seg,
                        )
                        if rel:
                            cur.breakdowns.append(
                                StructuredBreakdown(
                                    raw=seg,
                                    quantity=_to_float(rel.group("qty")),
                                    unit=rel.group("unit"),
                                    customer_code=rel.group("code"),
                                )
                            )
                        else:
                            notes.append(f"unparsed C 明细 fragment: {seg!r}")
                    matched += 1
                if matched == 0 and detail.strip():
                    notes.append(f"no C dash segments found in 明细: {detail!r}")
            continue

    return StructuredOrder(
        header=header,
        lines=structured,
        parser_notes="variant C parsed" + ("; " + "; ".join(notes) if notes else ""),
        variant="C",
        raw_text=text,
    )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def parse_supplier_order(text: str) -> StructuredOrder:
    """Detect the variant and parse. Always returns a StructuredOrder.

    On a totally non-matching text the parser still returns an empty
    `StructuredOrder(variant='unknown')` so callers can fall back to the
    legacy line-by-line parser without crashing.
    """
    if not text or not text.strip():
        return StructuredOrder(parser_notes="empty input", variant="unknown")

    variant = detect_variant(text)
    if variant == "C":
        return _parse_variant_c(text)
    if variant == "B":
        return _parse_variant_b(text)
    if variant in ("A", "D"):
        return _parse_variant_a(text)
    return StructuredOrder(parser_notes="variant not detected", variant="unknown")


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()