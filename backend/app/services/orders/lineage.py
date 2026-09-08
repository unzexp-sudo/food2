"""Order-line lineage service — walks the full fulfilment chain per §6.

For each order line, walks:
  order_line → consolidation_batch_line → purchase_order_line
            → inbound_receipt_lines (via po_line) → pick_lines
            → delivery_lines → invoice_lines

Returns the §6 JSON shape:
  {
    "order_id", "order_number",
    "lines": [{
      "order_line_id", "raw_text", "product_name_en", "product_name_zh",
      "quantity", "unit_code", "batch_number", "po_number", "po_quantity",
      "received_quantity", "picked_quantity", "delivered_quantity",
      "invoice_id", "invoice_number"
    }]
  }
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    ConsolidationBatch,
    ConsolidationBatchLine,
    DeliveryLine,
    Invoice,
    InvoiceLine,
    Order,
    OrderLine,
    PickLine,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    Unit,
    InboundReceiptLine,
)


def build_lineage(db: Session, order: Order) -> dict:
    lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order.id)
        .order_by(OrderLine.line_no)
        .all()
    )
    if not lines:
        return {"order_id": order.id, "order_number": order.order_number, "lines": []}

    line_ids = [ln.id for ln in lines]
    product_ids = [ln.product_id for ln in lines if ln.product_id]
    unit_ids = [ln.unit_id for ln in lines if ln.unit_id]

    products: dict[str, Product] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            products[p.id] = p
    units: dict[str, Unit] = {}
    if unit_ids:
        for u in db.query(Unit).filter(Unit.id.in_(unit_ids)).all():
            units[u.id] = u

    # Batch lines keyed by order_line_id.
    batch_lines = (
        db.query(ConsolidationBatchLine)
        .filter(ConsolidationBatchLine.order_line_id.in_(line_ids))
        .all()
    )
    batch_by_line: dict[str, ConsolidationBatchLine] = {
        bl.order_line_id: bl for bl in batch_lines
    }
    po_line_ids = [bl.purchase_order_line_id for bl in batch_lines if bl.purchase_order_line_id]

    # PO lines.
    po_lines: dict[str, PurchaseOrderLine] = {}
    if po_line_ids:
        for pl in db.query(PurchaseOrderLine).filter(
            PurchaseOrderLine.id.in_(po_line_ids)
        ).all():
            po_lines[pl.id] = pl
    po_ids = [pl.po_id for pl in po_lines.values()]
    pos: dict[str, PurchaseOrder] = {}
    if po_ids:
        for p in db.query(PurchaseOrder).filter(PurchaseOrder.id.in_(po_ids)).all():
            pos[p.id] = p

    # Batches for batch_number lookup.
    batch_ids = [bl.batch_id for bl in batch_lines]
    batches: dict[str, ConsolidationBatch] = {}
    if batch_ids:
        for b in db.query(ConsolidationBatch).filter(
            ConsolidationBatch.id.in_(batch_ids)
        ).all():
            batches[b.id] = b

    # Inbound receipt lines keyed by po_line_id (sum across receipts).
    inbound_lines: dict[str, list[InboundReceiptLine]] = {}
    if po_line_ids:
        for rl in db.query(InboundReceiptLine).filter(
            InboundReceiptLine.po_line_id.in_(po_line_ids)
        ).all():
            inbound_lines.setdefault(rl.po_line_id, []).append(rl)

    # Pick lines keyed by order_line_id.
    pick_lines: dict[str, list[PickLine]] = {}
    if line_ids:
        for pl in db.query(PickLine).filter(PickLine.order_line_id.in_(line_ids)).all():
            pick_lines.setdefault(pl.order_line_id, []).append(pl)

    # Delivery lines keyed by order_line_id.
    delivery_lines: dict[str, list[DeliveryLine]] = {}
    if line_ids:
        for dl in db.query(DeliveryLine).filter(
            DeliveryLine.order_line_id.in_(line_ids)
        ).all():
            delivery_lines.setdefault(dl.order_line_id, []).append(dl)

    # Invoice lines keyed by order_line_id.
    invoice_lines: dict[str, list[InvoiceLine]] = {}
    if line_ids:
        for il in db.query(InvoiceLine).filter(
            InvoiceLine.order_line_id.in_(line_ids)
        ).all():
            invoice_lines.setdefault(il.order_line_id, []).append(il)

    invoice_ids = {il.invoice_id for ils in invoice_lines.values() for il in ils}
    invoices: dict[str, Invoice] = {}
    if invoice_ids:
        for inv in db.query(Invoice).filter(Invoice.id.in_(list(invoice_ids))).all():
            invoices[inv.id] = inv

    out_lines: list[dict] = []
    for ln in lines:
        prod = products.get(ln.product_id) if ln.product_id else None
        unit = units.get(ln.unit_id) if ln.unit_id else None

        bl = batch_by_line.get(ln.id)
        batch_number = batches[bl.batch_id].batch_number if bl and bl.batch_id in batches else None
        po_line = po_lines.get(bl.purchase_order_line_id) if bl and bl.purchase_order_line_id else None
        po = pos.get(po_line.po_id) if po_line and po_line.po_id in pos else None
        po_number = po.po_number if po else None
        po_quantity = po_line.quantity_ordered if po_line else None

        # Sum received across all receipt lines for this PO line.
        received = None
        if po_line:
            rcs = inbound_lines.get(po_line.id, [])
            received = sum((r.quantity_received or 0) for r in rcs) if rcs else 0.0

        # Sum picked across pick lines for this order line.
        pls = pick_lines.get(ln.id, [])
        picked = sum((p.picked_quantity or 0) for p in pls) if pls else None

        # Sum delivered across delivery lines for this order line.
        dls = delivery_lines.get(ln.id, [])
        delivered = sum((d.delivered_quantity or 0) for d in dls) if dls else None

        # First invoice line → invoice id/number (one per order line, in practice).
        ils = invoice_lines.get(ln.id, [])
        invoice_id = ils[0].invoice_id if ils else None
        invoice_number = invoices[invoice_id].invoice_number if invoice_id and invoice_id in invoices else None

        out_lines.append({
            "order_line_id": ln.id,
            "raw_text": ln.raw_text,
            "product_name_en": prod.name_en if prod else (ln.product_display or None),
            "product_name_zh": prod.name_zh if prod else (ln.product_display or None),
            "quantity": ln.quantity,
            "unit_code": unit.code if unit else None,
            "batch_number": batch_number,
            "po_number": po_number,
            "po_quantity": po_quantity,
            "received_quantity": received,
            "picked_quantity": picked,
            "delivered_quantity": delivered,
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
        })

    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "lines": out_lines,
    }
