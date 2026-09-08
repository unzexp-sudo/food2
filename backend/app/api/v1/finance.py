"""FINANCE MODULE — owner: finance agent.

Implements (see docs/AGENT_CONTRACTS.md §5):
  GET  /statements/customer/{id}    ?from=&to= (dates)  R: finance/admin
  GET  /statements/wholesaler/{id}  ?from=&to=  R: finance/admin
  POST /invoices/generate   {order_id}  R: finance/admin
  GET  /invoices            paged; filters: status, customer_id   R: finance/admin
  GET  /invoices/{id}       → Invoice + lines  R: finance/admin
  POST /invoices/{id}/payments  {amount, method?, note?}  R: finance/admin
  GET  /payments            paged; filters: direction, invoice_id  R: finance/admin
  GET  /reports/margin      ?by=customer|category|product&from=&to=  R: finance/admin

Multi-router pattern: statements_router (/api/v1/statements),
invoices_router (/api/v1/invoices), payments_router (/api/v1/payments),
reports_router (/api/v1/reports). The exported `router` has no prefix of its
own; the sub-routers carry their full `/api/v1/<resource>` prefixes.

Importing this module also imports app.services.finance which registers the
"delivery.completed" auto-invoice handler at import time.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.events import emit
from app.core.pagination import clamp_page, page_response
from app.models import Invoice, User
from app.schemas.finance import (
    InvoiceGenerateIn,
    PaymentCreateIn,
)
from app.services.finance import invoices as inv_svc
from app.services.finance import margin as margin_svc
from app.services.finance import payments as pay_svc
from app.services.finance import statements as stmt_svc
from app.services.finance.invoices import InvoiceExists

# Importing the services package registers the auto-invoice event handler.
import app.services.finance  # noqa: F401

statements_router = APIRouter(prefix="/api/v1/statements", tags=["finance"])
invoices_router = APIRouter(prefix="/api/v1/invoices", tags=["finance"])
payments_router = APIRouter(prefix="/api/v1/payments", tags=["finance"])
reports_router = APIRouter(prefix="/api/v1/reports", tags=["finance"])
# `router` is what main.py registers. No prefix of its own; sub-routers carry
# their full `/api/v1/<resource>` prefixes.
router = APIRouter(tags=["finance"])


# --- Statements ---------------------------------------------------------------

@statements_router.get("/customer/{customer_id}", response_model=None)
def customer_statement_endpoint(
    customer_id: str,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("finance", "admin")),
):
    return stmt_svc.customer_statement(
        db, customer_id=customer_id, date_from=date_from, date_to=date_to,
    )


@statements_router.get("/wholesaler/{wholesaler_id}", response_model=None)
def wholesaler_statement_endpoint(
    wholesaler_id: str,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("finance", "admin")),
):
    return stmt_svc.wholesaler_statement(
        db, wholesaler_id=wholesaler_id, date_from=date_from, date_to=date_to,
    )


# --- Invoices -----------------------------------------------------------------

@invoices_router.post("/generate", response_model=None,
                      status_code=status.HTTP_201_CREATED)
def generate_invoice_endpoint(
    payload: InvoiceGenerateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("finance", "admin")),
):
    try:
        invoice = inv_svc.generate_invoice(
            db, order_id=payload.order_id, actor=actor,
            audit_action="generate_invoice",
        )
    except InvoiceExists as exc:
        # Idempotent: return the existing invoice in the 409 body.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "message": f"Invoice already exists for order {payload.order_id}",
                "invoice": exc.serialized(),
            },
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    # Outbound WeCom notification (docs/WECOM_CONTRACTS.md §11).
    emit("invoice.created", db=db, invoice=invoice)
    return inv_svc.serialize_invoice(db, invoice)


@invoices_router.get("", response_model=None)
def list_invoices_endpoint(
    status_filter: str | None = Query(default=None, alias="status"),
    customer_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("finance", "admin")),
):
    page, page_size = clamp_page(page, page_size)
    items, total = inv_svc.list_invoices(
        db, status=status_filter, customer_id=customer_id,
    )
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


@invoices_router.get("/{invoice_id}", response_model=None)
def get_invoice_endpoint(
    invoice_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("finance", "admin")),
):
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return inv_svc.serialize_invoice(db, inv)


@invoices_router.post("/{invoice_id}/payments", response_model=None,
                      status_code=status.HTTP_201_CREATED)
def create_payment_endpoint(
    invoice_id: str,
    payload: PaymentCreateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("finance", "admin")),
):
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    try:
        payment = pay_svc.create_payment(
            db, invoice=inv, amount=payload.amount,
            method=payload.method, note=payload.note, actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return {
        "payment": pay_svc.serialize_payment(payment),
        "invoice": pay_svc.invoice_after_payment(db, inv),
    }


# --- Payments -----------------------------------------------------------------

@payments_router.get("", response_model=None)
def list_payments_endpoint(
    direction: str | None = Query(default=None),
    invoice_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("finance", "admin")),
):
    page, page_size = clamp_page(page, page_size)
    items, total = pay_svc.list_payments(
        db, direction=direction, invoice_id=invoice_id,
    )
    start = (page - 1) * page_size
    return page_response(items[start : start + page_size], total, page, page_size)


# --- Reports -----------------------------------------------------------------

@reports_router.get("/margin", response_model=None)
def margin_report_endpoint(
    by: Literal["customer", "category", "product"] = Query(default="customer"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("finance", "admin")),
):
    return margin_svc.build_margin_report(
        db, by=by, date_from=date_from, date_to=date_to,
    )


# Merge sub-routers into the exported `router` (must come AFTER route
# decorators are registered on the sub-routers — include_router copies the
# route table at call time).
router.include_router(statements_router)
router.include_router(invoices_router)
router.include_router(payments_router)
router.include_router(reports_router)
