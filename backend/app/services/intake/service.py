"""Intake service — file storage, document/job CRUD, pipeline orchestration.

The service owns the file-storage logic (sha256 hash, bytes in the row, path on
disk as a cache) and the creation of IntakeDocument + IntakeJob rows. The
background pipeline (app/ai/pipeline.py) is invoked via FastAPI BackgroundTasks.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

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
from app.services.identity.service import identity_block_of, is_unbound
from app.services.intake.company_proposal import build_company_proposal

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class StoredOriginal:
    """Where an intake original ended up: in the row, and maybe also on disk."""

    file_data: bytes
    file_hash: str
    file_path: str | None


def _store_original(
    *,
    source_type: str,
    content: bytes,
    filename: str | None,
) -> StoredOriginal:
    """Keep the original's bytes in the row, and *cache* them on disk.

    The bytes are the record; the path is an optimisation. That ordering is the
    entire point of this function. `settings.files_path()` points inside the
    container, and a container filesystem is discarded on every redeploy — so a
    document stored as a path alone stops existing the moment the service
    restarts. That is not a theoretical loss: it is why the review screen shows
    "File not found on disk" for anything uploaded before the last deploy, and
    why those documents can never be re-parsed, retried or corrected.

    The disk copy is kept because it is free and because it keeps local
    development, and the synchronous PDF company proposal, working exactly as
    they did before. It is strictly best-effort: a disk that is full or read-only
    must not fail an ingest that the database can serve perfectly well.
    """
    ext = _ext_for(source_type, filename, content)
    file_name = f"{uuid.uuid4().hex}{ext}"

    path: str | None = None
    try:
        disk_path = settings.files_path("intake", file_name)
        disk_path.write_bytes(content)
        path = str(disk_path)
    except OSError:
        # Worth a warning, not a failure: this is the cache, not the record.
        logger.warning(
            "intake: could not cache original %s on disk — it is still stored in "
            "the database and can be served from there",
            file_name,
            exc_info=True,
        )

    return StoredOriginal(
        file_data=content,
        file_hash=_sha256(content),
        file_path=path,
    )


def _suffix_of(doc: IntakeDocument) -> str:
    """The extension to give the temp file, from whichever field still has it."""
    if doc.original_filename and "." in doc.original_filename:
        return Path(doc.original_filename).suffix.lower()
    if doc.file_path and "." in doc.file_path:
        return Path(doc.file_path).suffix.lower()
    return _ext_for(doc.source_type, None, None)


@contextmanager
def materialize_original(db: Session, doc: IntakeDocument) -> Iterator[str]:
    """Yield a real filesystem path holding this document's original.

    Every extractor takes a *path* and opens it itself (`PdfReader`, `openpyxl`,
    `open(..., "rb")` inside the OCR adapters), so the bytes have to become a
    file for the duration of a read. The tempting alternative — pass the bytes
    around in memory — means rewriting every reader for no gain.

    It is a context manager, and the file is deleted on the way out even when
    extraction raises, because a Mistral conversion takes seconds to a minute
    and a temp file per document would otherwise accumulate for the lifetime of
    the container.

    The disk cache is preferred when it is present only because it is already
    there; it is never trusted to be there, which is the bug this replaces.
    """
    if doc.file_path:
        cached = Path(doc.file_path)
        if cached.exists():
            yield str(cached)
            return

    data = doc.file_data
    if not data:
        raise FileNotFoundError(
            "the original for this document is not available — it was stored "
            "before originals were kept in the database, and the container "
            "copy it pointed at is gone"
        )

    fd, tmp = tempfile.mkstemp(suffix=_suffix_of(doc), prefix="intake-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        yield tmp
    finally:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover - already gone is the desired state
            pass


def customers_for(db: Session, docs: list[IntakeDocument]) -> dict[str, Customer]:
    """Batch-load the customers behind these documents, keyed by customer id.

    One query for the whole page, not one per row — the inbox lists 50 documents
    at a time and a per-row lookup would be 50 queries to render 50 names.
    """
    ids = {d.customer_id for d in docs if d.customer_id}
    if not ids:
        return {}
    rows = db.query(Customer).filter(Customer.id.in_(ids)).all()
    return {c.id: c for c in rows}


def _doc_out(
    doc: IntakeDocument,
    job: IntakeJob | None = None,
    *,
    customer: Customer | None = None,
) -> dict:
    """Serialize IntakeDocument (+ embedded job summary).

    `customer` is passed in rather than looked up here so callers can batch it;
    see `customers_for`. It carries the *name* because `customer_id` alone cannot
    render a name, and the inbox's Customer column reads one.

    `identity` is the conversation's binding state. It has to be on the row: a
    document held for want of a customer looks exactly like a normal one
    otherwise, and the reviewer's only clue was a banner telling them to go and
    bind something they could not identify from the list.

    `display_name` / `corp_name` / `alias` are the same three fields
    `list_unbound_chats` already exposes, and they are here for the same reason:
    `status: unbound` tells the reviewer a decision is needed but not *who* is
    on the other end, so a held row could only be described by its `chat_key`.
    They are contact metadata forwarded by the gateway, never evidence of
    identity — the bind is still made on `kind` + `value` alone.
    """
    file_url = f"/api/v1/intake/documents/{doc.id}/file"
    block = identity_block_of(doc)
    # Why a human threw this away, when one did. Without it the row says
    # `rejected` and nothing else — and "duplicate" is precisely what the next
    # person needs to know before they wonder why the customer's order never
    # arrived and order it again by hand.
    meta = doc.document_meta or {}
    return {
        "rejection": meta.get("review_rejection"),
        "id": doc.id,
        "customer_id": doc.customer_id,
        "customer_name_en": customer.name_en if customer else None,
        "customer_name_zh": customer.name_zh if customer else None,
        "source_type": doc.source_type,
        "original_filename": doc.original_filename,
        "file_url": file_url,
        "file_hash": doc.file_hash,
        "uploaded_by": doc.uploaded_by,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "job_id": job.id if job else None,
        "job_status": job.status if job else None,
        "draft_order_id": job.draft_order_id if job else None,
        "identity": {
            "status": block.get("status"),
            "chat_key": block.get("chat_key"),
            "kind": block.get("kind"),
            "value": block.get("value"),
            "method": block.get("method"),
            "reason": block.get("reason"),
            # Who is talking, as far as WeCom tells us. Display only — these
            # name the conversation so a held row is identifiable; they never
            # resolve a customer.
            "display_name": block.get("display_name"),
            "corp_name": block.get("corp_name"),
            "alias": block.get("alias"),
        },
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


def _extraction_out(
    ext: IntakeExtraction, document: IntakeDocument | None = None
) -> dict:
    """Serialize an extraction, optionally with the source it was read from.

    `raw_output` is returned exactly as stored, because it is the immutable
    record of what the extractor produced. The reviewer's corrections are
    deliberately NOT folded into it — they live on
    `document_meta["human_review"]` and are returned alongside — so "what the
    machine read" and "what a person decided" can never be confused for each
    other after the fact.

    `document` is optional and adds the SOURCE. The review screen has to show
    the operator what was actually sent next to what was read out of it, and
    for a text order the source IS the note. Without it the "Original" column
    had nothing to render and fell back to "No preview image for this source
    type" — a true sentence about an image and a useless one about a note.
    """
    out = {
        "id": ext.id,
        "job_id": ext.job_id,
        "raw_output": ext.raw_output,
        "overall_confidence": ext.overall_confidence,
        "parser_notes": ext.parser_notes,
    }
    if document is not None:
        meta = document.document_meta or {}
        out["source_type"] = document.source_type
        out["original_filename"] = document.original_filename
        out["source_text"] = meta.get("raw_text")
        out["rejection"] = meta.get("review_rejection")
        out["human_review"] = meta.get("human_review")
    return out


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

    The original is kept byte-for-byte on the document row itself, whatever the
    source: a typed message is stored as its UTF-8 bytes, an upload as-is. That
    is what makes the preview renderable and a bad read re-runnable after the
    container has been replaced. See `_store_original`.

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

    stored = _store_original(
        source_type=source_type,
        content=content,
        filename=original_filename,
    )

    meta: dict = {"source_type": source_type}
    if raw_text:
        meta["raw_text"] = raw_text
    if delivery_date:
        meta["delivery_date"] = delivery_date.isoformat()

    # Company pre-fill (§2.2): a proposal for the bind screen, nothing more.
    # It is stored next to the document, never on `customers`.
    # Reads the disk cache, so it is best-effort by construction: when the cache
    # is unavailable (read-only disk) the proposal is simply not made, and the
    # ingest still succeeds. A PDF's proposal is an enhancement, never a gate.
    proposal = build_company_proposal(
        raw_text=raw_text,
        source_type=source_type,
        file_path=stored.file_path,
        filename=original_filename,
    )
    if proposal is not None:
        meta["company_proposal"] = proposal

    doc = IntakeDocument(
        customer_id=customer_id,
        source_type=source_type,
        original_filename=original_filename,
        file_path=stored.file_path,
        file_hash=stored.file_hash,
        file_data=stored.file_data,
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
    unbound_only: bool = False,
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

    `unbound_only` narrows to documents held for want of a customer binding —
    the rows an operator can do nothing with until they bind the conversation.
    Filtered in Python because the SQL form of this JSON comparison is a known
    trap (see `identity.service._meta_status_expr`) and the rows are already in
    memory. Server-side on purpose: filtering this in the browser would show a
    page of 20 that silently hid every match on the pages not fetched.
    """
    q = db.query(IntakeDocument)
    if customer_id:
        q = q.filter(IntakeDocument.customer_id == customer_id)
    if source_type:
        q = q.filter(IntakeDocument.source_type == source_type)
    docs = q.order_by(IntakeDocument.created_at.desc()).all()

    if unbound_only:
        docs = [d for d in docs if is_unbound(d)]

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


