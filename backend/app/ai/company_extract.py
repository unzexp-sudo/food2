"""Deterministic company-detail extraction — it proposes, it never decides.

Given the text of a customer document (a typed WeChat message, the text layer
of a PDF, or OCR output from a photo of a paper form), pull out the candidate
company details so a human can *verify* them instead of typing them from
scratch.

Hard rules, in priority order:

1. **Never invent a field.** If a label is absent, the field comes back as
   ``FieldDraft(None, 0.0, None, "none")``. There is no fuzzy matching, no
   similarity scoring, no model call, no dictionary of known companies — this
   module is a set of regular expressions and nothing else.
2. **Every value carries its evidence.** ``evidence`` is the exact source line
   the value was cut from, so the reviewer can see *why* the suggestion was
   made and reject it when it is wrong. A wrong customer or a wrong delivery
   address can take the company down; the human is the only thing standing
   between this output and a real order, so the output must be auditable.
3. **Deterministic and offline.** Same text in, same draft out, forever.

The draft is consumed by ``app/services/intake/company_proposal.py`` and stored
as ``document_meta["company_proposal"]`` on the intake document. It is never
written to ``customers``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "FieldDraft",
    "CompanyDraft",
    "extract_company_info",
    "pdf_text",
]


@dataclass
class FieldDraft:
    """One proposed field. ``value is None`` means "not found", not "unknown"."""

    value: str | None
    confidence: float          # 0.0–1.0, fixed per method — see CONFIDENCE
    evidence: str | None       # the exact source line that produced it
    method: str                # regex_name | regex_address | regex_phone
                               # | regex_contact | tax_id | none


@dataclass
class CompanyDraft:
    name: FieldDraft
    address: FieldDraft
    phone: FieldDraft
    contact: FieldDraft
    tax_id: FieldDraft
    source_kind: str           # pdf_text | ocr_text | plain_text
    raw_excerpt: str           # first 300 chars of the text used


# Fixed confidence per method. Deliberately flat and boring: the reviewer is
# meant to read the evidence line, not to trust a score. These numbers only
# order the review UI (which suggestion to look at first); they are NOT a
# probability that the value is correct.
CONFIDENCE = {
    "tax_id": 0.9,          # an 18-char USCC is self-validating; almost never prose
    "regex_phone": 0.85,    # digit-shaped, hard to confuse with anything else
    "regex_address": 0.8,
    "regex_name": 0.7,      # company names bleed into the surrounding sentence
    "regex_contact": 0.7,   # a person's name is short and easy to truncate
    "none": 0.0,
}

_EXCERPT_CHARS = 300

# --- Label vocabularies -------------------------------------------------------
# Real Chinese purchase orders / invoices / delivery notes write the same field
# half a dozen different ways, and OCR inserts stray spaces. Longer labels are
# tried first (see `_label_re`) so that "购货单位名称" wins over "购货单位".
NAME_LABELS = (
    "客户名称", "收货单位名称", "购货单位名称", "供货单位名称", "供应商名称",
    "单位名称", "公司名称", "收货单位", "购货单位", "供货单位", "供应商",
    "客户", "名称", "公司",
)
ADDRESS_LABELS = (
    "客户收货地址", "客户地址", "开票地址", "收货地址", "送货地址", "交货地址",
    "详细地址", "地址",
)
PHONE_LABELS = ("联系电话", "联系方式", "电话号码", "电话", "手机", "传真")
CONTACT_LABELS = ("联系人", "收货人", "经办人")
TAX_ID_LABELS = ("统一社会信用代码", "纳税人识别号", "税号")

# 18-char Unified Social Credit Code. The alphabet excludes I, O, S, V and Z.
USCC_RE = re.compile(r"[0-9A-HJ-NPQRTUWXY]{18}", re.IGNORECASE)

# A phone-ish run: a digit followed by digits, separators and brackets. Used
# only to sanity-check a candidate value, never to rewrite it.
_PHONE_SHAPE_RE = re.compile(r"[0-9][0-9\-\u2010-\u2015\s()（）]{4,}")
_PHONE_DIGITS_RE = re.compile(r"\d")

# CJK ideographs — used to stop a short generic label like "公司" from matching
# inside a longer word ("有限公司", "供货单位名称").
_CJK = "\u4e00-\u9fff"

# Leading / trailing noise to strip off a value: whitespace (incl. ideographic
# space), full- and half-width colons, and the punctuation that follows a label.
_LEAD_NOISE = " \t\u3000:：,，、;；.。=－-"
_TAIL_NOISE = " \t\u3000:：,，、;；.。-－"


def _label_re(labels: tuple[str, ...]) -> re.Pattern[str]:
    """Build a label regex that tolerates OCR spacing and prefers long labels.

    Each label becomes its characters joined by ``[\\s\\u3000]*`` so that
    ``联 系 电 话`` still matches. Longer labels are listed first so that
    ``购货单位名称`` wins over ``购货单位`` at the same position.

    Every label carries a "not preceded by a Chinese character" lookbehind.
    That is the rule that stops ``公司`` firing inside ``有限公司`` and
    ``供应商`` inside ``佛山市禅城区供应商大厦`` — the commonest way this
    extractor could propose something that was never on the page. The cost is
    a miss when a label is glued to preceding Chinese text (``我方供货单位名称``);
    a miss leaves the field empty, which is the safe direction to fail, and the
    compound forms are enumerated instead (see ``*_LABELS``).
    """
    parts = [
        r"(?<![" + _CJK + r"])" + r"[\s\u3000]*".join(re.escape(ch) for ch in label)
        for label in sorted(labels, key=len, reverse=True)
    ]
    return re.compile("(" + "|".join(parts) + ")")


_NAME_RE = _label_re(NAME_LABELS)
_ADDRESS_RE = _label_re(ADDRESS_LABELS)
_PHONE_RE = _label_re(PHONE_LABELS)
_CONTACT_RE = _label_re(CONTACT_LABELS)
_TAX_ID_RE = _label_re(TAX_ID_LABELS)

# Every label, so that a value can be cut short at the next field on the same
# line ("名称：佛山XX公司   地址：佛山市禅城区XX路1号" is one PDF line).
_ANY_LABEL_RE = _label_re(
    NAME_LABELS + ADDRESS_LABELS + PHONE_LABELS + CONTACT_LABELS + TAX_ID_LABELS
)


def _none() -> FieldDraft:
    return FieldDraft(None, 0.0, None, "none")


def _clean(value: str) -> str:
    """Strip the label's punctuation, separators and surrounding whitespace."""
    return value.strip(_LEAD_NOISE).strip(_TAIL_NOISE).strip()


