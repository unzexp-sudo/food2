"""AI intake pipeline — background job processor.

`process_intake_job(db, job_id)` runs the full pipeline:
  1. set status "processing"
  2. classify (derive doc type from source_type)
  3. extract raw lines
  4. normalize each line to the §4 shape
  5. match SKUs (app/ai/matching.py)
  6. score (per-line + overall weighted by quantity)
  7. persist IntakeExtraction (immutable raw_output)
  8. create draft Order + OrderLines
  9. emit "order.draft_created" event
 10. set job status "completed" (or "failed" on exception — never raises)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.ai.adapters import FORM_TYPES, get_extractor
from app.ai.matching import match_product, match_unit
from app.core.audit import log_audit
from app.core.config import settings
from app.core.events import emit
from app.core.numbers import next_number
from app.models import (
    CustomerProductAlias,
    IntakeDocument,
    IntakeExtraction,
    IntakeJob,
    Order,
    OrderLine,
    Product,
    Unit,
)
from app.services.identity import assert_document_bound, is_unbound
from app.services.orders.orders import prefill_delivery_from_customer


def _tomorrow() -> date:
    return date.today() + timedelta(days=1)


def _normalize_lines(
    db: Session,
    *,
    raw_lines: list,
    customer_id: str | None,
) -> list[dict[str, Any]]:
    """Normalize raw extracted lines into the §4 shape with SKU + unit matches."""
    # Pre-load catalog and aliases once (avoid N+1 per line)
    products: list[Product] = db.query(Product).filter(Product.is_active.is_(True)).all()
    aliases: list[CustomerProductAlias] = db.query(CustomerProductAlias).all()
    units: list[Unit] = db.query(Unit).all()

    normalized: list[dict[str, Any]] = []

    for idx, rl in enumerate(raw_lines, start=1):
        pmatch = match_product(
            db,
            raw_name=rl.product_name,
            customer_id=customer_id,
            products=products,
            aliases=aliases,
        )

        # Resolve the matched product object (for default unit fallback)
        matched_prod = next((p for p in products if p.id == pmatch.product_id), None) if pmatch.product_id else None

        umatch = match_unit(
            db,
            raw_unit=rl.unit,
            units=units,
            fallback_product=matched_prod,
        )

        quantity = rl.quantity if rl.quantity is not None else 0.0
        display_name = pmatch.product_name or rl.product_name

        normalized.append({
            "line_no": idx,
            "raw_text": f"{rl.product_name}{(f' {quantity}{rl.unit}' if rl.unit and rl.quantity else '')}".strip(),
            "product_id": pmatch.product_id,
            "matched_product_id": pmatch.product_id,
            "matched_product_name": pmatch.product_name,
            "product_display": display_name,
            "product_name": rl.product_name,
            "quantity": quantity,
            "unit": rl.unit,
            "unit_id": umatch.unit_id,
            "unit_code": umatch.unit_code,
            "confidence": pmatch.confidence,
            "match_method": pmatch.match_method,
            "notes": rl.notes,
            # Sub-customer / menu-code breakdown rows attached by the
            # structured supplier-order parser. Empty for legacy line input.
            "breakdowns": list(getattr(rl, "breakdowns", None) or []),
            # Header metadata (supplier, task count, print time) rides along
            # on every line; we pull it off the first line below.
            "_header": getattr(rl, "header", None) if idx == 1 else None,
        })

    return normalized


def _overall_confidence(lines: list[dict[str, Any]]) -> float:
    """Quantity-weighted mean of line confidences."""
    total_qty = 0.0
    weighted = 0.0
    for ln in lines:
        q = ln.get("quantity") or 0.0
        c = ln.get("confidence") or 0.0
        # Guard against zero-quantity lines dominating
        w = q if q > 0 else 1.0
        weighted += c * w
        total_qty += w
    if total_qty == 0:
        return 0.0
    return round(weighted / total_qty, 4)


def process_intake_job(db: Session, job_id: str) -> None:
    """Background pipeline. Never raises — on exception marks job failed.

    `db` is a fresh session owned by the caller (the background-task wrapper).
    """
    job = db.get(IntakeJob, job_id)
    if job is None:
        return

    document = db.get(IntakeDocument, job.document_id)
    if document is None:
        job.status = "failed"
        job.error = "IntakeDocument not found"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    try:
        # Step 1: processing
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        job.error = None
        db.flush()

        # Step 2 + 3: classify + extract raw lines via the adapter
        extractor = get_extractor()
        extraction = extractor.extract(
            source_type=document.source_type,
            raw_text=document.document_meta.get("raw_text") if document.document_meta else None,
            file_path=document.file_path,
            original_filename=document.original_filename,
        )

        # QA gate result. For the mock provider this is always False; for the
        # real OCR providers (aliyun_qwen) it is set by apply_review_gate and
        # encodes the "0 assumptions" rule: a recognized handwritten note is
        # NEVER auto-submitted — a human must confirm it first.
        #
        # `intake_require_human_review` widens that rule from "risky documents"
        # to EVERY document. Business rule: a wrong order shipping is far more
        # costly than the few seconds a human spends confirming, so extractor
        # confidence never buys an auto-approval.
        requires_review = bool(
            getattr(extraction, "requires_human_review", False)
        ) or settings.intake_require_human_review
        # An unbound conversation is always held, on top of the review rules
        # above. Otherwise a confident extraction from an unknown chat would
        # sail straight into a draft order with no customer at all — the exact
        # hole this work closes (docs/IDENTITY_IMPLEMENTATION_SPEC.md §2.3).
        if is_unbound(document):
            requires_review = True

        # Step 4 + 5: normalize + match SKUs
        lines = _normalize_lines(
            db,
            raw_lines=extraction.lines,
            customer_id=document.customer_id,
        )

        # Step 6: score
        overall = _overall_confidence(lines)
        parser_notes = extraction.parser_notes
        if not parser_notes:
            parser_notes = f"Extracted {len(lines)} lines"

        # Build the AI output schema per EXECUTIVE_SUMMARY.md, enriched with the
        # QA fields so the review UI has everything it needs in one payload.
        raw_lines = extraction.lines

        # Attach per-line QA (review reasons + cancellation) from the extractor.
        for i, ln in enumerate(lines):
            rl = raw_lines[i] if i < len(raw_lines) else None
            if rl is not None:
                ln["review_reasons"] = getattr(rl, "review_reasons", None) or []
                ln["cancelled"] = bool(getattr(rl, "cancelled", False))
            else:
                ln.setdefault("review_reasons", [])
                ln.setdefault("cancelled", False)

        raw_output: dict[str, Any] = {
            "customer_id": document.customer_id,
            "delivery_date": (document.document_meta or {}).get("delivery_date"),
            "doc_type": extraction.doc_type,
            "lines": [
                {
                    "raw_text": ln["raw_text"],
                    # The name as the customer wrote it. Stored so a reviewer's
                    # correction can be re-matched from the original wording
                    # instead of from a name that has already been normalised
                    # once — re-matching a match drifts.
                    "product_name": ln["product_name"],
                    "matched_product_id": ln["matched_product_id"],
                    "matched_product_name": ln["matched_product_name"],
                    "quantity": ln["quantity"],
                    "unit": ln["unit"],
                    "unit_code": ln["unit_code"],
                    "unit_id": ln["unit_id"],
                    "confidence": ln["confidence"],
                    "match_method": ln["match_method"],
                    "breakdowns": ln.get("breakdowns", []),
                    # QA: per-line review reasons + cancellation flag so the UI
                    # can highlight exactly the weak cells and confirm removals.
                    "review_reasons": ln.get("review_reasons", []),
                    "cancelled": ln.get("cancelled", False),
                }
                for ln in lines
            ],
            "overall_confidence": overall,
            "parser_notes": parser_notes,
            # --- QA summary (consumed by the review screen) -------------------
            "form_type": extraction.form_type or None,
            "form_type_confidence": extraction.form_type_confidence,
            "ocr_overall_confidence": extraction.overall_confidence,
            "requires_human_review": requires_review,
            "is_handwritten": extraction.form_type in FORM_TYPES,
            "cancelled_lines": extraction.cancelled_lines or [],
            "image_path": extraction.image_path,
        }

        # If the structured supplier-order parser produced header metadata
        # (supplier, task count, print time, estimated total), surface it at
        # the top of the extraction payload so ERP staff can see the source
        # document context without a separate lookup.
        first_header = next(
            (ln.get("_header") for ln in lines if ln.get("_header")), None
        )
        if first_header:
            raw_output["header"] = first_header

        # Step 7: persist IntakeExtraction (immutable)
        extraction_row = IntakeExtraction(
            job_id=job.id,
            raw_output=raw_output,
            overall_confidence=overall,
            parser_notes=parser_notes,
        )
        db.add(extraction_row)
        db.flush()

        # Step 8 (conditional): if the QA gate flagged this document, DO NOT
        # create an order. Park the job in "needs_review" so a human confirms
        # the draft before any Order is created. This is the "OCR proposes,
        # human disposes" boundary — the system never makes the call itself.
        if requires_review:
            job.status = "needs_review"
            job.error = None
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            emit(
                "intake.needs_review",
                db=db,
                job=job,
                document=document,
                extraction=extraction_row,
            )
            db.commit()
            return

        # Step 8: create draft Order + OrderLines (only reached when the QA gate
        # did NOT flag the document for human review).
        order = _create_draft_order(db, document, job, lines, overall, parser_notes)
        job.draft_order_id = order.id

        log_audit(
            db,
            None,
            "Order",
            order.id,
            "create",
            before=None,
            after={
                "order_number": order.order_number,
                "customer_id": order.customer_id,
                "status": order.status,
                "delivery_date": order.delivery_date.isoformat(),
                "source_type": order.source_type,
                "intake_document_id": document.id,
                "overall_confidence": overall,
                "line_count": len(lines),
            },
            summary=f"AI intake created draft order {order.order_number} (confidence={overall})",
        )
        log_audit(
            db,
            None,
            "IntakeJob",
            job.id,
            "process",
            before=None,
            after={"status": "completed", "draft_order_id": order.id, "line_count": len(lines)},
            summary=f"Intake job {job.id} completed → order {order.order_number}",
        )

        # Step 9: emit event (orders module auto-confirm handler may run)
        # Commit the draft order first so the handler sees it.
        db.commit()

        emit("order.draft_created", db=db, order=order)

        # Step 10: completed
        job = db.get(IntakeJob, job_id) or job
        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()

    except Exception as exc:
        # Roll back any uncommitted changes, then mark the job failed.
        db.rollback()
        job = db.get(IntakeJob, job_id) or job
        job.status = "failed"
        job.error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        # Outbound WeCom notification (docs/WECOM_CONTRACTS.md §11).
        emit("intake.job_failed", db=db, job=job, document=document)


def _create_draft_order(
    db: Session,
    document: IntakeDocument,
    job: IntakeJob,
    lines: list[dict[str, Any]],
    overall: float,
    parser_notes: str | None,
    *,
    confirmed_by: str | None = None,
) -> Order:
    """Build and persist the draft Order + OrderLines. Does NOT commit.

    Shared by the automatic pipeline path (auto-run for non-flagged documents)
    and the human-confirm path (`confirm_intake_review`). Centralizing it keeps
    the two code paths identical so a confirmed review produces the same order a
    clean auto-extraction would.
    """
    delivery_date_str = (document.document_meta or {}).get("delivery_date")
    if delivery_date_str:
        try:
            delivery_date = date.fromisoformat(delivery_date_str)
        except (ValueError, TypeError):
            delivery_date = _tomorrow()
    else:
        delivery_date = _tomorrow()

    order_number = next_number(db, Order, "order_number", "ORD")
    order = Order(
        order_number=order_number,
        customer_id=document.customer_id,
        status="draft",
        delivery_date=delivery_date,
        source_type=document.source_type,
        intake_document_id=document.id,
        overall_confidence=overall,
        created_by=confirmed_by or document.uploaded_by,
    )
    db.add(order)
    db.flush()

    # Pre-fill the delivery fields from the customer — a proposal, not a fact.
    # It is shown to the human and still has to be confirmed before the order
    # can be confirmed.
    prefill_delivery_from_customer(db, order)

    for ln in lines:
        ol = OrderLine(
            order_id=order.id,
            line_no=ln["line_no"],
            raw_text=ln["raw_text"],
            product_id=ln["product_id"],
            product_display=ln["product_display"],
            quantity=ln["quantity"],
            unit_id=ln["unit_id"],
            unit_price=None,  # set at confirm time
            confidence=ln["confidence"],
            match_method=ln["match_method"],
        )
        db.add(ol)
    db.flush()
    return order


def _apply_review_edits(
    raw_lines: list[dict[str, Any]],
    edits: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fold a reviewer's corrections into an extraction's line list.

    Returns `(proposal, summary)`. A proposal line is
    `{line_no, product_name, quantity, unit, raw_text, edited, match}` — the
    shape *before* SKU matching. `edited` is False for a line the reviewer left
    alone, and `match` then carries the ORIGINAL match so an untouched line goes
    to the order with the confidence the extractor actually reported, rather
    than a number re-derived from a round trip.

    Corrections are addressed by 1-based `line_no`, matching the numbers the
    review screen shows. An edit naming a line that is not there is a hard error
    rather than a silent no-op: it means the drawer and the server disagree
    about the line set, and applying the rest would produce an order nobody
    actually reviewed.

    A quantity of zero is refused for the same reason. `0` is not a small order,
    it is a missing number — and it is exactly what a mis-tapped field produces.
    The reviewer has two honest ways out, and the message names both.
    """
    edits = edits or {}
    line_edits = list(edits.get("lines") or [])
    added = list(edits.get("added_lines") or [])

    by_no: dict[int, dict[str, Any]] = {}
    for e in line_edits:
        no = e.get("line_no")
        if not isinstance(no, int) or isinstance(no, bool):
            raise ValueError("Each line correction needs an integer line_no")
        if no in by_no:
            raise ValueError(f"Line {no} was corrected twice")
        if no < 1 or no > len(raw_lines):
            raise ValueError(
                f"Line {no} is not in this extraction — it has "
                f"{len(raw_lines)} line(s). Reopen the review and try again."
            )
        by_no[no] = e

    proposal: list[dict[str, Any]] = []
    corrected: list[dict[str, Any]] = []
    # Kept apart on purpose. "The customer struck this line out" and "the
    # reviewer dropped it" are different events with different meanings, and an
    # audit trail that merges them cannot answer either question.
    note_cancelled: list[int] = []
    reviewer_removed: list[int] = []

    for i, rl in enumerate(raw_lines, start=1):
        edit = by_no.get(i) or {}
        cancelled_on_note = bool(rl.get("cancelled"))
        if bool(edit.get("cancelled", cancelled_on_note)):
            (note_cancelled if cancelled_on_note else reviewer_removed).append(i)
            continue

        # `product_name` is the name as the customer wrote it, which is what
        # re-matching needs. Rows written before it was stored fall back to the
        # matched name — re-matching a name that already matched is stable.
        source_name = str(
            rl.get("product_name")
            or rl.get("matched_product_name")
            or rl.get("raw_text")
            or ""
        ).strip()

        new_name = edit.get("product_name")
        name = source_name if new_name is None else str(new_name).strip()
        new_qty = edit.get("quantity")
        qty = float(rl.get("quantity") or 0.0) if new_qty is None else float(new_qty)
        new_unit = edit.get("unit")
        unit = rl.get("unit") if new_unit is None else new_unit

        touched = (
            (new_name is not None and str(new_name).strip() != source_name)
            or (new_qty is not None and float(new_qty) != float(rl.get("quantity") or 0.0))
            or (new_unit is not None and new_unit != rl.get("unit"))
        )
        if touched:
            delta: dict[str, Any] = {"line_no": i}
            if new_name is not None and str(new_name).strip() != source_name:
                delta["product_name"] = {"from": source_name, "to": str(new_name).strip()}
            if new_qty is not None and float(new_qty) != float(rl.get("quantity") or 0.0):
                delta["quantity"] = {"from": rl.get("quantity"), "to": new_qty}
            if new_unit is not None and new_unit != rl.get("unit"):
                delta["unit"] = {"from": rl.get("unit"), "to": new_unit}
            corrected.append(delta)

        proposal.append({
            "line_no": i,
            "product_name": name,
            "quantity": qty,
            "unit": unit,
            "raw_text": rl.get("raw_text") or "",
            "edited": touched,
            "match": None if touched else {
                "product_id": rl.get("matched_product_id"),
                "product_display": rl.get("matched_product_name") or name,
                "unit_id": rl.get("unit_id"),
                "confidence": rl.get("confidence"),
                "match_method": rl.get("match_method"),
            },
        })

    # Lines the extractor never saw. They continue the numbering, so the order
    # reads in the sequence the reviewer built instead of with a gap where the
    # missing line was.
    next_no = len(proposal)
    added_summary: list[dict[str, Any]] = []
    for a in added:
        name = str(a.get("product_name") or "").strip()
        if not name:
            raise ValueError("An added line needs a product name")
        next_no += 1
        qty = float(a.get("quantity") or 0.0)
        proposal.append({
            "line_no": next_no,
            "product_name": name,
            "quantity": qty,
            "unit": a.get("unit"),
            "raw_text": "",
            "edited": True,
            "match": None,
        })
        added_summary.append({"line_no": next_no, "product_name": name, "quantity": qty})

    for ln in proposal:
        if ln["quantity"] <= 0:
            raise ValueError(
                f"Line {ln['line_no']} ({ln['product_name'] or 'no product'}) has a "
                f"quantity of {ln['quantity']:g} — set a quantity above zero, or "
                "remove the line."
            )

    summary = {
        "corrected": corrected,
        # The union, so `cancelled_lines_excluded` on the order audit stays a
        # count of everything that was dropped, from either source.
        "removed_line_nos": sorted(note_cancelled + reviewer_removed),
        "cancelled_on_note": note_cancelled,
        "removed_by_reviewer": reviewer_removed,
        "added_lines": added_summary,
    }
    return proposal, summary


