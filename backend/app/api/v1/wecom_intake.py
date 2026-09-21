"""WeCom intake endpoints — the ERP half of docs/WECOM_CONTRACTS.md §6.

  POST /api/v1/intake/wecom          R: service key OR ops/admin
  POST /api/v1/intake/wecom/reply    R: service key OR ops/admin
  GET  /api/v1/intake/wecom-messages R: service key OR ops/admin/finance

The Gateway calls these. All parsing stays in the ERP AI pipeline.
"""
from __future__ import annotations

from typing import Any

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
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_service_or_roles
from app.core.pagination import page_response
from app.models import Customer, CustomerContact, IntakeJob, User
from app.services.intake.service import (
    _doc_out,
    _job_out,
    backfill_triage_parks,
    customers_for,
)
from app.services.intake.triage import NOT_ORDER, PARKABLE_TIER, is_parkable
from app.services.intake.wecom_intake import ingest_wecom_message, list_wecom_documents

router = APIRouter(prefix="/api/v1/intake", tags=["intake-wecom"])


def _doc_with_job(db: Session, doc) -> dict:
    job = (
        db.query(IntakeJob)
        .filter(IntakeJob.document_id == doc.id)
        .order_by(IntakeJob.retry_count.desc(), IntakeJob.created_at.desc())
        .first()
    )
    out = _doc_out(doc, job, customer=customers_for(db, [doc]).get(doc.customer_id or ""))
    out["wecom"] = (doc.document_meta or {}).get("wecom")
    return out


def _msgid_from(payload: dict[str, Any], request: Request) -> str:
    """Contract §6: the idempotency key is `msgid` in the body, or the
    `Idempotency-Key` header. Accept either so a header-only request works."""
    msgid = (payload.get("msgid") or "").strip()
    if msgid:
        return msgid
    return (request.headers.get("Idempotency-Key") or "").strip()


@router.post("/wecom", status_code=status.HTTP_201_CREATED)
def receive_wecom_message(
    payload: dict[str, Any],
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User | None = Depends(require_service_or_roles("ops")),
):
    """Handoff endpoint for the WeCom Gateway.

    Idempotent on `msgid`: a replay returns 200 with the original ids and
    `duplicate: true` instead of creating a second order.
    """
    msgid = _msgid_from(payload, request)
    if not msgid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "msgid is required")
    payload["msgid"] = msgid

    try:
        doc, job, duplicate = ingest_wecom_message(
            db, payload, actor=actor, background_tasks=background_tasks
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    db.commit()
    if duplicate:
        # §6: a replay is 200 + duplicate=true, not a fresh 201.
        response.status_code = status.HTTP_200_OK
    body = {
        "document_id": doc.id,
        "job_id": job.id if job else None,
        "customer_id": doc.customer_id,
        "status": job.status if job else "queued",
        "duplicate": duplicate,
    }
    return body


@router.post("/wecom/reply", status_code=status.HTTP_201_CREATED)
def receive_wecom_reply(
    payload: dict[str, Any],
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User | None = Depends(require_service_or_roles("ops")),
):
    """Customer reply loop (Phase 6). `reply_to_msgid` links it to the original.

    Falls back to a normal ingest when the parent cannot be found, so a reply is
    never dropped.
    """
    msgid = _msgid_from(payload, request)
    if not msgid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "msgid is required")
    payload["msgid"] = msgid

    try:
        doc, job, duplicate = ingest_wecom_message(
            db, payload, actor=actor, background_tasks=background_tasks
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    db.commit()
    if duplicate:
        response.status_code = status.HTTP_200_OK
    return {
        "document_id": doc.id,
        "job_id": job.id if job else None,
        "customer_id": doc.customer_id,
        "status": job.status if job else "queued",
        "duplicate": duplicate,
        "reply_to_msgid": payload.get("reply_to_msgid"),
        "parent_document_id": (doc.document_meta or {}).get("wecom", {}).get("parent_document_id"),
    }


@router.get("/wecom/lookup-customer")
def lookup_customer(
    code: str | None = Query(default=None, description="Exact Customer.code"),
    phone: str | None = Query(default=None, description="Customer or contact phone"),
    db: Session = Depends(get_db),
    _: User | None = Depends(require_service_or_roles("ops", "finance")),
):
    """Resolve an ERP customer for the Gateway's auto-bind cascade (§5).

    `code` is an exact match (used by the `[CUST:CODE]` remark rule);
    `phone` matches Customer.contact_phone or any CustomerContact.phone.
    """
    if not code and not phone:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "code or phone is required")

    customer = None
    if code:
        customer = db.query(Customer).filter(Customer.code == code).first()
    if customer is None and phone:
        customer = db.query(Customer).filter(Customer.contact_phone == phone).first()
    if customer is None and phone:
        link = db.query(CustomerContact).filter(CustomerContact.phone == phone).first()
        if link is not None:
            customer = db.get(Customer, link.customer_id)

    if customer is None:
        return {"found": False, "customer": None}
    return {
        "found": True,
        "customer": {
            "id": customer.id,
            "code": customer.code,
            "name_en": customer.name_en,
            "name_zh": customer.name_zh,
            "contact_phone": customer.contact_phone,
        },
    }


