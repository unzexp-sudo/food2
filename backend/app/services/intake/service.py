"""Intake service — file storage, document/job CRUD, pipeline orchestration.

The service owns the file-storage logic (sha256 hash, path on disk) and the
creation of IntakeDocument + IntakeJob rows. The background pipeline
(app/ai/pipeline.py) is invoked via FastAPI BackgroundTasks.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import (
    Customer,
    IntakeDocument,
    IntakeExtraction,
    IntakeJob,
    User,
)


# --- Helpers ------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _ext_for(source_type: str, filename: str | None, raw_text: str | None) -> str:
    """Determine the file extension to use when storing the original."""
    if filename and "." in filename:
        return Path(filename).suffix.lower()
    defaults = {
        "text": ".txt",
        "email_body": ".txt",
        "image": ".png",
        "pdf": ".pdf",
        "excel": ".xlsx",
    }
    return defaults.get(source_type, ".bin")


def _store_original(
    *,
    source_type: str,
    content: bytes,
    filename: str | None,
) -> tuple[str, str]:
    """Write the original bytes to disk. Returns (file_path, file_hash)."""
    ext = _ext_for(source_type, filename, content)
    file_name = f"{uuid.uuid4().hex}{ext}"
    path = settings.files_path("intake", file_name)
    path.write_bytes(content)
    return str(path), _sha256(content)


def _doc_out(doc: IntakeDocument, job: IntakeJob | None = None) -> dict:
    """Serialize IntakeDocument (+ embedded job summary)."""
    file_url = f"/api/v1/intake/documents/{doc.id}/file"
    return {
        "id": doc.id,
        "customer_id": doc.customer_id,
        "source_type": doc.source_type,
        "original_filename": doc.original_filename,
        "file_url": file_url,
        "file_hash": doc.file_hash,
        "uploaded_by": doc.uploaded_by,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "job_id": job.id if job else None,
        "job_status": job.status if job else None,
        "draft_order_id": job.draft_order_id if job else None,
    }


def _job_out(job: IntakeJob) -> dict:
    return {
        "id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "error": job.error,
        "retry_count": job.retry_count,
        "draft_order_id": job.draft_order_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _extraction_out(ext: IntakeExtraction) -> dict:
    return {
        "id": ext.id,
        "job_id": ext.job_id,
        "raw_output": ext.raw_output,
        "overall_confidence": ext.overall_confidence,
        "parser_notes": ext.parser_notes,
    }


# --- Submit -------------------------------------------------------------------

def submit_intake(
    db: Session,
    *,
    customer_id: str | None,
    delivery_date: date | None,
    source_type: str,
    raw_text: str | None = None,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    actor: User | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> tuple[IntakeDocument, IntakeJob]:
    """Create IntakeDocument + IntakeJob(queued), kick off background pipeline.

    For raw_text, write it to a .txt file so we keep an immutable original.
    For uploaded files, store the bytes as-is.
    """
    if customer_id is not None:
        customer = db.get(Customer, customer_id)
        if customer is None:
            raise ValueError(f"Customer not found: {customer_id}")

    # Determine the content to store.
    if file_bytes is not None:
        content = file_bytes
        original_filename = filename
    elif raw_text is not None:
        content = raw_text.encode("utf-8")
        original_filename = filename or "intake.txt"
    else:
        raise ValueError("Either raw_text or file must be provided")

    file_path, file_hash = _store_original(
        source_type=source_type,
        content=content,
        filename=original_filename,
    )

    meta: dict = {"source_type": source_type}
    if raw_text:
        meta["raw_text"] = raw_text
    if delivery_date:
        meta["delivery_date"] = delivery_date.isoformat()

    doc = IntakeDocument(
        customer_id=customer_id,
        source_type=source_type,
        original_filename=original_filename,
        file_path=file_path,
        file_hash=file_hash,
        uploaded_by=actor.id if actor else None,
        document_meta=meta,
    )
    db.add(doc)
    db.flush()

    job = IntakeJob(
        document_id=doc.id,
        status="queued",
        retry_count=0,
    )
    db.add(job)
    db.flush()

    log_audit(
        db, actor, "IntakeDocument", doc.id, "create",
        before=None,
        after={"source_type": source_type, "customer_id": customer_id, "filename": original_filename},
        summary=f"Intake submitted: source={source_type}, customer={customer_id or 'none'}",
    )
    log_audit(
        db, actor, "IntakeJob", job.id, "create",
        before=None,
        after={"status": "queued", "document_id": doc.id},
        summary=f"Intake job queued for document {doc.id}",
    )

    # Kick off the background pipeline.
    if background_tasks is not None:
        background_tasks.add_task(_run_pipeline_task, job.id)

    return doc, job


def _run_pipeline_task(job_id: str) -> None:
    """BackgroundTasks wrapper — owns its own DB session."""
    db = SessionLocal()
    try:
        from app.ai.pipeline import process_intake_job
        process_intake_job(db, job_id)
    finally:
        db.close()


# --- Document listing/get -----------------------------------------------------

def list_documents(
    db: Session,
    *,
    customer_id: str | None = None,
    source_type: str | None = None,
) -> tuple[list[tuple[IntakeDocument, IntakeJob | None]], int]:
    """Return (documents+latest_job, total)."""
    q = db.query(IntakeDocument)
    if customer_id:
        q = q.filter(IntakeDocument.customer_id == customer_id)
    if source_type:
        q = q.filter(IntakeDocument.source_type == source_type)
    total = q.count()
    docs = q.order_by(IntakeDocument.created_at.desc()).all()

    # Fetch the latest job per document (there's typically one; pick the
    # highest retry_count to support retry-after-failure scenarios).
    result: list[tuple[IntakeDocument, IntakeJob | None]] = []
    for d in docs:
        job = (
            db.query(IntakeJob)
            .filter(IntakeJob.document_id == d.id)
            .order_by(IntakeJob.retry_count.desc(), IntakeJob.created_at.desc())
            .first()
        )
        result.append((d, job))
    return result, total


def get_document(db: Session, document_id: str) -> IntakeDocument | None:
    return db.get(IntakeDocument, document_id)


def get_job_for_document(db: Session, document_id: str) -> IntakeJob | None:
    return (
        db.query(IntakeJob)
        .filter(IntakeJob.document_id == document_id)
        .order_by(IntakeJob.retry_count.desc(), IntakeJob.created_at.desc())
        .first()
    )


# --- Job listing/get/retry ---------------------------------------------------

def list_jobs(
    db: Session,
    *,
    status: str | None = None,
) -> tuple[list[IntakeJob], int]:
    q = db.query(IntakeJob)
    if status:
        q = q.filter(IntakeJob.status == status)
    total = q.count()
    return q.order_by(IntakeJob.created_at.desc()).all(), total


def get_job(db: Session, job_id: str) -> IntakeJob | None:
    return db.get(IntakeJob, job_id)


def retry_job(
    db: Session,
    job: IntakeJob,
    *,
    actor: User | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> IntakeJob:
    """Reset job to queued, increment retry_count, re-queue pipeline."""
    before = _job_out(job)
    job.status = "queued"
    job.error = None
    job.retry_count = (job.retry_count or 0) + 1
    job.started_at = None
    job.finished_at = None
    db.flush()
    log_audit(
        db, actor, "IntakeJob", job.id, "retry",
        before=before, after=_job_out(job),
        summary=f"Retrying intake job {job.id} (attempt {job.retry_count})",
    )
    if background_tasks is not None:
        background_tasks.add_task(_run_pipeline_task, job.id)
    return job


# --- Extraction ---------------------------------------------------------------

def get_extraction_for_job(db: Session, job_id: str) -> IntakeExtraction | None:
    return (
        db.query(IntakeExtraction)
        .filter(IntakeExtraction.job_id == job_id)
        .order_by(IntakeExtraction.id)
        .first()
    )