def count_unbound(db: Session) -> int:
    """How many documents are held for want of a customer binding.

    The number that matters to an operator staring at "17 waiting for review":
    if all 17 are unbound, none of them can be finished, and the queue is not
    work — it is a blocked queue with one action behind it.

    Counted in Python for the same reason as `unbound_only` in
    `list_documents`: the SQL form of this JSON comparison is a known trap.
    This is a work queue, not history, so the set is small by construction.
    """
    return sum(1 for d in db.query(IntakeDocument).all() if is_unbound(d))


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


# --- Reject -------------------------------------------------------------------

# Why an order was thrown away. Fixed codes rather than free text, because a note
# cannot be counted: "how many duplicates did we get this week?" is a question
# someone will ask, and the answer has to come out of the data. `duplicate` is
# first because it is the reason a WeCom order most often must not be created a
# second time.
REJECTION_REASONS: tuple[str, ...] = (
    "duplicate",
    "not_an_order",
    "wrong_customer",
    "unreadable",
    "other",
)


def reject_job(
    db: Session,
    job: IntakeJob,
    *,
    reason: str,
    note: str | None = None,
    actor: User | None = None,
) -> IntakeJob:
    """A human says this extraction must NOT become an order.

    The counterpart to `confirm_intake_review`, and the only other way out of
    `needs_review`. Without it the queue has no exit but "confirm", so an
    operator holding a duplicate has to either create the duplicate order or
    leave the row in the queue forever — and a queue you cannot clear stops
    being read, which is how a real order gets missed.

    `reason` must be one of `REJECTION_REASONS`. `note` carries the specifics
    and is REQUIRED for `other`, because "other" with nothing attached records
    a decision nobody can interpret later.

    Neither the document nor its extraction is touched. The parse is evidence
    of what the message said; the rejection is a decision *about* it, and the
    two must stay separable — that is what makes the extraction a usable audit
    trail. The decision is written to the document's meta and the audit log, so
    "why is this not an order?" is answerable afterwards.
    """
    if job.status != "needs_review":
        raise ValueError(
            f"Only jobs awaiting review can be rejected (status is {job.status})"
        )
    if reason not in REJECTION_REASONS:
        raise ValueError(
            f"Unknown rejection reason {reason!r}; expected one of "
            f"{', '.join(REJECTION_REASONS)}"
        )
    note = (note or "").strip()
    if reason == "other" and not note:
        raise ValueError("A note is required when the reason is 'other'")

    before = _job_out(job)
    job.status = "rejected"
    job.error = None
    job.finished_at = datetime.now(timezone.utc)
    db.flush()

    doc = db.get(IntakeDocument, job.document_id)
    if doc is not None:
        meta = dict(doc.document_meta or {})
        meta["review_rejection"] = {
            "reason": reason,
            "note": note or None,
            "by": getattr(actor, "id", None),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        doc.document_meta = meta
        flag_modified(doc, "document_meta")
        db.flush()

    log_audit(
        db, actor, "IntakeJob", job.id, "reject",
        before=before, after=_job_out(job),
        summary=(
            f"Intake job {job.id} rejected ({reason})"
            + (f": {note}" if note else "")
        ),
    )
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
    """The job's CURRENT extraction — its newest, not an arbitrary one.

    Every run appends a row rather than replacing one, because the raw AI output
    is an immutable audit trail. So a retried job holds several rows, and
    ordering by `id` picked whichever UUID happened to sort first.

    That is not a theoretical concern. Observed 2026-09-19: a job retried after
    the figure-only fix still served the OLD five-line read — `tbl-0.md`
    included, a line the new code cannot produce — which makes a re-processed
    document look as though nothing changed. Anyone re-running a document to get
    a better read is exactly the person this has to be right for.
    """
    return (
        db.query(IntakeExtraction)
        .filter(IntakeExtraction.job_id == job_id)
        # NULLs last: a row written before `created_at` existed must lose to a
        # real timestamp, not win it. Postgres sorts NULLs first on DESC.
        .order_by(IntakeExtraction.created_at.desc().nulls_last())
        .first()
    )