def _value_after(line: str, match: re.Match[str]) -> str:
    """The text following a matched label, cut at the next field label."""
    rest = line[match.end():]
    nxt = _ANY_LABEL_RE.search(rest)
    if nxt is not None:
        rest = rest[: nxt.start()]
    return _clean(rest)


def _iter_lines(text: str):
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            yield line


def _first_match(
    text: str,
    label_re: re.Pattern[str],
    method: str,
    *,
    accept=lambda value: True,
) -> FieldDraft:
    """First line whose label yields an acceptable value. Never guesses."""
    for line in _iter_lines(text):
        match = label_re.search(line)
        if match is None:
            continue
        value = _value_after(line, match)
        if not value or not accept(value):
            # A label with nothing usable after it is not a match. Keep
            # scanning; do not fall back to a weaker interpretation.
            continue
        return FieldDraft(value, CONFIDENCE[method], line, method)
    return _none()


def _plausible_name(value: str) -> bool:
    """A name must look like a name, not like a stray punctuation run."""
    if len(value) < 2:
        return False
    return any(ch.isalnum() for ch in value)


def _plausible_phone(value: str) -> bool:
    """At least 6 digits, and shaped like a phone number somewhere inside."""
    if len(_PHONE_DIGITS_RE.findall(value)) < 6:
        return False
    return _PHONE_SHAPE_RE.search(value) is not None


def _extract_tax_id(text: str) -> FieldDraft:
    """Label-anchored USCC only.

    An unlabelled 18-character run could just as easily be an order number, and
    a wrong tax id is exactly the kind of false assumption this module exists to
    avoid — so a label is required.
    """
    for line in _iter_lines(text):
        match = _TAX_ID_RE.search(line)
        if match is None:
            continue
        code = USCC_RE.search(line[match.end():])
        if code is None:
            continue
        return FieldDraft(
            code.group(0).upper(), CONFIDENCE["tax_id"], line, "tax_id"
        )
    return _none()


def extract_company_info(text: str, *, source_kind: str = "plain_text") -> CompanyDraft:
    """Propose company details from ``text``. Pure function, no I/O.

    Every field is either a value with the exact source line that produced it,
    or an empty field. The first usable match per field wins; nothing is
    merged, ranked or inferred.
    """
    text = text or ""

    return CompanyDraft(
        name=_first_match(text, _NAME_RE, "regex_name", accept=_plausible_name),
        address=_first_match(text, _ADDRESS_RE, "regex_address"),
        phone=_first_match(text, _PHONE_RE, "regex_phone", accept=_plausible_phone),
        contact=_first_match(text, _CONTACT_RE, "regex_contact"),
        tax_id=_extract_tax_id(text),
        source_kind=source_kind,
        raw_excerpt=text[:_EXCERPT_CHARS],
    )


def pdf_text(file_path: str) -> str:
    """Text layer of a PDF, or ``""`` — this never raises.

    A scanned PDF has no text layer and comes back empty; the caller treats
    that as "no proposal", which is the correct answer. pypdf is imported
    lazily so a checkout without it still imports this module.
    """
    try:
        from pypdf import PdfReader
    except Exception:
        return ""

    try:
        reader = PdfReader(str(file_path))
    except Exception:
        return ""

    parts: list[str] = []
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text:
            parts.append(page_text)

    return "\n".join(parts)
