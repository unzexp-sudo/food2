"""Pydantic schemas for the orders module.

Response shapes match docs/AGENT_CONTRACTS.md §4 (Order, OrderLine) and §6 (lineage).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Order line payloads ------------------------------------------------------
class OrderLineCreate(BaseModel):
    """Payload for creating/replacing a line on an order."""

    product_id: str | None = None
    product_display: str | None = Field(default=None, max_length=200)
    quantity: float = Field(gt=0)
    unit_id: str | None = None
    unit_price: float | None = None
    raw_text: str | None = None


class OrderLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    line_no: int
    raw_text: str | None = None
    product_id: str | None = None
    product_display: str | None = None
    quantity: float
    unit_id: str | None = None
    unit_code: str | None = None
    unit_price: float | None = None
    confidence: float | None = None
    match_method: str | None = None


# --- Order payloads -----------------------------------------------------------
class OrderCreate(BaseModel):
    customer_id: str
    delivery_date: date
    notes: str | None = None
    lines: list[OrderLineCreate] = Field(min_length=1)


class OrderLineReplace(BaseModel):
    """Bulk-replace lines payload."""

    lines: list[OrderLineCreate] = Field(min_length=1)


class OrderConfirm(BaseModel):
    notes: str | None = None


class OrderReject(BaseModel):
    reason: str = Field(min_length=1)


class OrderClarification(BaseModel):
    note: str = Field(min_length=1)


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_number: str
    customer_id: str
    customer_name_en: str
    customer_name_zh: str
    status: str
    delivery_date: date
    source_type: str
    intake_document_id: str | None = None
    standing_template_id: str | None = None
    overall_confidence: float | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    notes: str | None = None
    line_count: int
    created_at: datetime
    lines: list[OrderLineOut] = Field(default_factory=list)


# --- Lineage (§6) -------------------------------------------------------------
class LineageLine(BaseModel):
    order_line_id: str
    raw_text: str | None = None
    product_name_en: str | None = None
    product_name_zh: str | None = None
    quantity: float
    unit_code: str | None = None
    batch_number: str | None = None
    po_number: str | None = None
    po_quantity: float | None = None
    received_quantity: float | None = None
    picked_quantity: float | None = None
    delivered_quantity: float | None = None
    invoice_id: str | None = None
    invoice_number: str | None = None


class LineageOut(BaseModel):
    order_id: str
    order_number: str
    lines: list[LineageLine]
