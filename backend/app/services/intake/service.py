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
from sqlalchemy.orm.attributes import flag_modified

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
    parked: bool = False,
) -> tuple[IntakeDocument, IntakeJob]:
    """Create IntakeDocument + IntakeJob(queued), kick off background pipeline.

    For raw_text, write it to a .txt file so we keep an immutable original.
    For uploaded files, store the bytes as-is.

    `parked=True` creates the document with a job in the terminal `parked`
    state and does NOT run the pipeline — Gate 1 decided this is not an order,
    so there is nothing worth spending a parse on. The original is still
    stored exactly as normal, which is what makes a parked message
    recoverable: promoting it later parses it as if it had never been parked.
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
        status="parked" if parked else "queued",
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
        after={"status": job.status, "document_id": doc.id},
        summary=(
            f"Intake job parked for document {doc.id} (not an order)"
            if parked
            else f"Intake job queued for document {doc.id}"
        ),
    )

    # Kick off the background pipeline — but never for a parked message:
    # not paying for a parse is the entire point of parking it.
    if not parked and background_tasks is not None:
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
    status: str | None = None,
    pending_first: bool = False,
    include_parked: bool = False,
) -> tuple[list[tuple[IntakeDocument, IntakeJob | None]], int]:
    """Return (documents+latest_job, total).

    `status` filters on the *latest job's* status (e.g. "needs_review"), which
    is how the inbox picks out the orders still waiting for a human. The UI
    sends this filter, so without it the status dropdown silently did nothing.

    `pending_first` floats unreviewed orders to the top so a reviewer opening
    the inbox sees the work queue, not yesterday's confirmed orders.

    `include_parked` defaults to False: Gate 1 already decided these are not
    orders, so showing them by default would defeat the point of parking them.
    Ask for them explicitly (`status="parked"` or `include_parked=True`) when
    you want to audit or promote them.
    """
    q = db.query(IntakeDocument)
    if customer_id:
        q = q.filter(IntakeDocument.customer_id == customer_id)
    if source_type:
        q = q.filter(IntakeDocument.source_type == source_type)
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

    if not include_parked and status != "parked":
        result = [(d, j) for d, j in result if not (j is not None and j.status == "parked")]

    if status:
        result = [(d, j) for d, j in result if j is not None and j.status == status]

    if pending_first:
        # Stable sort: needs_review first, everything else keeps its
        # created_at desc order.
        result.sort(key=lambda pair: 0 if (pair[1] and pair[1].status == "needs_review") else 1)

    return result, len(result)


def count_pending_review(db: Session) -> int:
    """How many jobs are waiting for a human (the "unread" count).

    Counts `needs_review` only. Gate 1 `parked` messages are deliberately
    excluded — they are not work waiting for anyone, and folding them in
    would make the badge claim attention is needed when it is not. See
    `count_parked` for that separate number.
    """
    return db.query(IntakeJob).filter(IntakeJob.status == "needs_review").count()


def count_parked(db: Session) -> int:
    """How many messages Gate 1 decided are not orders.

    Kept visible on purpose. Parking is a bet that a message was junk; if the
    bet is wrong the order is invisible, so this number and the parked list
    must stay one click away.
    """
    return db.query(IntakeJob).filter(IntakeJob.status == "parked").count()


def promote_parked_job(
    db: Session,
    job: IntakeJob,
    *,
    actor: User | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> IntakeJob:
    """A human says "this was an order after all" — put it back in the queue.

    Gate 3 for Gate 1: no classifier is trusted blindly, so a wrong park is
    recoverable rather than fatal. The original bytes were stored at park
    time, so this parses the message exactly as it would have originally.
    """
    if job.status != "parked":
        raise ValueError(f"Only parked jobs can be promoted (status is {job.status})")

    before = _job_out(job)
    job.status = "queued"
    job.error = None
    job.started_at = None
    job.finished_at = None
    db.flush()

    # Record that a human overrode the classifier, so the same message cannot
    # be re-parked by a later run and so the audit trail shows who disagreed.
    doc = db.get(IntakeDocument, job.document_id)
    if doc is not None:
        meta = dict(doc.document_meta or {})
        wecom = meta.get("wecom")
        # The live verdict lives under meta["wecom"]["triage"] for WeCom
        # messages; keep both copies in step so the promote is visible
        # wherever the report reads it from.
        triage = dict(meta.get("triage") or (wecom or {}).get("triage") or {})
        triage["overridden"] = True
        triage["overridden_by"] = getattr(actor, "id", None)
        meta["triage"] = triage
        if isinstance(wecom, dict):
            wecom = dict(wecom)
            wecom["triage"] = triage
            meta["wecom"] = wecom
        doc.document_meta = meta
        flag_modified(doc, "document_meta")
        db.flush()

    log_audit(
        db, actor, "IntakeJob", job.id, "promote",
        before=before, after=_job_out(job),
        summary=f"Parked job {job.id} promoted by a human — re-queued for processing",
    )
    if background_tasks is not None:
        background_tasks.add_task(_run_pipeline_task, job.id)
    return job


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
    include_parked: bool = False,
) -> tuple[list[IntakeJob], int]:
    """List intake jobs.

    Parked jobs are hidden by default for the same reason `list_documents`
    hides them: they are not work, and listing them defeats parking them.
    Pass `status="parked"` (or `include_parked=True`) to audit the pile.
    """
    q = db.query(IntakeJob)
    if not include_parked and status != "parked":
        q = q.filter(IntakeJob.status != "parked")
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