def _normalize_proposal(
    db: Session,
    proposal: list[dict[str, Any]],
    *,
    customer_id: str | None,
) -> list[dict[str, Any]]:
    """Match a reviewer-approved line set to the catalog, in one pass.

    Untouched lines keep the match the extractor recorded; corrected and added
    lines are matched fresh from what the reviewer typed, with
    `match_method` saying so and no confidence — a person's correction is not a
    confidence score, and pretending otherwise would put an invented number on
    the order.
    """
    products: list[Product] = (
        db.query(Product).filter(Product.is_active.is_(True)).all()
    )
    aliases: list[CustomerProductAlias] = db.query(CustomerProductAlias).all()
    units: list[Unit] = db.query(Unit).all()

    out: list[dict[str, Any]] = []
    for p in proposal:
        kept = p.get("match")
        if not p.get("edited") and kept is not None:
            out.append({
                "line_no": p["line_no"],
                "raw_text": p.get("raw_text") or "",
                "product_id": kept.get("product_id"),
                "product_display": kept.get("product_display") or p["product_name"],
                "quantity": p["quantity"],
                "unit_id": kept.get("unit_id"),
                "confidence": kept.get("confidence"),
                "match_method": kept.get("match_method"),
            })
            continue

        pmatch = match_product(
            db,
            raw_name=p["product_name"],
            customer_id=customer_id,
            products=products,
            aliases=aliases,
        )
        matched_prod = (
            next((x for x in products if x.id == pmatch.product_id), None)
            if pmatch.product_id
            else None
        )
        umatch = match_unit(
            db, raw_unit=p.get("unit"), units=units, fallback_product=matched_prod
        )
        out.append({
            "line_no": p["line_no"],
            "raw_text": p.get("raw_text") or "",
            "product_id": pmatch.product_id,
            "product_display": pmatch.product_name or p["product_name"],
            "quantity": p["quantity"],
            "unit_id": umatch.unit_id,
            "confidence": None,
            "match_method": "human_added" if not p.get("raw_text") else "human_edited",
        })
    return out


