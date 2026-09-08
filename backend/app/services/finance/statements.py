"""Statements service.

AR (customer statement):
  - Rows from DELIVERED quantities: for each delivery_line with
    delivered_quantity>0 joined to order_line + order, group per order line:
    {order_id, order_number, delivery_date, description, quantity,
    unit_price, amount}. Description = product name (or product_display).
  - Filter by delivery_date in [from,to] (optional).
  - Plus totals {total}.

AP (wholesaler statement):
  - Rows from inbound receipts on POs of that wholesaler:
    {po_number, receipt_number, received_at, description, quantity,
    cost_price, amount}. amount = qty × po_line.cost_price.
  - Filter by receipt.received_at date in [from,to] (optional).
  - Plus totals.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import (
    ConsolidationBatch,
    Customer,
    Delivery,
    DeliveryLine,
    InboundReceipt,
    InboundReceiptLine,
    Order,
    OrderLine,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
)


def _description(ol: OrderLine | None, product: Product | None) -> str:
    if product is not None:
        return product.name_en or product.name_zh or ""
    if ol is not None:
        return ol.product_display or ol.raw_text or ""
    return ""


# --- AR (customer) ------------------------------------------------------------


def customer_statement(
    db: Session,
    *,
    customer_id: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """AR rows from delivered quantities for this customer.

    Groups per order line: an order line delivered across multiple deliveries
    (partial then top-up) sums to one row.
    """
    customer = db.get(Customer, customer_id)
    if customer is None:
        return {"rows": [], "total": 0.0}

    # Join delivery_line → order_line → order (filtered to this customer).
    q = (
        db.query(DeliveryLine, OrderLine, Order, Delivery)
        .join(OrderLine, OrderLine.id == DeliveryLine.order_line_id)
        .join(Order, Order.id == OrderLine.order_id)
        .join(Delivery, Delivery.id == DeliveryLine.delivery_id)
        .filter(
            Order.customer_id == customer_id,
            Delivery.status.in_(("delivered", "partial")),
            DeliveryLine.delivered_quantity > 0,
        )
    )
    if date_from is not None:
        q = q.filter(Delivery.scheduled_date >= date_from)
    if date_to is not None:
        q = q.filter(Delivery.scheduled_date <= date_to)
    rows = q.all()

    if not rows:
        return {"rows": [], "total": 0.0}

    # Aggregate per (order_line_id): delivered_quantity and delivery_date.
    by_ol: dict[str, dict] = {}
    product_ids: set[str] = set()
    for dl, ol, order, delivery in rows:
        pid = ol.product_id
        if pid:
            product_ids.add(pid)
        entry = by_ol.get(ol.id)
        if entry is None:
            entry = {
                "order_id": order.id,
                "order_number": order.order_number,
                "delivery_date": (
                    delivery.scheduled_date.isoformat()
                    if delivery.scheduled_date else None
                ),
                "description": "",  # filled after product lookup
                "quantity": float(dl.delivered_quantity or 0),
                "unit_price": float(ol.unit_price) if ol.unit_price is not None else 0.0,
                "_ol": ol,
            }
            by_ol[ol.id] = entry
        else:
            entry["quantity"] += float(dl.delivered_quantity or 0)

    products: dict[str, Product] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(list(product_ids))).all():
            products[p.id] = p

    total = 0.0
    out_rows: list[dict] = []
    # Stable ordering by order then line_no.
    for ol_id in sorted(by_ol.keys(), key=lambda k: (by_ol[k]["order_number"] or "", k)):
        entry = by_ol[ol_id]
        ol = entry.pop("_ol", None)
        prod = products.get(ol.product_id) if ol and ol.product_id else None
        entry["description"] = _description(ol, prod)
        amount = round(entry["quantity"] * entry["unit_price"], 6)
        entry["amount"] = amount
        total += amount
        # Drop the helper key from the returned dict.
        out_rows.append({
            "order_id": entry["order_id"],
            "order_number": entry["order_number"],
            "delivery_date": entry["delivery_date"],
            "description": entry["description"],
            "quantity": entry["quantity"],
            "unit_price": entry["unit_price"],
            "amount": entry["amount"],
        })
    return {"rows": out_rows, "total": round(total, 6)}


# --- AP (wholesaler) ---------------------------------------------------------


def wholesaler_statement(
    db: Session,
    *,
    wholesaler_id: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """AP rows from inbound receipts on POs of that wholesaler."""
    # POs of this wholesaler.
    po_rows = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.wholesaler_id == wholesaler_id)
        .all()
    )
    if not po_rows:
        return {"rows": [], "total": 0.0}
    po_ids = [po.id for po in po_rows]
    po_by_id = {po.id: po for po in po_rows}

    # Inbound receipts for those POs.
    rcpt_q = db.query(InboundReceipt).filter(InboundReceipt.po_id.in_(po_ids))
    if date_from is not None or date_to is not None:
        # received_at is a datetime; compare the date portion.
        if date_from is not None:
            # >= from 00:00:00
            from_dt = datetime.combine(date_from, datetime.min.time())
            rcpt_q = rcpt_q.filter(InboundReceipt.received_at >= from_dt)
        if date_to is not None:
            # <= to 23:59:59.999999
            to_dt = datetime.combine(date_to, datetime.max.time())
            rcpt_q = rcpt_q.filter(InboundReceipt.received_at <= to_dt)
    receipts = rcpt_q.order_by(InboundReceipt.received_at.desc()).all()
    if not receipts:
        return {"rows": [], "total": 0.0}

    receipt_ids = [r.id for r in receipts]
    receipt_by_id = {r.id: r for r in receipts}

    # Lines for those receipts.
    rcpt_lines = (
        db.query(InboundReceiptLine, PurchaseOrderLine)
        .join(PurchaseOrderLine, PurchaseOrderLine.id == InboundReceiptLine.po_line_id)
        .filter(InboundReceiptLine.receipt_id.in_(receipt_ids))
        .all()
    )
    if not rcpt_lines:
        return {"rows": [], "total": 0.0}

    product_ids = {pl.product_id for _, pl in rcpt_lines if pl.product_id}
    products: dict[str, Product] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(list(product_ids))).all():
            products[p.id] = p

    total = 0.0
    out_rows: list[dict] = []
    for rl, pl in rcpt_lines:
        receipt = receipt_by_id.get(rl.receipt_id)
        po = po_by_id.get(receipt.po_id) if receipt else None
        prod = products.get(pl.product_id) if pl.product_id else None
        qty = float(rl.quantity_received or 0)
        cost = float(pl.cost_price) if pl.cost_price is not None else 0.0
        amount = round(qty * cost, 6)
        total += amount
        description = prod.name_en if prod else (prod.name_zh if prod else "")
        out_rows.append({
            "po_number": po.po_number if po else None,
            "receipt_number": receipt.receipt_number if receipt else None,
            "received_at": (
                receipt.received_at.isoformat() if receipt and receipt.received_at else None
            ),
            "description": description,
            "quantity": qty,
            "cost_price": cost,
            "amount": amount,
        })
    return {"rows": out_rows, "total": round(total, 6)}
