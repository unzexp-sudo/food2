# Sample Order Format Validation

**Scope:** validate the four real WeChat procurement photos supplied on 2026-09-07
against the ERP intake pipeline, and fix every anomaly found.

**Result: all four formats are now processed correctly.** 50 order lines, 102
per-customer breakdown rows, 98 of them carrying a customer code, and **every
line reconciles** — the sum of its breakdown quantities equals the printed row
total.

---

## 1. The four formats

| Sample | Detected variant | Signature | Lines | Breakdowns | Coded |
|---|---|---|---|---|---|
| A | **A** | long table, merged 商品名 cells, 明细 sub-rows (`佛山市政府（夜用台）A46 … 26斤`) | 13 | 32 | 32 |
| B | **B** | bracketed annotations (`8板*[A10锅钯]`) | 16 | 24 | 20 |
| C | **C** | `公司采购订单`, dash-separated (`20斤-----186B-----(*)`) | 7 | 11 | 11 |
| D | **A** | same as A, different date/customer mix | 14 | 35 | 35 |

Sample A's photo is **cropped at row 13** although its header prints `任务数:20`.
Rows 14–20 are not visible in the image and were **not** invented — the parser
reports what is actually on the page.

## 2. Why the legacy parser could not handle these

The original `parse_text_lines` regex assumed one product per line with an
inline quantity. All four samples break that assumption:

- a product cell spans many rows (merged cell) with per-customer 明细 sub-rows;
- quantities live at the *end* of a sub-row, after a customer name and a code;
- three different separators appear: `…`, `-----`, and `*[…]`.

A new deterministic parser was built instead:
`backend/app/ai/structured_parser.py` → `parse_supplier_order(text)`, with
`detect_variant()` auto-selecting A / B / C.

## 3. Anomalies found and fixed

| # | Anomaly | Fix |
|---|---|---|
| 1 | Variant C emitted **0 breakdowns** — the detail cell was indexed `parts[5]`, but a trailing `\|` makes `split("\|")` yield an empty 6th cell | `_pick_detail_cell()` locates the 明细 cell by its `-----` marker, falling back to its fixed index |
| 2 | Variant C note regex expected `-----*`, real text is `-----(*)` — so `(*切小方块)` was lost | note group is now `\(\s*\*?(?P<note>[^)]*?)\s*\)`; bare `(*)` is normalised to *no requirement* |
| 3 | Variant B never captured codes — `[A10锅钯]` landed wholly in the label because both regex groups were lazy-optional | `_B_BODY_RE` splits a leading `[A-Za-z]{1,3}\d{1,4}` code from the label |
| 4 | Variant B dropped annotations that OCR joined **inline** onto the product row | `_B_INLINE_RE` harvests them from the product row as well as continuation lines |
| 5 | Customer names kept their trailing quantity (`佛山市政府（夜用台） 26斤`) | quantity stripped with `{qty:g}` so whole numbers match the OCR text |
| 6 | **Inventory ledger ordered by random UUID** — pagination was non-deterministic and the newest movement was not guaranteed to be on page 1 (a pre-existing flaky test) | `InventoryMovement` now uses `TimestampMixin` (adds `created_at`); listing orders by `created_at DESC, id DESC` |

## 4. Verification

- **Correctness invariant:** for every parsed line, `sum(breakdown.quantity) == line.total_quantity`.
  This is the strongest available signal — a mis-split 明细 cell drifts away from
  采购总数, which is exactly the failure that would under- or over-deliver to a
  customer. Enforced as a test across all four samples.
- **Variant C cross-check:** each row's parsed breakdown count is asserted
  against its declared 客户数 (2+1+1+2+1+2+2 = 11).
- **Test suites:** backend **243 passed**; WeCom gateway **194 passed, 2 xpassed**.
  The backend suite was run three times consecutively to confirm the previous
  flakiness is gone.

## 5. Notes / limitations

- Breakdown rows carry `customer_code` but not a resolved `customer_id`. The
  bind cascade (exact code → exact name → fuzzy) runs at intake, so an
  unrecognised code still lands as an unmatched line needing review rather than
  being silently attached to the wrong customer.
- `created_at` was added to `inventory_movements`. `init_db()` now applies
  additive columns via ALTER TABLE, so existing databases pick it up without a
  migration tool.
- Sample A remains truncated by its source photo. If rows 14–20 matter, supply
  the full image and the parser will pick them up unchanged.
