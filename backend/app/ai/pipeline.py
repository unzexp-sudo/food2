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

from app.ai.adapters import get_extractor
from app.ai.matching import match_product, match_unit
from app.core.audit import log_audit
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

        # Build the AI output schema per EXECUTIVE_SUMMARY.md
        raw_output: dict[str, Any] = {
            "customer_id": document.customer_id,
            "delivery_date": (document.document_meta or {}).get("delivery_date"),
            "doc_type": extraction.doc_type,
            "lines": [
                {
                    "raw_text": ln["raw_text"],
                    "matched_product_id": ln["matched_product_id"],
                    "matched_product_name": ln["matched_product_name"],
                    "quantity": ln["quantity"],
                    "unit": ln["unit"],
                    "unit_code": ln["unit_code"],
                    "confidence": ln["confidence"],
                    "match_method": ln["match_method"],
                    "breakdowns": ln.get("breakdowns", []),
                }
                for ln in lines
            ],
            "overall_confidence": overall,
            "parser_notes": parser_notes,
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

        # Step 8: create draft Order + OrderLines
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
            created_by=document.uploaded_by,
        )
        db.add(order)
        db.flush()

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
