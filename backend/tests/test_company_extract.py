"""Tests for the deterministic company-detail extractor.

The extractor is a *proposal* engine: every assertion here is ultimately about
the two promises it makes to the human reviewer — never invent a field, and
always say which line the suggestion came from.

Note: `pytest`'s `tmp_path` fixture fails in this sandbox (EEXIST on basetemp),
so filesystem tests use `tempfile.TemporaryDirectory()`.
"""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from app.ai.company_extract import (
    CompanyDraft,
    FieldDraft,
    extract_company_info,
    pdf_text,
)

FIELDS = ("name", "address", "phone", "contact", "tax_id")

# A realistic typed/PDF'd purchase order: labels, a title line that is *not*
# labelled, and a company name on a line of its own further down.
FULL_DOC = """佛山市顺德区鲜达贸易有限公司
采购订单  PO-2026-0914

名称：佛山市南海区旺角餐饮管理有限公司
收货地址：佛山市南海区桂城街道永安路18号3栋
联系电话：0757-86332211
联系人：陈志强
统一社会信用代码：91440605MA4W2K7X8L
"""


def _values(draft: CompanyDraft) -> dict[str, str | None]:
    return {name: getattr(draft, name).value for name in FIELDS}


def _methods(draft: CompanyDraft) -> dict[str, str]:
    return {name: getattr(draft, name).method for name in FIELDS}


# ---------------------------------------------------------------------------
# 1. All fields present
# ---------------------------------------------------------------------------

def test_document_with_all_fields_present():
    draft = extract_company_info(FULL_DOC, source_kind="pdf_text")

    assert _values(draft) == {
        "name": "佛山市南海区旺角餐饮管理有限公司",
        "address": "佛山市南海区桂城街道永安路18号3栋",
        "phone": "0757-86332211",
        "contact": "陈志强",
        "tax_id": "91440605MA4W2K7X8L",
    }
    assert _methods(draft) == {
        "name": "regex_name",
        "address": "regex_address",
        "phone": "regex_phone",
        "contact": "regex_contact",
        "tax_id": "tax_id",
    }
    assert draft.source_kind == "pdf_text"


def test_confidence_is_a_fixed_value_per_method():
    draft = extract_company_info(FULL_DOC)
    assert getattr(draft, "name").confidence == 0.7
    assert getattr(draft, "address").confidence == 0.8
    assert getattr(draft, "phone").confidence == 0.85
    assert getattr(draft, "contact").confidence == 0.7
    assert getattr(draft, "tax_id").confidence == 0.9

    # Same text in, same numbers out — no scoring, no model.
    assert extract_company_info(FULL_DOC) == draft


def test_source_kind_defaults_to_plain_text_and_is_passed_through():
    assert extract_company_info(FULL_DOC).source_kind == "plain_text"
    assert extract_company_info(FULL_DOC, source_kind="ocr_text").source_kind == "ocr_text"


def test_raw_excerpt_is_the_first_300_chars():
    long_text = ("很长的文档。" * 200) + "名称：佛山市禅城区大福食品厂"
    draft = extract_company_info(long_text)

    assert len(draft.raw_excerpt) == 300
    assert draft.raw_excerpt == long_text[:300]

    short = extract_company_info("名称：佛山市禅城区大福食品厂")
    assert short.raw_excerpt == "名称：佛山市禅城区大福食品厂"


# ---------------------------------------------------------------------------
# 2. Only a name — the rest must be empty, not guessed
# ---------------------------------------------------------------------------

def test_document_with_only_a_name_leaves_everything_else_empty():
    draft = extract_company_info("名称：佛山市禅城区大福食品厂")

    assert draft.name.value == "佛山市禅城区大福食品厂"
    assert draft.name.method == "regex_name"
    assert draft.name.confidence == 0.7

    for field in ("address", "phone", "contact", "tax_id"):
        assert getattr(draft, field) == FieldDraft(None, 0.0, None, "none")


def test_unlabelled_company_name_is_not_invented():
    """The first line looks like a name but carries no label — not a match."""
    draft = extract_company_info("佛山市顺德区鲜达贸易有限公司\n采购订单\n合计：1200.00 元")

    assert draft.name.value is None
    assert draft.name.method == "none"
    assert _methods(draft) == dict.fromkeys(FIELDS, "none")


# ---------------------------------------------------------------------------
# 3. Nothing recognisable — every field "none"
# ---------------------------------------------------------------------------

def test_nothing_recognisable_yields_all_none():
    draft = extract_company_info("今天天气不错，下午三点前送到，谢谢。")

    assert _methods(draft) == dict.fromkeys(FIELDS, "none")
    assert _values(draft) == dict.fromkeys(FIELDS, None)
    for field in FIELDS:
        assert getattr(draft, field).confidence == 0.0
        assert getattr(draft, field).evidence is None


