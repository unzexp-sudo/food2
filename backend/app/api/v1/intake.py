"""INTAKE MODULE — owner: intake agent.

Endpoints (see docs/AGENT_CONTRACTS.md §5):
  POST /submit               R: ops/admin
      Accepts BOTH multipart/form-data (file + fields) and JSON (raw_text).
  GET  /documents            paged; filters: customer_id, source_type,     R: ops/admin/finance
                                   status, pending_first
  GET  /review-count         → {"pending_review": n} for the nav badge     R: ops/admin/finance
  GET  /documents/{id}       → IntakeDocument (+ job summary embedded)
  GET  /documents/{id}/file  → FileResponse original
  GET  /jobs                 paged; filters: status                             R: ops/admin
  GET  /jobs/{id}            → IntakeJob
  POST /jobs/{id}/retry      → re-queue processing                            R: ops/admin
  POST /jobs/{id}/promote    → human un-parks a "parked" (not-an-order) job   R: ops/admin
  POST /jobs/{id}/confirm-review → human confirms a needs_review job, creates  R: ops/admin
                                  the draft Order (never auto-run by pipeline).
                                  Optional body carries the reviewer's line
                                  corrections (qty / unit / product / added /
                                  removed); the stored extraction is not touched.
  POST /jobs/{id}/reject     → human refuses a needs_review job, with a        R: ops/admin
                                  reason. Creates nothing and keeps the reason.
  GET  /extractions/{job_id} → raw AI output (immutable audit trail), plus the
                                  source it was read from, for the review screen
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.ai.pipeline import confirm_intake_review
from app.services.intake.service import (
    REJECTION_REASONS,
    _doc_out,
    _extraction_out,
    _job_out,
    customers_for,
    get_document,
    get_extraction_for_job,
    get_job,
    get_job_for_document,
    count_parked,
    count_unbound,
    count_pending_review,
    list_documents,
    list_jobs,
    promote_parked_job,
    reject_job,
    retry_job,
    submit_intake,
)

router = APIRouter(prefix="/api/v1/intake", tags=["intake"])


# --- Review actions -----------------------------------------------------------
#
# The two ways a human can settle a `needs_review` job: correct it into an order,
# or refuse it with a reason. Both are optional-bodied so the calls that already
# existed keep working untouched.

class RejectRequest(BaseModel):
    """Why a reviewer threw this extraction away.

    `reason` is validated against `REJECTION_REASONS` in the service rather than
    as a `Literal` here, so an unknown code comes back as the same plain 400
    every other refusal on this router produces. A `Literal` would answer 422
    with pydantic's list of dicts, which the review drawer cannot render as a
    sentence next to the button.
    """

    reason: str = Field(..., description="One of: " + ", ".join(REJECTION_REASONS))
    note: str | None = Field(
        default=None, description="Free text. Required when reason='other'."
    )


class ReviewLineEdit(BaseModel):
    """A correction to one extracted line, addressed by its 1-based position.

    Every field is optional and only the ones PRESENT are applied, so the client
    sends the change and nothing else. A whole-line payload would let a stale
    drawer silently revert a field the operator never looked at — and the whole
    point of this screen is that a person checked each value.
    """

    line_no: int = Field(..., ge=1)
    quantity: float | None = None
    unit: str | None = None
    product_name: str | None = None
    cancelled: bool | None = None


class ReviewAddedLine(BaseModel):
    """A line the extractor missed. `product_name` is required — a line with no
    product is not a line, and a quantity is checked in the service so the
    refusal reads as a sentence."""

    product_name: str = Field(..., min_length=1)
    quantity: float | None = None
    unit: str | None = None


class ReviewConfirmRequest(BaseModel):
    """Corrections to apply before the draft order is created.

    All three lists default to empty, so `POST .../confirm-review` with no body
    behaves exactly as it did before this existed.
    """

    lines: list[ReviewLineEdit] = Field(default_factory=list)
    added_lines: list[ReviewAddedLine] = Field(default_factory=list)


# --- Submit -------------------------------------------------------------------

@router.post("/submit", response_model=None, status_code=status.HTTP_201_CREATED)
async def submit(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops", "admin")),
):
    """Submit an intake document for AI processing.

    Accepts either:
    - JSON body: {customer_id?, delivery_date?, source_type, raw_text?}
    - multipart/form-data: customer_id?, delivery_date?, source_type, raw_text?, file?

    Returns {document_id, job_id} immediately; processing runs in background.
    """
    content_type = (request.headers.get("content-type") or "").lower()
    cid = dd = st = rt = None
    file_bytes = None
    filename = None

    if content_type.startswith("application/json"):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON body")
        cid = body.get("customer_id")
        dd_raw = body.get("delivery_date")
        dd = date.fromisoformat(dd_raw) if dd_raw else None
        st = body.get("source_type")
        rt = body.get("raw_text")
    elif content_type.startswith("multipart/form-data") or content_type.startswith(
        "application/x-www-form-urlencoded"
    ):
        form = await request.form()
        cid = form.get("customer_id")
        dd_raw = form.get("delivery_date")
        dd = date.fromisoformat(dd_raw) if dd_raw else None
        st = form.get("source_type")
        rt = form.get("raw_text")
        upload = form.get("file")
        if upload is not None and hasattr(upload, "read"):
            file_bytes = await upload.read()
            filename = upload.filename or "upload"
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Content-Type must be application/json or multipart/form-data",
        )

    if not st:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "source_type is required")

    if file_bytes is None and not rt:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Either raw_text or file must be provided",
        )

    try:
        doc, job = submit_intake(
            db,
            customer_id=cid,
            delivery_date=dd,
            source_type=st,
            raw_text=rt,
            file_bytes=file_bytes,
            filename=filename,
            actor=actor,
            background_tasks=background_tasks,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return {"document_id": doc.id, "job_id": job.id}


# --- Documents ----------------------------------------------------------------

@router.get("/documents", response_model=None)
def list_documents_endpoint(
    customer_id: Optional[str] = Query(default=None),
    source_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    pending_first: bool = Query(default=False),
    # Parked messages are hidden by default; pass true (or status=parked) to
    # audit what Gate 1 threw away.
    include_parked: bool = Query(default=False),
    # Held documents: no customer binding, so no order can be created from them
    # until someone binds the conversation. The inbox offers this as a filter.
    unbound_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance", "admin")),
):
    page, page_size = clamp_page(page, page_size)
    pairs, total = list_documents(
        db,
        customer_id=customer_id,
        source_type=source_type,
        status=status,
        pending_first=pending_first,
        include_parked=include_parked,
        unbound_only=unbound_only,
    )
    start = (page - 1) * page_size
    page_pairs = pairs[start : start + page_size]
    customers = customers_for(db, [d for d, _ in page_pairs])
    items = [
        _doc_out(d, j, customer=customers.get(d.customer_id or ""))
        for d, j in page_pairs
    ]
    return page_response(items, total, page, page_size)


@router.get("/review-count", response_model=None)
def pending_review_count_endpoint(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance", "admin")),
):
    """How many orders are parked waiting for a human (nav badge / "unread").

    `parked` is returned alongside so the UI can show what Gate 1 discarded —
    a wrong park is otherwise invisible, and an operator who cannot see the
    discarded pile cannot check it.

    `unbound` is the subset that cannot be finished at all yet, because their
    conversation has no customer. Without it the banner says "17 waiting for
    review" and hides the fact that the queue is blocked rather than busy.

    Declared before `/documents/{id}` so the literal path wins over the
    path-parameter route.
    """
    return {
        "pending_review": count_pending_review(db),
        "parked": count_parked(db),
        "unbound": count_unbound(db),
    }


@router.get("/documents/{document_id}", response_model=None)
def get_document_endpoint(
    document_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance", "admin")),
):
    doc = get_document(db, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    job = get_job_for_document(db, document_id)
    customers = customers_for(db, [doc])
    return _doc_out(doc, job, customer=customers.get(doc.customer_id or ""))


@router.get("/documents/{document_id}/company-proposal", response_model=None)
def company_proposal_endpoint(
    document_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "admin")),
):
    """The extraction's *proposal* for who this document belongs to.

    Read-only by design: `POST /identity/bind` is the only thing that attaches
    a customer, and it is always a human doing it. Returns `null` when nothing
    was proposed (no text, or the extractor found nothing) so the bind screen
    can simply not show the panel.
    """
    doc = get_document(db, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return (doc.document_meta or {}).get("company_proposal")


# The browser needs the REAL type to render an original inline: an `<img>` or a
# PDF viewer handed `application/octet-stream` downloads the file instead of
# showing it, and showing it is the entire job of the review screen's source
# pane. The download path keeps the opaque type on purpose — a download should
# not be re-interpreted by whatever ends up opening it.
_INLINE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _inline_media_type(path: str | None) -> str:
    return _INLINE_MEDIA_TYPES.get(
        (Path(path or "").suffix or "").lower(), "application/octet-stream"
    )


def _content_disposition(name: str, *, inline: bool) -> str:
    """`inline; filename=…`, matching what FileResponse would have sent.

    Built by hand because the bytes are now served from the row rather than
    from a file, so FileResponse — which owns this header — is no longer in the
    path. Same two-branch shape it uses: a plain ASCII name is quoted, anything
    else is RFC 5987 percent-encoded, which is what keeps a Chinese filename
    from arriving as mojibake.
    """
    kind = "inline" if inline else "attachment"
    encoded = quote(name)
    if encoded != name:
        return f"{kind}; filename*=utf-8''{encoded}"
    return f'{kind}; filename="{name}"'


@router.get("/documents/{document_id}/file", response_class=FileResponse)
def download_document_file(
    document_id: str,
    inline: bool = Query(
        default=False,
        description=(
            "Serve for display instead of download: real media type plus an "
            "inline disposition."
        ),
    ),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance", "admin")),
):
    """The original exactly as it was stored.

    `inline=1` is what the review screen uses to put the source next to the
    parse. The default is unchanged — a download with an opaque content type —
    so every existing caller behaves as before.

    The bytes come from the row, and the disk is only a fallback. Serving from
    `doc.file_path` is what produced "File not found on disk" for every document
    uploaded before the last redeploy — the path pointed into a container that
    no longer existed, so the preview was blank and the failure looked like a
    frontend bug rather than a storage one. The fallback stays for rows written
    before this column existed, and for local development.
    """
    doc = get_document(db, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    name = doc.original_filename or "intake"
    media_type = (
        _inline_media_type(doc.original_filename or doc.file_path)
        if inline
        else "application/octet-stream"
    )
    disposition = _content_disposition(name, inline=inline)

    if doc.file_data:
        return Response(
            content=doc.file_data,
            media_type=media_type,
            headers={"Content-Disposition": disposition},
        )

    if doc.file_path and Path(doc.file_path).exists():
        return FileResponse(
            path=doc.file_path,
            media_type=media_type,
            filename=name,
            content_disposition_type="inline" if inline else "attachment",
        )

    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        "The original for this document is no longer available",
    )


# --- Jobs ---------------------------------------------------------------------

@router.get("/jobs", response_model=None)
def list_jobs_endpoint(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    include_parked: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "admin")),
):
    page, page_size = clamp_page(page, page_size)
    jobs, total = list_jobs(db, status=status_filter, include_parked=include_parked)
    start = (page - 1) * page_size
    items = [_job_out(j) for j in jobs[start : start + page_size]]
    return page_response(items, total, page, page_size)


@router.get("/jobs/{job_id}", response_model=None)
def get_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "admin")),
):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return _job_out(job)


@router.post("/jobs/{job_id}/retry", response_model=None)
def retry_job_endpoint(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops", "admin")),
):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    job = retry_job(db, job, actor=actor, background_tasks=background_tasks)
    db.commit()
    return {"job_id": job.id, "status": job.status, "retry_count": job.retry_count}


@router.post("/jobs/{job_id}/promote", response_model=None)
def promote_parked_job_endpoint(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops", "admin")),
):
    """A human says a parked message really was an order — process it.

    Gate 1 is a classifier and classifiers are wrong sometimes. Because the
    original file was stored when the message was parked, this parses it
    exactly as it would have the first time; nothing is lost by the detour.
    """
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    try:
        job = promote_parked_job(db, job, actor=actor, background_tasks=background_tasks)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return {"job_id": job.id, "status": job.status}


@router.post("/jobs/{job_id}/confirm-review", response_model=None)
def confirm_review_endpoint(
    job_id: str,
    payload: ReviewConfirmRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops", "admin")),
):
    """Human confirms a `needs_review` extraction → creates the draft order.

    The pipeline never calls this automatically. This is the explicit human
    "Confirm & submit" action for handwritten / low-confidence notes. Cancelled
    lines are excluded from the resulting order (the human already confirmed
    their removal during review), so a voided line never resurrects itself.

    The optional body carries the reviewer's CORRECTIONS — a quantity the OCR
    misread, a unit, a product matched to the wrong SKU, or a line the extractor
    missed entirely. They are applied to the ORDER and never to the stored
    extraction: the parse stays the record of what the machine read, so the gap
    between the two is what makes "the AI got it wrong" measurable instead of a
    matter of opinion.
    """
    try:
        order = confirm_intake_review(
            db,
            job_id,
            actor=actor,
            # A plain dict, so the pipeline never has to import this module —
            # and so the service can be driven directly from a test.
            edits=payload.model_dump() if payload is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return {
        "job_id": job_id,
        "order_id": order.id,
        "order_number": order.order_number,
        "status": order.status,
    }


@router.post("/jobs/{job_id}/reject", response_model=None)
def reject_review_endpoint(
    job_id: str,
    payload: RejectRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops", "admin")),
):
    """Human refuses a `needs_review` extraction, with a reason.

    This is the exit the queue was missing. Without it the only way out of
    `needs_review` was to confirm, so a duplicate had to either become a second
    order or sit in the queue forever — and a queue you cannot clear stops
    being read, which is how a real order gets missed.

    No order is created and the document is left exactly as it was. The reason
    is written to the document's meta and the audit log, so "why is this not an
    order?" is answerable months later.
    """
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    try:
        job = reject_job(
            db, job, reason=payload.reason, note=payload.note, actor=actor
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return {
        "job_id": job.id,
        "status": job.status,
        "reason": payload.reason,
        "note": (payload.note or "").strip() or None,
    }


# --- Extractions (immutable audit trail) -------------------------------------

@router.get("/extractions/{job_id}", response_model=None)
def get_extraction_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "admin", "finance")),
):
    """The raw AI output for a job, plus the source it was read from.

    The source rides along because the review screen has to show both at once
    and this is the request it already makes — the inbox's list payload stays
    lean, and a document's full text never lands on a 20-row page.
    """
    ext = get_extraction_for_job(db, job_id)
    if ext is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Extraction not found for job")
    job = get_job(db, job_id)
    document = get_document(db, job.document_id) if job else None
    return _extraction_out(ext, document)
