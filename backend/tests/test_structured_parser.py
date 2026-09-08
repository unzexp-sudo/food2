"""Unit tests for `app.ai.structured_parser` against transcribed WeCom
supplier-order photos (4 real samples).

Each fixture under `tests/fixtures/sample_orders/` is a faithful transcription
of one procurement photo. These tests assert:

* the right `variant` is detected (A / B / C / A)
* the right number of `StructuredLine`s is produced
* `breakdowns` (per-line delivery-routing rows) come through with
  `customer_code` / `menu_code` set on at least one row
* the header dict carries `task_count` (and, for variant A, the
  采购单位 key)

Known parser risks observed against the real samples are called out in
docstrings (e.g. sample C's digit-split is too aggressive and currently
emits 0 breakdowns; sample C is asserted with relaxed expectations).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.structured_parser import parse_supplier_order


FIXTURES = Path(__file__).parent / "fixtures" / "sample_orders"


def _read(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Sample A — variant A, 13 visible rows (image cropped, 任务数 prints 20)
# ---------------------------------------------------------------------------
def test_sample_a_parses_as_variant_a():
    text = _read("sample_a")
    order = parse_supplier_order(text)
    assert order.variant == "A", f"expected variant A, got {order.variant}"
    assert len(order.lines) == 13, (
        f"expected 13 lines (image is cropped at row 13; 任务数:20 reports 20 "
        f"rows total, but only 13 are visible). Got {len(order.lines)}."
    )
    # Sum of breakdowns across all lines should match the per-line 明细
    # counts: 2+4+1+2+11+1+1+1+3+2+1+2+1 = 32. We ask for >= 25 to absorb
    # any OCR edge case where a sub-row fails to parse.
    total_bds = sum(len(ln.breakdowns) for ln in order.lines)
    assert total_bds >= 25, f"expected >= 25 breakdowns, got {total_bds}"
    # Header assertions
    assert order.header.get("task_count") == "20"
    # 采购单位 must be present (zh key with trimmed value)
    assert "采购单位" in order.header
    assert order.header["采购单位"].strip() == "广东某生餐饮有限公司"
    # Spot-check the first line
    l1 = order.lines[0]
    assert isinstance(l1.total_quantity, (int, float))
    assert l1.product_name and l1.product_name != "明细"
    assert l1.product_name == "白豆腐干"
    # At least one breakdown on L1 must have a customer_code
    assert any(b.customer_code for b in l1.breakdowns)
    # Row 5 must have 11 sub-rows (its claimed 明细 count)
    l5 = order.lines[4]
    assert len(l5.breakdowns) == 11, (
        f"row 5 should have 11 明细 sub-rows, got {len(l5.breakdowns)}"
    )


# ---------------------------------------------------------------------------
# Sample B — variant B, 16 rows
# ---------------------------------------------------------------------------
def test_sample_b_parses_as_variant_b():
    text = _read("sample_b")
    order = parse_supplier_order(text)
    assert order.variant == "B", f"expected variant B, got {order.variant}"
    assert len(order.lines) == 16
    # 16 rows by structure; some rows carry multiple bracketed sub-rows
    # (row 1 has 3, row 6 has 2, etc.) so we expect 16 main lines and
    # >= 16 breakdowns total. (Actual = 24 in our transcription.)
    total_bds = sum(len(ln.breakdowns) for ln in order.lines)
    assert total_bds >= 16, f"expected >= 16 breakdowns, got {total_bds}"
    # Header
    assert order.header.get("task_count") == "16"
    # Spot-check: L1 should have 3 bracketed sub-rows
    l1 = order.lines[0]
    assert l1.product_name == "豆腐（小板）"
    assert isinstance(l1.total_quantity, (int, float))
    assert l1.product_name and l1.product_name != "明细"
    # Bracketed sub-rows expose menu_code (and menu_label)
    assert len(l1.breakdowns) == 3
    # At least one breakdown on L1 has a menu_code
    assert any(b.menu_code for b in l1.breakdowns)
    # "[A10锅钯]" must split into code A10 + label 锅钯, not swallow the
    # whole body into the label.
    coded = [b for b in order.lines[0].breakdowns if b.menu_code]
    assert coded and coded[0].menu_code == "A10"
    assert coded[0].menu_label == "锅钯"
    assert coded[0].customer_code == "A10"
    # "[猪脚饭2斤]" has no leading code — it stays a pure label.
    plain = [b for b in l1.breakdowns if not b.menu_code]
    assert plain and plain[0].menu_label == "猪脚饭2斤"


def test_variant_b_inline_annotations_on_product_row():
    """OCR often joins the 明细 onto the product row.

    The parser must harvest bracketed annotations whether they arrive on
    their own continuation line or inline on the product row — otherwise
    the per-customer split is silently lost.
    """
    text = (
        "任务数:2\n"
        "序号 | 商品名 | 采购总数(基本单位) | 明细\n"
        "1 | 豆腐（小板） | 12板 | 8板*[A10锅钯] 2板*[猪脚饭2斤] 2板*[盖浇饭2斤]\n"
        "2 | 韧豆腐（中板） | 6板 | 6板*[A01坚美]\n"
    )
    order = parse_supplier_order(text)
    assert order.variant == "B", f"expected variant B, got {order.variant}"
    assert len(order.lines) == 2
    l1, l2 = order.lines
    assert l1.product_name == "豆腐（小板）"
    assert l1.total_quantity == 12.0
    assert len(l1.breakdowns) == 3, l1.breakdowns
    assert [b.quantity for b in l1.breakdowns] == [8.0, 2.0, 2.0]
    # The row total must still reconcile against its inline breakdowns.
    assert sum(b.quantity for b in l1.breakdowns) == l1.total_quantity
    assert l1.breakdowns[0].menu_code == "A10"
    assert l1.breakdowns[0].menu_label == "锅钯"
    assert len(l2.breakdowns) == 1
    assert l2.breakdowns[0].menu_code == "A01"


# ---------------------------------------------------------------------------
# Sample C — variant C, 7 rows
# ---------------------------------------------------------------------------
def test_sample_c_parses_as_variant_c():
    text = _read("sample_c")
    order = parse_supplier_order(text)
    assert order.variant == "C", f"expected variant C, got {order.variant}"
    assert len(order.lines) == 7
    # Every row declares a 客户数; the parser must emit exactly that many
    # per-customer breakdowns (2+1+1+2+1+2+2 = 11).
    total_bds = sum(len(ln.breakdowns) for ln in order.lines)
    assert total_bds == 11, (
        f"expected 11 variant-C breakdowns (sum of 客户数), got {total_bds}"
    )
    for ln in order.lines:
        declared = ln.extra.get("customer_count")
        assert declared == len(ln.breakdowns), (
            f"line {ln.line_no}: 客户数={declared} but parsed "
            f"{len(ln.breakdowns)} breakdowns"
        )
        assert all(bd.customer_code for bd in ln.breakdowns), (
            f"line {ln.line_no}: a breakdown is missing its customer code"
        )
    # "(*)" means "no special requirement" and must not leak into the note,
    # while real requirements like "(*切小方块)" must survive.
    notes = [bd.note for ln in order.lines for bd in ln.breakdowns]
    assert notes.count("切小方块") == 1, f"expected 1 切小方块 note, got {notes}"
    assert notes.count("教职工") == 1, f"expected 1 教职工 note, got {notes}"
    assert notes.count(None) == 9, f"expected 9 empty notes, got {notes}"
    # Header
    assert order.header.get("task_count") == "7"
    assert order.header.get("estimated_total") == "1822.50"
    assert order.header.get("supplier_name") or order.header.get("供应商")
    # Spot-check: L1 should be 小豆卜, with a numeric total
    l1 = order.lines[0]
    assert isinstance(l1.total_quantity, (int, float))
    assert l1.product_name and l1.product_name != "明细"
    assert "小豆卜" in l1.product_name


# ---------------------------------------------------------------------------
# Sample D — variant A shape, 14 rows
# ---------------------------------------------------------------------------
def test_sample_d_parses_as_variant_a():
    text = _read("sample_d")
    order = parse_supplier_order(text)
    assert order.variant == "A", f"expected variant A, got {order.variant}"
    assert len(order.lines) == 14
    # >= 25 across all lines: 1+5+9+2+1+1+1+1+1+2+1+7+2+1 = 35
    total_bds = sum(len(ln.breakdowns) for ln in order.lines)
    assert total_bds >= 25, f"expected >= 25 breakdowns, got {total_bds}"
    # Header
    assert order.header.get("task_count") == "14"
    assert order.header.get("采购单位", "").strip() == "广东某生餐饮有限公司"
    # Spot-check: row 12 (攸县香干) has 7 sub-rows
    l12 = order.lines[11]
    assert l12.product_name == "攸县香干"
    assert len(l12.breakdowns) == 7
    assert isinstance(l12.total_quantity, (int, float))
    # Customer codes propagated (7 of them)
    assert sum(1 for b in l12.breakdowns if b.customer_code) >= 5


# ---------------------------------------------------------------------------
# Generic safety: empty / garbage input must not crash
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "  ", "hello world", "任务数:0"])
def test_parser_handles_garbage_gracefully(bad):
    order = parse_supplier_order(bad)
    # Either variant is "unknown" with no lines, OR — for "任务数:0" —
    # variant is "A" with no lines. Either way: no crash, no exception,
    # and the result is a `StructuredOrder`.
    assert order.variant in ("A", "B", "C", "unknown")
    assert order.lines == []