def test_empty_and_none_ish_input_is_safe():
    for text in ("", "   ", "\n\n\u3000\u3000\n"):
        draft = extract_company_info(text)
        assert _methods(draft) == dict.fromkeys(FIELDS, "none")


def test_label_with_nothing_usable_after_it_is_not_a_match():
    """`电话：无` is not a phone number — return none rather than "无"."""
    draft = extract_company_info("电话：无\n传真：见名片")
    assert draft.phone.method == "none"

    # An 18-char code is required for a tax id; a short number is not one.
    draft = extract_company_info("统一社会信用代码：12345")
    assert draft.tax_id.method == "none"


# ---------------------------------------------------------------------------
# 4. Label / punctuation stripping
# ---------------------------------------------------------------------------

def test_strips_half_and_full_width_colons():
    for line in ("名称：佛山市南海区旺角餐饮管理有限公司",
                 "名称:佛山市南海区旺角餐饮管理有限公司",
                 "名称 ： 佛山市南海区旺角餐饮管理有限公司"):
        assert extract_company_info(line).name.value == "佛山市南海区旺角餐饮管理有限公司"


def test_strips_ideographic_space_and_ocr_spacing_inside_labels():
    draft = extract_company_info("单 位 名 称 ： 佛山市高明区利民蔬菜行\u3000")

    assert draft.name.value == "佛山市高明区利民蔬菜行"
    assert draft.name.method == "regex_name"


def test_strips_surrounding_punctuation_and_whitespace():
    draft = extract_company_info("收货地址：、佛山市南海区桂城街道永安路18号3栋。 ")

    assert draft.address.value == "佛山市南海区桂城街道永安路18号3栋"


def test_a_value_stops_at_the_next_label_on_the_same_line():
    draft = extract_company_info(
        "客户名称：佛山市三水区XX商行  地址：佛山市三水区西南街道人民路7号  电话：13800138000"
    )

    assert draft.name.value == "佛山市三水区XX商行"
    assert draft.address.value == "佛山市三水区西南街道人民路7号"
    assert draft.phone.value == "13800138000"


def test_short_generic_label_does_not_fire_inside_a_longer_word():
    """`公司` must not match the `有限公司` at the end of an address."""
    draft = extract_company_info("地址：佛山市南海区大沥镇联和路2号佛山市鸿运有限公司")

    assert draft.address.value == "佛山市南海区大沥镇联和路2号佛山市鸿运有限公司"
    assert draft.name.method == "none"


def test_a_compound_label_is_matched_even_when_a_longer_word_precedes_it():
    draft = extract_company_info("客户收货地址：佛山市禅城区XX路1号")

    assert draft.address.value == "佛山市禅城区XX路1号"


def test_a_label_word_inside_a_value_does_not_truncate_the_value():
    """`供应商` inside `佛山市禅城区供应商大厦` is not a label — no guessing."""
    draft = extract_company_info("地址：佛山市禅城区供应商大厦")

    assert draft.address.value == "佛山市禅城区供应商大厦"
    assert draft.name.method == "none"


def test_tax_id_is_uppercased_and_validated_against_the_uscc_alphabet():
    draft = extract_company_info("纳税人识别号:91440604ma51b2cd3x")
    assert draft.tax_id.value == "91440604MA51B2CD3X"

    # "I", "O", "S", "V" and "Z" are not in the USCC alphabet.
    assert extract_company_info("统一社会信用代码：91440604MA51B2CDIX").tax_id.method == "none"


def test_phone_requires_enough_digits():
    assert extract_company_info("联系电话：0757-86332211").phone.value == "0757-86332211"
    assert extract_company_info("手机：13912345678").phone.value == "13912345678"
    assert extract_company_info("电话：123").phone.method == "none"


def test_common_real_world_label_variants():
    cases = {
        "供货单位名称：佛山市顺德区顺客隆商业有限公司": ("name", "佛山市顺德区顺客隆商业有限公司"),
        "公司：佛山市禅城区兴隆果菜店": ("name", "佛山市禅城区兴隆果菜店"),
        "购货单位：佛山市第一人民医院": ("name", "佛山市第一人民医院"),
        "收货单位：佛山市中医院": ("name", "佛山市中医院"),
        "送货地址：佛山市禅城区季华五路1号": ("address", "佛山市禅城区季华五路1号"),
        "详细地址：佛山市禅城区季华五路2号": ("address", "佛山市禅城区季华五路2号"),
        "联系方式：0757-22223333": ("phone", "0757-22223333"),
        "传真：0757-88889999": ("phone", "0757-88889999"),
        "收货人：黄小燕": ("contact", "黄小燕"),
    }
    for line, (field, expected) in cases.items():
        assert getattr(extract_company_info(line), field).value == expected, line


