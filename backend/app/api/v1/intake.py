"""INTAKE MODULE — owner: intake agent.

Endpoints (see docs/AGENT_CONTRACTS.md §5):
  POST /submit               R: ops/admin
      Accepts BOTH multipart/form-data (file + fields) and JSON (raw_text).
  GET  /documents            paged; filters: customer_id, source_type      R: ops/admin/finance
  GET  /documents/{id}       → IntakeDocument (+ job summary embedded)
  GET  /documents/{id}/file  → FileResponse original
  GET  /jobs                 paged; filters: status                             R: ops/admin
  GET  /jobs/{id}            → IntakeJob
  POST /jobs/{id}/retry      → re-queue processing                            R: ops/admin
  POST /jobs/{id}/confirm-review → human confirms a needs_review job, creates  R: ops/admin
                                  the draft Order (never auto-run by pipeline)
  GET  /extractions/{job_id} → raw AI output (immutable audit trail)
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.ai.pipeline import confirm_intake_review
from app.services.intake.service import (
    _doc_out,
    _extraction_out,
    _job_out,
    get_document,
    get_extraction_for_job,
    get_job,
    get_job_for_document,
    list_documents,
    list_jobs,
    retry_job,
    submit_intake,
)

router = APIRouter(prefix="/api/v1/intake", tags=["intake"])


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
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance", "admin")),
):
    page, page_size = clamp_page(page, page_size)
    pairs, total = list_documents(db, customer_id=customer_id, source_type=source_type)
    start = (page - 1) * page_size
    items = [_doc_out(d, j) for d, j in pairs[start : start + page_size]]
    return page_response(items, total, page, page_size)


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
    return _doc_out(doc, job)


@router.get("/documents/{document_id}/file", response_class=FileResponse)
def download_document_file(
    document_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "finance", "admin")),
):
    doc = get_document(db, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if not doc.file_path or not Path(doc.file_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found on disk")
    return FileResponse(
        path=doc.file_path,
        media_type="application/octet-stream",
        filename=doc.original_filename or "intake",
    )


# --- Jobs ---------------------------------------------------------------------

@router.get("/jobs", response_model=None)
def list_jobs_endpoint(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "admin")),
):
    page, page_size = clamp_page(page, page_size)
    jobs, total = list_jobs(db, status=status_filter)
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


@router.post("/jobs/{job_id}/confirm-review", response_model=None)
def confirm_review_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops", "admin")),
):
    """Human confirms a `needs_review` extraction → creates the draft order.

    The pipeline never calls this automatically. This is the explicit human
    "Confirm & submit" action for handwritten / low-confidence notes. Cancelled
    lines are excluded from the resulting order (the human already confirmed
    their removal during review), so a voided line never resurrects itself.
    """
    try:
        order = confirm_intake_review(db, job_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return {
        "job_id": job_id,
        "order_id": order.id,
        "order_number": order.order_number,
        "status": order.status,
    }


# --- Extractions (immutable audit trail) -------------------------------------

@router.get("/extractions/{job_id}", response_model=None)
def get_extraction_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "admin", "finance")),
):
    ext = get_extraction_for_job(db, job_id)
    if ext is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Extraction not found for job")
    return _extraction_out(ext)
