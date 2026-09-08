"""Pydantic schemas for the intake module (intake agent).

Response shapes match docs/AGENT_CONTRACTS.md §4 (IntakeDocument, IntakeJob)
and docs/EXECUTIVE_SUMMARY.md "AI output schema".
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SourceType = Literal["text", "image", "pdf", "excel", "email_body"]
JobStatus = Literal["queued", "processing", "completed", "failed"]


# --- Submit -------------------------------------------------------------------
class IntakeSubmit(BaseModel):
    """JSON body variant for /intake/submit (multipart handled separately)."""
    customer_id: str | None = None
    delivery_date: date | None = None
    source_type: SourceType
    raw_text: str | None = None


class IntakeSubmitResponse(BaseModel):
    document_id: str
    job_id: str


# --- IntakeDocument -----------------------------------------------------------
class IntakeDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str | None = None
    source_type: str
    original_filename: str | None = None
    file_url: str | None = None
    file_hash: str | None = None
    uploaded_by: str | None = None
    created_at: datetime
    # Embedded job summary (optional, populated by the GET /{id} endpoint)
    job_id: str | None = None
    job_status: str | None = None
    draft_order_id: str | None = None


# --- IntakeJob ----------------------------------------------------------------
class IntakeJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    status: str
    error: str | None = None
    retry_count: int = 0
    draft_order_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


# --- IntakeExtraction ---------------------------------------------------------
class IntakeExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    raw_output: dict[str, Any]
    overall_confidence: float | None = None
    parser_notes: str | None = None


# --- Extracted line (used inside raw_output) ----------------------------------
class ExtractedLine(BaseModel):
    raw_text: str
    matched_product_id: str | None = None
    matched_product_name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_code: str | None = None
    confidence: float = 0.0
    match_method: str = "unmatched"


# --- Retry response -----------------------------------------------------------
class RetryResponse(BaseModel):
    job_id: str
    status: str
    retry_count: int
