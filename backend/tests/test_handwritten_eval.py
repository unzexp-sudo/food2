"""Eval harness for the handwritten-order OCR pipeline.

What this validates WITHOUT any external API keys or network:

1. **Contract** — the extended RawLine / ExtractionResult round-trips through
   dataclasses.asdict and back, so the pipeline can serialize it into the
   IntakeExtraction.raw_output JSON column.
2. **MathValidator** — line amount = qty×price and order sum = stated total.
   Clean lines are NOT falsely flagged; deliberately broken lines ARE flagged.
3. **Review gate** — `apply_review_gate` flips `requires_human_review` for:
   low per-field confidence, unknown confidence, cancellation, packaging
   annotations, and math mismatch. Every real handwritten note (our eval set)
   is routed to human review, which is exactly the "0 assumptions" rule.
4. **Form-type enum + wiring** — `get_extractor` returns the right class per
   `ERP_AI_PROVIDER`, and refuses `aliyun_qwen` when credentials are missing.

Live OCR (Aliyun + Qwen-VL) runs only when keys AND the real images are
present; otherwise those paths are skipped with a clear note. To run live:

    ERP_AI_PROVIDER=aliyun_qwen \
    ERP_ALIYUN_ACCESS_KEY_ID=... ERP_ALIYUN_ACCESS_KEY_SECRET=... \
    ERP_QWEN_API_KEY=... \
    pytest tests/test_handwritten_eval.py -k live
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from app.ai.adapters import (
    FORM_TYPES,
    AliyunHandwritingExtractor,
    AliyunQwenExtractor,
    ExtractionResult,
    MockExtractor,
    QwenVLFormTypeClassifier,
    RawLine,
    apply_review_gate,
    get_extractor,
    validate_line_math,
    validate_order_total,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "handwritten"
EXPECTED_DIR = FIXTURE_DIR / "expected"


def _load_expected(name: str) -> dict:
    return json.loads((EXPECTED_DIR / name).read_text(encoding="utf-8"))


def _build_result(doc: dict, *, simulated_confidence: float) -> ExtractionResult:
    """Build an ExtractionResult from a ground-truth doc, simulating the
    confidence an OCR would report for a note of this difficulty."""
    lines = []
    for i, ln in enumerate(doc.get("lines", [])):
        cancelled = any(
            cl.get("index") == i for cl in doc.get("cancelled_lines", [])
        ) or "已关" in (ln.get("notes") or "")
        lines.append(RawLine(
            product_name=ln["product_name"],
            quantity=ln.get("quantity"),
            unit=ln.get("unit"),
            unit_price=ln.get("unit_price"),
            amount=ln.get("amount"),
            notes=ln.get("notes"),
            confidence=simulated_confidence,
            field_confidences={
                "product_name": simulated_confidence,
                "quantity": simulated_confidence,
                "unit": simulated_confidence,
                "unit_price": simulated_confidence,
                "amount": simulated_confidence,
            },
            cancelled=cancelled,
        ))
    return ExtractionResult(
        lines=lines,
        doc_type="handwritten_note",
        form_type=doc.get("form_type", ""),
        structured={"total": doc.get("expected_total")},
        image_path=str(FIXTURE_DIR / doc["source_file"]) if (FIXTURE_DIR / doc["source_file"]).exists() else None,
    )


# Simulated OCR confidence by form-type difficulty (optimistic but realistic).
_CONF_BY_FORM = {
    "printed_form_numbers": 0.97,
    "printed_product_handwritten_values": 0.86,
    "fully_handwritten": 0.55,
    "mixed": 0.50,
}


# --------------------------------------------------------------------------- #
# 1. Contract round-trip
# --------------------------------------------------------------------------- #

def test_contract_roundtrip():
    res = ExtractionResult(
        lines=[RawLine(product_name="土豆", quantity=50, unit="斤", confidence=0.9)],
        form_type="printed_form_numbers",
        overall_confidence=0.9,
        requires_human_review=True,
        cancelled_lines=[{"index": 1}],
        image_path="/tmp/x.jpeg",
    )
    blob = asdict(res)
    # JSON-serializable (the pipeline stores this in IntakeExtraction.raw_output)
    json.dumps(blob)
    back = ExtractionResult(**{
        "lines": [RawLine(**l) for l in blob["lines"]],
        **{k: v for k, v in blob.items() if k != "lines"},
    })
    assert back.requires_human_review is True
    assert back.lines[0].product_name == "土豆"
    assert back.cancelled_lines == [{"index": 1}]


# --------------------------------------------------------------------------- #
# 2. MathValidator precision (synthetic, deterministic)
# --------------------------------------------------------------------------- #

def test_math_validator_clean_line_passes():
    line = RawLine(product_name="土豆", quantity=50, unit_price=2, amount=100)
    assert validate_line_math(line) == []


def test_math_validator_broken_line_flagged():
    line = RawLine(product_name="土豆", quantity=50, unit_price=2, amount=90)
    reasons = validate_line_math(line)
    assert reasons and "amount mismatch" in reasons[0]


def test_math_validator_missing_fields_not_flagged():
    # Can't check what we didn't read.
    assert validate_line_math(RawLine(product_name="土豆", quantity=50)) == []


def test_order_total_mismatch_flagged():
    res = ExtractionResult(lines=[
        RawLine(product_name="a", quantity=1, unit_price=10, amount=10),
        RawLine(product_name="b", quantity=1, unit_price=10, amount=10),
    ])
    reasons = validate_order_total(res, stated_total=100)
    assert reasons and "order total mismatch" in reasons[0]


def test_order_total_cancelled_excluded():
    res = ExtractionResult(lines=[
        RawLine(product_name="a", quantity=1, unit_price=10, amount=10),
        RawLine(product_name="b", quantity=1, unit_price=10, amount=10, cancelled=True),
    ])
    # 10 + (cancelled 10) vs stated 10 -> must reconcile after excluding cancel.
    assert validate_order_total(res, stated_total=10) == []


# --------------------------------------------------------------------------- #
# 3. Review gate — every real note is flagged (0-assumptions rule)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fname,conf,expected_flag", [
    # Hard forms (low OCR confidence) -> always reviewed.
    ("001_two_slips.json", 0.50, True),
    ("002_songdan_609621.json", 0.86, True),   # box annotations trip the gate
    ("003_songdan_609621_edited.json", 0.90, True),  # has a cancelled line
    ("004_songdan_0102282.json", 0.55, True),
    # EASY form: printed products, only numbers handwritten. The "0 assumptions"
    # rule STILL routes it to human review even at high confidence — OCR is never
    # 100% certain, so a handwritten note is NEVER auto-submitted regardless of
    # confidence. The only thing confidence changes is which fields get highlighted.
    ("005_shengxian_XS202504001.json", 0.97, True),
    ("005_shengxian_XS202504001.json", 0.70, True),
])
def test_review_gate_flags_by_confidence(fname, conf, expected_flag):
    doc = _load_expected(fname)
    res = _build_result(doc, simulated_confidence=conf)
    res = apply_review_gate(res)
    assert res.requires_human_review is expected_flag, (
        f"{fname} @conf={conf}: expected requires_human_review={expected_flag}, "
        f"got {res.requires_human_review}; notes={res.parser_notes}"
    )


def test_review_gate_zero_confidence_forces_review():
    res = ExtractionResult(lines=[RawLine(product_name="神秘商品", confidence=None)])
    res = apply_review_gate(res)
    assert res.requires_human_review is True
    assert "unknown" in res.lines[0].review_reasons[0].lower()


def test_review_gate_cancellation_detected():
    doc = _load_expected("003_songdan_609621_edited.json")
    res = _build_result(doc, simulated_confidence=0.9)
    res = apply_review_gate(res)
    cancelled = [l for l in res.lines if l.cancelled]
    assert cancelled, "cancelled line (洽洽瓜蒌 / 已关) should be detected"
    assert any("cancelled" in r.lower() for r in cancelled[0].review_reasons)


def test_review_gate_packaging_annotation_flagged():
    res = ExtractionResult(lines=[RawLine(
        product_name="康师傅冰红茶", quantity=6, notes="6箱",
        confidence=0.9,
        field_confidences={"product_name": 0.9, "quantity": 0.9, "unit": 0.9, "unit_price": 0.9, "amount": 0.9},
    )])
    res = apply_review_gate(res)
    assert any("packaging" in r for r in res.lines[0].review_reasons)


# --------------------------------------------------------------------------- #
# 4. Form-type enum + extractor wiring
# --------------------------------------------------------------------------- #

def test_form_type_enum_complete():
    assert FORM_TYPES == {
        "printed_form_numbers",
        "printed_product_handwritten_values",
        "fully_handwritten",
        "mixed",
    }


def test_get_extractor_default_is_mock():
    # Ensure the default config still resolves to the deterministic mock.
    assert isinstance(get_extractor(), MockExtractor)


def test_get_extractor_aliyun_qwen_missing_keys_raises(monkeypatch):
    monkeypatch.setattr("app.ai.adapters.settings.ai_provider", "aliyun_qwen")
    monkeypatch.setattr("app.ai.adapters.settings.aliyun_access_key_id", "")
    monkeypatch.setattr("app.ai.adapters.settings.aliyun_access_key_secret", "")
    monkeypatch.setattr("app.ai.adapters.settings.qwen_api_key", "")
    with pytest.raises(RuntimeError):
        get_extractor()


def test_get_extractor_aliyun_qwen_wires_correctly(monkeypatch):
    monkeypatch.setattr("app.ai.adapters.settings.ai_provider", "aliyun_qwen")
    monkeypatch.setattr("app.ai.adapters.settings.aliyun_access_key_id", "x")
    monkeypatch.setattr("app.ai.adapters.settings.aliyun_access_key_secret", "y")
    monkeypatch.setattr("app.ai.adapters.settings.qwen_api_key", "z")
    assert isinstance(get_extractor(), AliyunQwenExtractor)


# --------------------------------------------------------------------------- #
# 5. Live path (skipped unless keys + images present)
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(
    not (FIXTURE_DIR / "002_songdan_609621.jpeg").exists(),
    reason="source image not in fixtures (drop the 5 jpegs into tests/fixtures/handwritten/)",
)
def test_live_image_present_note():
    # Sanity that the image is where the harness expects it.
    assert (FIXTURE_DIR / "005_shengxian_XS202504001.jpeg").exists()


@pytest.mark.skipif(
    not (FIXTURE_DIR / "002_songdan_609621.jpeg").exists(),
    reason="source images missing",
)
@pytest.mark.skipif(
    not __import__("app.ai.adapters", fromlist=["settings"]).settings.qwen_api_key,
    reason="set ERP_QWEN_API_KEY (and Aliyun keys) to run live OCR",
)
def test_live_aliyun_qwen_extract():
    """End-to-end against the real APIs. Run manually with keys set."""
    extractor = AliyunQwenExtractor()
    res = extractor.extract(source_type="image", file_path=str(FIXTURE_DIR / "002_songdan_609621.jpeg"))
    # We never auto-submit; we surface for review.
    assert res.lines
    assert res.requires_human_review is True  # conservative default for handwritten
    print("\nLIVE RESULT:", json.dumps(asdict(res), ensure_ascii=False, indent=2))