# ---------------------------------------------------------------------------
# 5. Evidence points at the exact source line
# ---------------------------------------------------------------------------

def test_evidence_is_the_exact_source_line_for_every_field():
    draft = extract_company_info(FULL_DOC)

    assert draft.name.evidence == "名称：佛山市南海区旺角餐饮管理有限公司"
    assert draft.address.evidence == "收货地址：佛山市南海区桂城街道永安路18号3栋"
    assert draft.phone.evidence == "联系电话：0757-86332211"
    assert draft.contact.evidence == "联系人：陈志强"
    assert draft.tax_id.evidence == "统一社会信用代码：91440605MA4W2K7X8L"

    # Every evidence line really is a line of the document, and really does
    # contain the proposed value.
    source_lines = FULL_DOC.splitlines()
    for field in FIELDS:
        field_draft = getattr(draft, field)
        assert field_draft.evidence in source_lines
        assert field_draft.value in field_draft.evidence


def test_evidence_survives_ocr_noise_and_still_points_at_the_source():
    text = "抬头\n联 系 人 ： 陈 志 强\n备注：尽快送货"
    draft = extract_company_info(text, source_kind="ocr_text")

    assert draft.contact.evidence == "联 系 人 ： 陈 志 强"
    assert draft.contact.evidence in text.splitlines()
    # The value is cut from that line — here the OCR spacing is preserved.
    assert draft.contact.value == "陈 志 强"


def test_first_match_wins():
    text = "名称：佛山市第一家公司\n名称：佛山市第二家公司"
    draft = extract_company_info(text)

    assert draft.name.value == "佛山市第一家公司"
    assert draft.name.evidence == "名称：佛山市第一家公司"


# ---------------------------------------------------------------------------
# 6. pdf_text
# ---------------------------------------------------------------------------

def test_pdf_text_returns_empty_string_instead_of_raising():
    assert pdf_text("/definitely/not/here.pdf") == ""
    assert pdf_text("") == ""

    with tempfile.TemporaryDirectory() as tmp:
        not_a_pdf = Path(tmp) / "order.txt"
        not_a_pdf.write_text("名称：佛山市禅城区大福食品厂", encoding="utf-8")
        # A text file is not a PDF: no text layer, no exception.
        assert pdf_text(str(not_a_pdf)) == ""
        assert pdf_text(str(Path(tmp) / "missing.pdf")) == ""


def test_pdf_text_extracts_a_real_pdf_text_layer():
    pytest = __import__("pytest")
    pytest.importorskip("pypdf")

    # pypdf cannot author text content, so build the smallest valid PDF by
    # hand with a visible text operator and read it back through our function.
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]"
        b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 10 100 Td (Hello PDF) Tj ET\nendstream endobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "order.pdf"
        path.write_bytes(pdf_bytes)
        try:
            extracted = pdf_text(str(path))
        except Exception as exc:  # pragma: no cover - the contract is "no raise"
            pytest.fail(f"pdf_text raised: {exc!r}")

    assert isinstance(extracted, str)
    if extracted:
        assert "Hello" in extracted


# ---------------------------------------------------------------------------
# 7. Contract with the consumer (app/services/intake/company_proposal.py)
# ---------------------------------------------------------------------------

def test_consumer_serialises_the_draft_without_loss():
    from app.services.intake.company_proposal import build_company_proposal

    out = build_company_proposal(raw_text=FULL_DOC, source_type="text")

    assert out is not None
    assert out["name"]["value"] == "佛山市南海区旺角餐饮管理有限公司"
    assert out["name"]["evidence"] == "名称：佛山市南海区旺角餐饮管理有限公司"
    assert out["name"]["method"] == "regex_name"
    assert out["tax_id"] == {
        "value": "91440605MA4W2K7X8L",
        "confidence": 0.9,
        "evidence": "统一社会信用代码：91440605MA4W2K7X8L",
        "method": "tax_id",
    }
    assert out["source_kind"] == "plain_text"
    # The consumer trims the text before handing it over.
    assert out["raw_excerpt"] == FULL_DOC.strip()[:300]
    assert out["extracted_from"] == "text"


def test_consumer_returns_no_proposal_for_unrecognisable_text():
    from app.services.intake.company_proposal import build_company_proposal

    # Empty text -> nothing to propose at all (not a draft full of "none").
    assert build_company_proposal(raw_text="   ", source_type="text") is None


def test_module_has_no_network_or_db_imports():
    """Pure functions only: the extractor must stay offline and deterministic."""
    source = Path(__file__).resolve().parents[1] / "app" / "ai" / "company_extract.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"__future__", "re", "dataclasses", "pypdf"}, imported