def confirm_intake_review(
    db: Session,
    job_id: str,
    *,
    actor=None,
    edits: dict[str, Any] | None = None,
) -> Order:
    """Human confirms a `needs_review` extraction → create the draft Order.

    Called only by the review UI's "Confirm & submit" action. The pipeline
    never calls this automatically. Cancelled lines (e.g. struck-through / 已关)
    are excluded from the order — the human has already confirmed their removal
    during review, so we must not silently resurrect them.

    `edits` carries the reviewer's corrections, as a plain dict (the route
    dumps its request model into one, so this module never imports the API
    layer):

        {"lines": [{"line_no": 2, "quantity": 30, "unit": "斤",
                    "product_name": "大白菜", "cancelled": false}],
         "added_lines": [{"product_name": "大米", "quantity": 2, "unit": "袋"}]}

    They are applied to the ORDER and never to the stored extraction. The parse
    is the record of what the machine read; the reviewer's version is a
    different artefact and is kept separately, on the document and in the audit
    log. Collapsing the two would destroy the only evidence of how often the
    extractor is wrong — which is the number that decides whether it is worth
    trusting at all.

    Returns the created Order. Raises ValueError if the job isn't awaiting review,
    has no non-cancelled lines, or the edits do not fit the extraction.
    """
    job = db.get(IntakeJob, job_id)
    if job is None:
        raise ValueError(f"IntakeJob {job_id} not found")
    if job.status != "needs_review":
        raise ValueError(f"Job {job_id} is not awaiting review (status={job.status})")

    document = db.get(IntakeDocument, job.document_id)
    if document is None:
        raise ValueError("IntakeDocument not found")

    # Hard gate: no path from an unbound conversation to an order. Held
    # documents are released by POST /identity/bind, not by confirming harder.
    assert_document_bound(db, document)

    ext = (
        db.query(IntakeExtraction)
        .filter(IntakeExtraction.job_id == job_id)
        .order_by(IntakeExtraction.id)
        .first()
    )
    if ext is None:
        raise ValueError("No extraction found for this job")

    raw = ext.raw_output or {}
    raw_lines = raw.get("lines", [])

    summary: dict[str, Any] = {}
    if edits:
        proposal, summary = _apply_review_edits(raw_lines, edits)
        if not proposal:
            raise ValueError("No non-cancelled lines to confirm — nothing to order")
        normalized = _normalize_proposal(
            db, proposal, customer_id=document.customer_id
        )
    else:
        # The original path, unchanged: rebuild normalized line dicts from the
        # stored extraction, dropping lines the customer cancelled (they ride
        # through for human confirmation but must not become order lines).
        normalized = []
        dropped = 0
        for i, rl in enumerate(raw_lines, start=1):
            if rl.get("cancelled"):
                dropped += 1
                continue
            normalized.append({
                "line_no": i,
                "raw_text": rl.get("raw_text", ""),
                "product_id": rl.get("matched_product_id"),
                "product_display": rl.get("matched_product_name"),
                "quantity": rl.get("quantity") or 0.0,
                "unit_id": rl.get("unit_id"),
                "confidence": rl.get("confidence"),
                "match_method": rl.get("match_method"),
            })
        summary = {"cancelled_lines_excluded": dropped}

        if not normalized:
            raise ValueError("No non-cancelled lines to confirm — nothing to order")

    overall = float(
        raw.get("ocr_overall_confidence") or raw.get("overall_confidence") or 0.0
    )
    actor_id = getattr(actor, "id", None)
    order = _create_draft_order(
        db, document, job, normalized, overall,
        (raw.get("parser_notes") or "Confirmed by human review"),
        confirmed_by=actor_id,
    )
    job.draft_order_id = order.id

    log_audit(
        db, actor, "Order", order.id, "create",
        before=None,
        after={
            "order_number": order.order_number,
            "customer_id": order.customer_id,
            "status": order.status,
            "source": "human_review_confirm",
            "intake_document_id": document.id,
            "line_count": len(normalized),
            "cancelled_lines_excluded": summary.get("cancelled_lines_excluded", len(summary.get("removed_line_nos", []))),
        },
        summary=f"Human confirmed intake review → draft order {order.order_number}",
    )

    # Record what the person changed, when they changed anything. This is the
    # only place the size of the extractor's error is written down, so it is
    # kept even though it is not needed to build the order.
    if summary.get("corrected") or summary.get("added_lines") or summary.get("removed_line_nos"):
        meta = dict(document.document_meta or {})
        meta["human_review"] = {
            **summary,
            "by": actor_id,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        document.document_meta = meta
        flag_modified(document, "document_meta")
        db.flush()
        log_audit(
            db, actor, "IntakeJob", job.id, "review_edit",
            before=None, after=summary,
            summary=(
                f"Reviewer corrected intake job {job.id}: "
                f"{len(summary.get('corrected') or [])} corrected, "
                f"{len(summary.get('added_lines') or [])} added, "
                f"{len(summary.get('removed_by_reviewer') or [])} removed"
            ),
        )

    log_audit(
        db, actor, "IntakeJob", job.id, "review_confirm",
        before={"status": "needs_review"},
        after={"status": "completed", "draft_order_id": order.id},
        summary=f"Intake job {job.id} confirmed by human review",
    )

    db.commit()
    emit("order.draft_created", db=db, order=order)

    job = db.get(IntakeJob, job_id) or job
    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    return order