@router.get("/wecom/triage-report")
def triage_report(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User | None = Depends(require_service_or_roles("ops", "finance")),
):
    """What Gate 1 does with the messages it has already judged.

    Two readings of the same data, and they must not be confused:

    - `would_park` / `would_park_count` — the messages the park rule selects.
      This is the list to eyeball: if any of those are real orders, the rules
      need fixing, not the mode.
    - `kept_visible_count` — judged `not_order`, but left in the inbox anyway,
      because the verdict came from the heuristic tier. Not an oversight; see
      `is_parkable`.

    `would_park_count` counts the same population the list is drawn from, and
    the population is defined by `is_parkable` — the same function the ingest
    path and the backfill use. It used to count every `not_order`, which made
    the report disagree with the gate it exists to describe.
    """
    docs, total = list_wecom_documents(db)
    counts: dict[str, int] = {}
    would_park: list[dict[str, Any]] = []
    unclassified = 0
    actually_parked = 0
    parkable_total = 0
    kept_visible = 0

    for d in docs:
        block = (d.document_meta or {}).get("wecom") or {}
        verdict = block.get("triage")
        if not verdict:
            unclassified += 1
            continue
        decision = verdict.get("decision") or "unknown"
        counts[decision] = counts.get(decision, 0) + 1
        # A promoted (human-overridden) message is back in the queue, so it is
        # no longer something enforcement has hidden. Counting it here would
        # disagree with `GET /intake/review-count`, which counts parked *jobs*
        # and therefore drops to 0 the moment the job is promoted.
        if verdict.get("parked") and not verdict.get("overridden"):
            actually_parked += 1
        if not is_parkable(verdict):
            # A heuristic `not_order` stays visible by design. Report the count
            # so the gap between "not an order" and "hidden" is visible rather
            # than something the reader has to reconcile themselves.
            if decision == NOT_ORDER:
                kept_visible += 1
            continue
        parkable_total += 1
        if len(would_park) < limit:
            would_park.append(
                {
                    "document_id": d.id,
                    "msgid": block.get("msgid"),
                    "msgtype": block.get("msgtype"),
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    "excerpt": verdict.get("excerpt"),
                    "score": verdict.get("score"),
                    "tier": verdict.get("tier"),
                    "reasons": verdict.get("reasons"),
                    "parked": bool(verdict.get("parked")),
                    "overridden": bool(verdict.get("overridden")),
                }
            )

    return {
        "mode": (settings.intake_triage_mode or "shadow").lower(),
        "parkable_tier": PARKABLE_TIER,
        "total_wecom_documents": total,
        "classified": total - unclassified,
        "unclassified": unclassified,
        "counts": counts,
        "would_park_count": parkable_total,
        "would_park": would_park,
        # Judged not an order, deliberately not hidden. If this is ever
        # non-zero and the rows are junk, the answer is not to widen the rule
        # blindly -- see the counterexample in `triage.is_parkable`.
        "kept_visible_count": kept_visible,
        # What enforcement has currently removed from the inbox. In shadow mode
        # this is 0 and `would_park_count` is the forecast. Promoted messages
        # are excluded — they are visible again.
        "actually_parked_count": actually_parked,
    }


@router.post("/wecom/triage-backfill")
def triage_backfill(
    apply: bool = Query(
        default=False,
        description="Set true to actually park. The default is a dry run.",
    ),
    db: Session = Depends(get_db),
    actor: User | None = Depends(require_service_or_roles("ops")),
):
    """Park the chatter already sitting in the inbox, judged by its own verdict.

    Gate 1 only governs messages that arrive *after* it is switched on, so
    turning enforcement on does not clean up what the inbox already holds — the
    operator keeps looking at the same junk and concludes the filter still does
    not work. This applies the live rule to the WeCom documents that already
    carry a verdict.

    **Dry run by default**: call it, read `would_park`, then call it again with
    `apply=true`. Parking hides rows, so acting on a list nobody previewed is
    how a real order disappears. Nothing already confirmed into an order,
    rejected by a human, or overridden by a human is touched, and every park is
    reversible with `POST /intake/jobs/{id}/promote`.
    """
    return backfill_triage_parks(db, apply=apply, actor=actor)


@router.get("/wecom-messages")
def list_wecom_messages(
    customer_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User | None = Depends(require_service_or_roles("ops", "finance")),
):
    docs, total = list_wecom_documents(db, customer_id=customer_id)
    start = (page - 1) * page_size
    items = [_doc_with_job(db, d) for d in docs[start : start + page_size]]
    return page_response(items, total, page, page_size)
