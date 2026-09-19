"""Inbound receipt service.

Creating an inbound receipt against a PurchaseOrder:
  - validates PO status (sent | partially_received)
  - creates InboundReceipt + InboundReceiptLines
  - flags discrepancies (received != ordered per line)
  - increments po_line.quantity_received
  - records InventoryMovement (+quantity_received − quantity_damaged)
  - updates PO status: any line under-ordered → "partially_received";
    all lines fully received (sum ≥ ordered) → "received"
  - audit-logs

Then the warehouse router triggers pick list regeneration for the PO's
batch delivery_date (see picklists.regenerate_for_date).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.numbers import next_number
from app.models import (
    ConsolidationBatch,
    Customer,
    InboundReceipt,
    InboundReceiptLine,
    InventoryMovement,
    Order,
    OrderLine,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    User,
    Wholesaler,
)


RECEIVABLE_PO_STATUSES = {"sent", "partially_received"}


def _po_summary(po: PurchaseOrder) -> dict:
    return {
        "id": po.id,
        "po_number": po.po_number,
        "status": po.status,
    }


def _receipt_line_out(db: Session, rl: InboundReceiptLine) -> dict:
    pl = db.get(PurchaseOrderLine, rl.po_line_id) if rl.po_line_id else None
    prod = db.get(Product, pl.product_id) if pl and pl.product_id else None
    ordered = pl.quantity_ordered if pl else 0.0
    received = rl.quantity_received or 0.0
    damaged = rl.quantity_damaged or 0.0
    discrepancy = abs(received - ordered) > 1e-9
    return {
        "id": rl.id,
        "po_line_id": rl.po_line_id,
        "product_name_en": prod.name_en if prod else "",
        "product_name_zh": prod.name_zh if prod else "",
        "quantity_ordered": ordered,
        "quantity_received": received,
        "quantity_damaged": damaged,
        "discrepancy": discrepancy,
        "notes": rl.notes,
    }


def serialize_receipt(db: Session, r: InboundReceipt) -> dict:
    po = db.get(PurchaseOrder, r.po_id) if r.po_id else None
    return {
        "id": r.id,
        "receipt_number": r.receipt_number,
        "po_id": r.po_id,
        "po_number": po.po_number if po else None,
        "received_by": r.received_by,
        "received_at": r.received_at.isoformat() if r.received_at else None,
        "status": r.status,
        "notes": r.notes,
        "lines": [_receipt_line_out(db, rl) for rl in (r.lines or [])],
    }


def list_receipts(
    db: Session,
    *,
    po_id: str | None = None,
) -> tuple[list[dict], int]:
    q = db.query(InboundReceipt)
    if po_id:
        q = q.filter(InboundReceipt.po_id == po_id)
    total = q.count()
    rows = q.order_by(InboundReceipt.received_at.desc()).all()
    return [serialize_receipt(db, r) for r in rows], total


def get_receipt(db: Session, receipt_id: str) -> dict | None:
    """One receipt with its lines — the `View lines` drawer.

    This route did not exist: the page called `GET /inbound-receipts/{id}` and
    the router only had `""`, so the button 404'd and the drawer rendered "no
    lines" for every receipt ever created. A 404 swallowed by a `.catch()` is
    indistinguishable from an empty receipt.
    """
    r = db.get(InboundReceipt, receipt_id)
    return serialize_receipt(db, r) if r is not None else None


def _awaiting_receipt_rows(db: Session) -> list[tuple[PurchaseOrder, date | None]]:
    """POs the warehouse is owed, with the date the goods are due.

    The date lives on the consolidation batch, not the PO, so it is joined in
    here rather than left for the caller to fetch per row.
    """
    rows = (
        db.query(PurchaseOrder, ConsolidationBatch.delivery_date)
        .outerjoin(ConsolidationBatch, ConsolidationBatch.id == PurchaseOrder.batch_id)
        .filter(PurchaseOrder.status.in_(sorted(RECEIVABLE_PO_STATUSES)))
        .all()
    )
    # Due first, then anything with no batch (a hand-raised PO) by number.
    return sorted(rows, key=lambda r: (r[1] is None, r[1] or date.min, r[0].po_number))


def list_awaiting_receipts(db: Session) -> tuple[list[dict], int]:
    """`GET /inbound-receipts/awaiting` — the work, not the history.

    This is the list the Inbound badge counts. It exists because the page used
    to render *receipts* while the badge counted *purchase orders awaiting a
    receipt*: the badge read 2 and the table was empty, and the operator had no
    way to tell a broken badge from a real one.

    It also closes a harder dead end. The page built its PO picker from
    `GET /purchase-orders`, which requires `ops`/`finance` — so for a warehouse
    user that request was a 403, caught and discarded, and the picker was
    **permanently empty**. The role that was supposed to receive goods had no
    way to name one.

    Deliberately returns no `cost_price` and no `total_amount`: whoever checks
    the truck against the delivery note does not need what we pay for it.
    """
    out: list[dict] = []
    for po, delivery_date in _awaiting_receipt_rows(db):
        lines = (
            db.query(PurchaseOrderLine)
            .filter(PurchaseOrderLine.po_id == po.id)
            .all()
        )
        total_ordered = sum((ln.quantity_ordered or 0.0) for ln in lines)
        total_received = sum((ln.quantity_received or 0.0) for ln in lines)
        outstanding = sum(
            max((ln.quantity_ordered or 0.0) - (ln.quantity_received or 0.0), 0.0)
            for ln in lines
        )
        wholesaler = db.get(Wholesaler, po.wholesaler_id) if po.wholesaler_id else None
        out.append({
            "id": po.id,
            "po_number": po.po_number,
            "status": po.status,
            "wholesaler_id": po.wholesaler_id,
            "wholesaler_name_en": wholesaler.name_en if wholesaler else "",
            "wholesaler_name_zh": wholesaler.name_zh if wholesaler else "",
            "delivery_date": delivery_date.isoformat() if delivery_date else None,
            "sent_at": po.sent_at.isoformat() if po.sent_at else None,
            "line_count": len(lines),
            "total_ordered": total_ordered,
            "total_received": total_received,
            "quantity_outstanding": outstanding,
        })
    return out, len(out)


def count_awaiting_receipts(db: Session) -> int:
    """The Inbound nav badge — the same predicate as the page it badges.

    Shared with `list_awaiting_receipts` on purpose. The previous badge was a
    hand-written `status IN ('sent', 'partially_received')` inside
    `workqueue.collect`, next to a page that read a different table entirely;
    nothing tied them together, so nothing could tell them apart when they
    disagreed. A badge that disagrees with its page is worse than no badge.
    """
    return (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.status.in_(sorted(RECEIVABLE_PO_STATUSES)))
        .count()
    )


def po_received_summary(db: Session, po: PurchaseOrder) -> dict:
    """GET /purchase-orders/{id}/received shape."""
    lines = (
        db.query(PurchaseOrderLine)
        .filter(PurchaseOrderLine.po_id == po.id)
        .order_by(PurchaseOrderLine.id)
        .all()
    )
    line_out: list[dict] = []
    total_ordered = 0.0
    total_received = 0.0
    total_damaged = 0.0
    for pl in lines:
        prod = db.get(Product, pl.product_id) if pl.product_id else None
        # Sum receipts for this PO line.
        rcpt_lines = (
            db.query(InboundReceiptLine)
            .filter(InboundReceiptLine.po_line_id == pl.id)
            .all()
        )
        rcpt_qty = sum((rl.quantity_received or 0) for rl in rcpt_lines)
        dmg_qty = sum((rl.quantity_damaged or 0) for rl in rcpt_lines)
        ordered = pl.quantity_ordered or 0.0
        # quantity_received on the PO line is the running total; fall back to sum.
        received = pl.quantity_received if pl.quantity_received is not None else rcpt_qty
        damaged = dmg_qty
        line_out.append({
            "po_line_id": pl.id,
            "product_id": pl.product_id,
            "product_name_en": prod.name_en if prod else "",
            "product_name_zh": prod.name_zh if prod else "",
            "quantity_ordered": ordered,
            "quantity_received": received,
            "quantity_damaged": damaged,
            "discrepancy": abs(received - ordered) > 1e-9,
        })
        total_ordered += ordered
        total_received += received
        total_damaged += damaged
    # Receipts list.
    receipts = (
        db.query(InboundReceipt)
        .filter(InboundReceipt.po_id == po.id)
        .order_by(InboundReceipt.received_at.desc())
        .all()
    )
    return {
        "po_id": po.id,
        "po_number": po.po_number,
        "status": po.status,
        "total_ordered": total_ordered,
        "total_received": total_received,
        "total_damaged": total_damaged,
        "lines": line_out,
        "receipts": [serialize_receipt(db, r) for r in receipts],
    }


def create_inbound_receipt(
    db: Session,
    *,
    po: PurchaseOrder,
    lines: list[dict],
    actor: User | None = None,
    notes: str | None = None,
) -> InboundReceipt:
    """Create an inbound receipt and update PO status.

    Raises ValueError on:
      - PO not in {sent, partially_received}
      - a po_line_id doesn't belong to the PO
    """
    if po.status not in RECEIVABLE_PO_STATUSES:
        raise ValueError(
            f"Cannot receive against PO in status '{po.status}' "
            "(only sent|partially_received)"
        )

    # Validate po_line_ids belong to this PO.
    po_line_ids = [ln["po_line_id"] for ln in lines]
    po_lines: dict[str, PurchaseOrderLine] = {}
    if po_line_ids:
        for pl in db.query(PurchaseOrderLine).filter(
            PurchaseOrderLine.id.in_(po_line_ids)
        ).all():
            po_lines[pl.id] = pl
    for ln in lines:
        pl = po_lines.get(ln["po_line_id"])
        if pl is None or pl.po_id != po.id:
            raise ValueError(
                f"PO line {ln.get('po_line_id')} does not belong to PO {po.po_number}"
            )

    before = {"status": po.status, "quantity_received": {
        pl.id: pl.quantity_received for pl in po_lines.values()
    }}

    receipt_number = next_number(db, InboundReceipt, "receipt_number", "RCP")
    receipt = InboundReceipt(
        receipt_number=receipt_number,
        po_id=po.id,
        received_by=actor.id if actor else None,
        received_at=datetime.now(timezone.utc),
        status="posted",
        notes=notes,
    )
    db.add(receipt)
    db.flush()

    for ln in lines:
        pl = po_lines[ln["po_line_id"]]
        qty_received = float(ln["quantity_received"])
        qty_damaged = float(ln.get("quantity_damaged", 0) or 0)
        ordered = pl.quantity_ordered or 0.0
        discrepancy = abs(qty_received - ordered) > 1e-9
        line_notes = ln.get("notes")
        if discrepancy and not line_notes:
            line_notes = f"discrepancy: ordered {ordered}, received {qty_received}"
        rl = InboundReceiptLine(
            receipt_id=receipt.id,
            po_line_id=pl.id,
            quantity_received=qty_received,
            quantity_damaged=qty_damaged,
            notes=line_notes,
        )
        db.add(rl)
        # Increment the PO line running total.
        pl.quantity_received = (pl.quantity_received or 0.0) + qty_received
        # Inventory movement: +qty received − damaged.
        mv = InventoryMovement(
            product_id=pl.product_id,
            quantity_delta=qty_received - qty_damaged,
            ref_type="inbound_receipt",
            ref_id=receipt.id,
            note=f"Receipt {receipt.receipt_number} for PO {po.po_number}",
        )
        db.add(mv)
    db.flush()

    # Update PO status.
    all_pos_lines = (
        db.query(PurchaseOrderLine)
        .filter(PurchaseOrderLine.po_id == po.id)
        .all()
    )
    any_under = any(
        (pl.quantity_received or 0.0) < (pl.quantity_ordered or 0.0) - 1e-9
        for pl in all_pos_lines
    )
    all_full = all(
        (pl.quantity_received or 0.0) >= (pl.quantity_ordered or 0.0) - 1e-9
        for pl in all_pos_lines
    )
    if all_full:
        po.status = "received"
    elif any_under:
        po.status = "partially_received"
    db.flush()

    after = {
        "status": po.status,
        "quantity_received": {pl.id: pl.quantity_received for pl in po_lines.values()},
    }
    log_audit(
        db, actor, "InboundReceipt", receipt.id, "create",
        before=before,
        after=after,
        summary=(
            f"Receipt {receipt.receipt_number} created for PO {po.po_number}; "
            f"status → {po.status}"
        ),
    )
    return receipt


def po_batch_delivery_date(db: Session, po: PurchaseOrder) -> "date | None":  # noqa: F821
    """Resolve the delivery_date of the batch this PO belongs to, if any."""
    if not po.batch_id:
        return None
    b = db.get(ConsolidationBatch, po.batch_id)
    return b.delivery_date if b else None
