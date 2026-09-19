from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, LargeBinary, String, Text
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
    # The original bytes themselves. `file_path` names a file in *this
    # container*, and a container filesystem does not survive a redeploy: every
    # intake document older than the last deploy 404s on its own preview and
    # can never be re-parsed. Postgres does survive, so the bytes live here and
    # the path is a cache.
    #
    # `deferred=True` is load-bearing, not a micro-optimisation. The inbox lists
    # 50 documents per request and a photo is megabytes; without it, rendering a
    # list would pull every blob into memory to print filenames.
    file_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    document_meta: Mapped[dict] = mapped_column(JSON, default=dict)  # JSON


class IntakeJob(TimestampMixin):
    __tablename__ = "intake_jobs"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("intake_documents.id"), index=True, nullable=False
    )
    # `parked` = Gate 1 decided this is not an order, so no pipeline ran. It is
    # terminal but reversible: a human can promote it back to queued.
    # `rejected` = a HUMAN looked at the extraction and refused it (duplicate,
    # not an order, unreadable...). Terminal. Deliberately not folded into
    # `failed` (the machine never got that far) or `parked` (an automated
    # guess) — the difference is who decided, and it is the difference that
    # decides whether the row needs a second look.
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)  # queued|processing|completed|failed|needs_review|parked|rejected
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
