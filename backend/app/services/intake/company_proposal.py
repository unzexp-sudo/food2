"""Company pre-fill — extraction proposes, it never decides.

`app/ai/company_extract.py` is owned by another agent and may not exist yet in
a given checkout, so it is imported defensively: a missing or failing extractor
degrades to "no proposal" instead of blocking ingest. A WeCom order that cannot
be pre-filled is merely slower; one that fails to ingest is lost.

The result is stored on the intake document as
`document_meta["company_proposal"]` and is shown to a human on the bind screen
with its evidence line. It is NEVER written to `customers` — a proposal is a
suggestion with a source line attached, and the whole point of this module is
that a human turns it into a fact.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("erp.intake.company_proposal")


def _extractor():
    try:
        from app.ai import company_extract
    except Exception:  # module not written yet, or it failed to import
        return None
    if not hasattr(company_extract, "extract_company_info"):
        return None
    return company_extract


def _field_out(field: Any) -> dict:
    return {
        "value": getattr(field, "value", None),
        "confidence": getattr(field, "confidence", 0.0),
        "evidence": getattr(field, "evidence", None),
        "method": getattr(field, "method", "none"),
    }


def _draft_out(draft: Any) -> dict:
    return {
        "name": _field_out(getattr(draft, "name", None)),
        "address": _field_out(getattr(draft, "address", None)),
        "phone": _field_out(getattr(draft, "phone", None)),
        "contact": _field_out(getattr(draft, "contact", None)),
        "tax_id": _field_out(getattr(draft, "tax_id", None)),
        "source_kind": getattr(draft, "source_kind", None),
        "raw_excerpt": getattr(draft, "raw_excerpt", None),
    }


def build_company_proposal(
    *,
    raw_text: str | None,
    source_type: str,
    file_path: str | None = None,
    filename: str | None = None,
) -> dict | None:
    """Return a JSON-serialisable CompanyDraft, or None when there is nothing.

    Text first (a typed message is the cheapest, cleanest source), then PDF
    text. Images have no text at ingest time — OCR runs later in the pipeline
    and is not available synchronously, so no proposal is attempted for them
    rather than proposing from an empty string.
    """
    mod = _extractor()
    if mod is None:
        return None

    text = (raw_text or "").strip()
    source_kind = "plain_text"

    if not text and file_path and str(file_path).lower().endswith(".pdf"):
        pdf_text = getattr(mod, "pdf_text", None)
        if pdf_text is None:
            return None
        text = (pdf_text(file_path) or "").strip()
        source_kind = "pdf_text"

    if not text:
        return None

    try:
        draft = mod.extract_company_info(text, source_kind=source_kind)
    except Exception:
        # Extraction is an enhancement. It must never be able to fail an ingest.
        logger.warning("company_proposal: extraction failed (ignored)", exc_info=True)
        return None

    out = _draft_out(draft)
    out["extracted_from"] = source_type
    return out
