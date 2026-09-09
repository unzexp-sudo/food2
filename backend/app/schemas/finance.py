"""Pydantic schemas for the finance module (invoices, payments, statements, margin).

Response shapes match docs/AGENT_CONTRACTS.md §4 (Invoice, InvoiceLine, Payment)
and §5 (statements rows, margin rows).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Invoices ----------------------------------------------------------------
class InvoiceGenerateIn(BaseModel):
    order_id: str


class InvoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_line_id: str | None = None
    description: str
    quantity: float
    unit_price: float
    amount: float


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    invoice_number: str
    customer_id: str
    customer_name_en: str
    customer_name_zh: str
    order_id: str | None = None
    order_number: str | None = None
    status: str
    total_amount: float
    paid_amount: float
    issued_at: datetime | None = None
    created_at: datetime
    lines: list[InvoiceLineOut] = Field(default_factory=list)


# --- Payments ----------------------------------------------------------------
class PaymentCreateIn(BaseModel):
    amount: float = Field(gt=0)
    method: str | None = None
    note: str | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    invoice_id: str | None = None
    direction: str
    counterparty: str | None = None
    amount: float
    method: str | None = None
    paid_at: datetime
    note: str | None = None


# --- Statements --------------------------------------------------------------
class StatementGenerateIn(BaseModel):
    """Generate an AR (customer) or AP (wholesaler) statement for a date range.

    The statement is computed on demand from deliveries/inbound receipts — there
    is no persisted Statement resource, so "generate" returns the same computed
    payload the GET endpoints produce.
    """

    party_type: Literal["customer", "wholesaler"]
    party_id: str
    from_date: date | None = None
    to_date: date | None = None


class ARStatementRow(BaseModel):
    order_id: str
    order_number: str | None = None
    delivery_date: str | None = None
    description: str
    quantity: float
    unit_price: float
    amount: float


class ARStatementOut(BaseModel):
    rows: list[ARStatementRow]
    total: float


class APStatementRow(BaseModel):
    po_number: str | None = None
    receipt_number: str | None = None
    received_at: str | None = None
    description: str
    quantity: float
    cost_price: float
    amount: float


class APStatementOut(BaseModel):
    rows: list[APStatementRow]
    total: float


# --- Margin report -----------------------------------------------------------
class MarginRow(BaseModel):
    dimension_id: str
    dimension_name: str
    revenue: float
    cost: float
    margin: float
    margin_pct: float | None = None


class MarginReportOut(BaseModel):
    rows: list[MarginRow]
    warning: str | None = None
