"""Margin report service.

GET /reports/margin?by=customer|category|product&from=&to=

  Revenue = Σ invoice line amounts (delivered basis).
  Cost = Σ over the order's delivered lines: delivered_quantity × PO cost price.

Cost derivation (per the finance contract note in §5):
  Walk order_line → consolidation_batch_line → purchase_order_line.cost_price.
  If multiple PO lines exist for the same product, use the AVERAGE cost weighted
  by PO line quantity.

For each invoice line:
  - revenue = the invoice line amount.
  - cost = delivered_quantity × weighted_avg_cost_price for that order line's
    product (via its batch → PO lines).

If cost data is missing for any row (no PO line / no cost_price) → cost 0 for
that row and a top-level {"warning": "..."} note is included on the report.

dimension_name is bilingual: prefer the EN name (customer name_en / category
name_en / product name_en) — that's fine per the contract note.

Date filter:
  - Filters by the invoice.issued_at date (when the invoice was issued, which
    reflects the delivered-basis revenue). This aligns revenue and cost on the
    same delivery-basis.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    ConsolidationBatchLine,
    Customer,
    Invoice,
    InvoiceLine,
    Order,
    OrderLine,
    Product,
    ProductCategory,
    PurchaseOrderLine,
    Wholesaler,
)


def _weighted_cost_per_product(
    db: Session,
    po_line_ids: list[str],
) -> dict[str, tuple[float, bool]]:
    """For each product_id touched by the given PO lines, compute the
    quantity-weighted average cost_price across all PO lines for that product.

    Returns {product_id: (avg_cost, has_missing)}.
    `has_missing` is True if any PO line for the product has no cost_price.
    """
    out: dict[str, tuple[float, bool]] = {}
    if not po_line_ids:
        return out
    # Get the products + cost/qty for the linked PO lines.
    pol_rows = (
        db.query(PurchaseOrderLine.product_id, PurchaseOrderLine.cost_price,
                 PurchaseOrderLine.quantity_ordered)
        .filter(PurchaseOrderLine.id.in_(po_line_ids))
        .all()
    )
    if not pol_rows:
        return out
    product_ids = {r[0] for r in pol_rows if r[0]}
    # Pull all PO lines for these products so the weighted average considers
    # every PO line (not just the ones linked to the order's batch lines).
    all_rows = (
        db.query(PurchaseOrderLine.product_id, PurchaseOrderLine.cost_price,
                 PurchaseOrderLine.quantity_ordered)
        .filter(PurchaseOrderLine.product_id.in_(list(product_ids)))
        .all()
    )
    by_product: dict[str, list[tuple[float | None, float]]] = {}
    for pid, cost, qty in all_rows:
        by_product.setdefault(pid, []).append((cost, float(qty or 0)))
    for pid, entries in by_product.items():
        total_qty = 0.0
        total_weighted = 0.0
        has_missing = False
        for cost, qty in entries:
            if cost is None:
                has_missing = True
                continue
            total_qty += qty
            total_weighted += cost * qty
        avg = (total_weighted / total_qty) if total_qty > 0 else 0.0
        out[pid] = (avg, has_missing or not entries)
    return out


def build_margin_report(
    db: Session,
    *,
    by: str = "customer",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Build the margin report.

    Returns {"rows": [...], "warning": str | None}.
    """
    if by not in ("customer", "category", "product"):
        by = "customer"

    # Pull invoice lines in the date range, joined to their invoice + order +
    # order line (for the cost derivation).
    inv_q = (
        db.query(InvoiceLine, Invoice, Order, OrderLine)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .join(Order, Order.id == Invoice.order_id)
        .join(OrderLine, OrderLine.id == InvoiceLine.order_line_id)
        .filter(Invoice.status != "void")
    )
    if date_from is not None:
        from_dt = datetime.combine(date_from, datetime.min.time())
        inv_q = inv_q.filter(Invoice.issued_at >= from_dt)
    if date_to is not None:
        to_dt = datetime.combine(date_to, datetime.max.time())
        inv_q = inv_q.filter(Invoice.issued_at <= to_dt)
    inv_rows = inv_q.all()
    if not inv_rows:
        return {"rows": [], "warning": None}

    # Resolve the PO line ids per order line (for the cost derivation).
    order_line_ids = [ol.id for _, _, _, ol in inv_rows]
    batch_links = {}
    if order_line_ids:
        for bl in db.query(ConsolidationBatchLine).filter(
            ConsolidationBatchLine.order_line_id.in_(order_line_ids)
        ).all():
            if bl.purchase_order_line_id:
                batch_links.setdefault(bl.order_line_id, []).append(
                    bl.purchase_order_line_id
                )
    po_line_ids = [pid for ids in batch_links.values() for pid in ids]
    cost_map = _weighted_cost_per_product(db, po_line_ids)

    # Pre-fetch dimension lookups: customers, categories, products.
    customer_ids = {o.customer_id for _, _, o, _ in inv_rows}
    product_ids = {ol.product_id for _, _, _, ol in inv_rows if ol.product_id}
    category_ids: set[str] = set()
    customers: dict[str, Customer] = {}
    products: dict[str, Product] = {}
    categories: dict[str, ProductCategory] = {}
    if customer_ids:
        for c in db.query(Customer).filter(Customer.id.in_(list(customer_ids))).all():
            customers[c.id] = c
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(list(product_ids))).all():
            products[p.id] = p
            if p.category_id:
                category_ids.add(p.category_id)
    if category_ids:
        for c in db.query(ProductCategory).filter(
            ProductCategory.id.in_(list(category_ids))
        ).all():
            categories[c.id] = c

    has_missing_cost = False
    buckets: dict[str, dict] = {}

    for il, inv, order, ol in inv_rows:
        # Determine dimension.
        if by == "customer":
            dim_id = order.customer_id
            cust = customers.get(order.customer_id)
            dim_name = cust.name_en if cust else ""
        elif by == "category":
            prod = products.get(ol.product_id) if ol.product_id else None
            cat = categories.get(prod.category_id) if prod and prod.category_id else None
            dim_id = prod.category_id if prod and prod.category_id else "unknown"
            dim_name = cat.name_en if cat else "Unknown"
        else:  # product
            dim_id = ol.product_id or "unknown"
            prod = products.get(ol.product_id) if ol.product_id else None
            dim_name = prod.name_en if prod else (ol.product_display or "")

        revenue = float(il.amount or 0)
        # Cost: delivered_quantity × weighted avg cost price for the product.
        cost = 0.0
        if ol.product_id and ol.product_id in cost_map:
            avg_cost, missing = cost_map[ol.product_id]
            cost = float(il.quantity or 0) * avg_cost
            if missing or avg_cost == 0.0:
                has_missing_cost = True
        else:
            # No PO line / no cost data for this row.
            has_missing_cost = True

        bucket = buckets.setdefault(dim_id, {
            "dimension_id": dim_id,
            "dimension_name": dim_name,
            "revenue": 0.0,
            "cost": 0.0,
        })
        bucket["revenue"] += revenue
        bucket["cost"] += cost

    out_rows: list[dict] = []
    for dim_id in sorted(buckets.keys(), key=lambda k: (buckets[k]["dimension_name"], k)):
        b = buckets[dim_id]
        revenue = round(b["revenue"], 6)
        cost = round(b["cost"], 6)
        margin = round(revenue - cost, 6)
        margin_pct = (margin / revenue) if revenue > 0 else None
        out_rows.append({
            "dimension_id": b["dimension_id"],
            "dimension_name": b["dimension_name"],
            "revenue": revenue,
            "cost": cost,
            "margin": margin,
            "margin_pct": round(margin_pct, 4) if margin_pct is not None else None,
        })
    return {
        "rows": out_rows,
        "warning": (
            "Some rows had no cost data (no PO line or no cost_price); "
            "cost set to 0 for those rows"
            if has_missing_cost else None
        ),
    }
