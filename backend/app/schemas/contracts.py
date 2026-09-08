"""Pydantic schemas for the contracts module (master data agent).

Response shapes match docs/AGENT_CONTRACTS.md §4.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- ContractPrice ------------------------------------------------------------
class ContractPriceCreate(BaseModel):
    customer_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    price: float = Field(gt=0)
    valid_from: date
    valid_until: date | None = None


class ContractPriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    product_id: str
    unit_id: str
    price: float
    valid_from: date
    valid_until: date | None = None


# --- StandingOrderTemplate ----------------------------------------------------
class TemplateLineIn(BaseModel):
    product_id: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    unit_id: str = Field(min_length=1)


class TemplateLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: str
    quantity: float
    unit_id: str


class TemplateCreate(BaseModel):
    customer_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    delivery_days: list[str] = Field(default_factory=list)
    is_active: bool = True
    lines: list[TemplateLineIn] = Field(default_factory=list)


class TemplateUpdate(BaseModel):
    customer_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    delivery_days: list[str] | None = None
    is_active: bool | None = None
    lines: list[TemplateLineIn] | None = None  # if provided, fully replaced


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    name: str
    delivery_days: list[str]
    is_active: bool
    lines: list[TemplateLineOut]
    created_at: datetime


# --- Standing order → create order -------------------------------------------
class CreateOrderRequest(BaseModel):
    delivery_date: date | None = None


# Order response shape (matches §4 Order entity) — used for create-order result.
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
    unit_price: float | None = None
    confidence: float | None = None
    match_method: str | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_number: str
    customer_id: str
    status: str
    delivery_date: date
    source_type: str
    intake_document_id: str | None = None
    standing_template_id: str | None = None
    overall_confidence: float | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    lines: list[OrderLineOut]
