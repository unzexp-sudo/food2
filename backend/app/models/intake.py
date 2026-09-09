from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDMixin


class IntakeDocument(TimestampMixin):
    __tablename__ = "intake_documents"

    customer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("customers.id"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # text|image|pdf|excel|email_body
    original_filename: Mapped[str | None] = mapped_column(String(500))
    file_path: Mapped[str | None] = mapped_column(String(1000))
    file_hash: Mapped[str | None] = mapped_column(String(64))
    uploaded_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    document_meta: Mapped[dict] = mapped_column(JSON, default=dict)  # JSON


class IntakeJob(TimestampMixin):
    __tablename__ = "intake_jobs"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("intake_documents.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)  # queued|processing|completed|failed|needs_review
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(default=0)
    draft_order_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("orders.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    document: Mapped[IntakeDocument] = relationship()


class IntakeExtraction(UUIDMixin):
    __tablename__ = "intake_extractions"

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("intake_jobs.id"), index=True, nullable=False
    )
    raw_output: Mapped[dict] = mapped_column(JSON, nullable=False)  # immutable AI output
    overall_confidence: Mapped[float | None] = mapped_column()
    parser_notes: Mapped[str | None] = mapped_column(Text)
