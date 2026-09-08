"""Pydantic schemas for the quotations module.

Response shapes mirror docs/AGENT_CONTRACTS.md §4 (Order) for consistency.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- Quotation line payloads -------------------------------------------------
class QuotationLineCreate(BaseModel):
    product_id: str | None = None
    product_display: str | None = Field(default=None, max_length=200)
    quantity: float = Field(gt=0)
    unit_id: str | None = None
    unit_price: float | None = None


class QuotationLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    quotation_id: str
    line_no: int
    product_id: str | None = None
    product_display: str | None = None
    quantity: float
    unit_id: str | None = None
    unit_code: str | None = None
    unit_price: float | None = None


# --- Quotation payloads ------------------------------------------------------
class QuotationCreate(BaseModel):
    customer_id: str
    external_name: str | None = None
    service_time: str = "default"
    pricing_cycle: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    lines: list[QuotationLineCreate] = Field(min_length=1)


class QuotationUpdate(BaseModel):
    customer_id: str | None = None
    status: str | None = None
    external_name: str | None = None
    service_time: str | None = None
    pricing_cycle: str | None = None
    tags: list[str] | None = None
    description: str | None = None


class QuotationLineReplace(BaseModel):
    lines: list[QuotationLineCreate] = Field(min_length=1)


class QuotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    customer_id: str
    customer_name_en: str
    customer_name_zh: str
    status: str
    external_name: str | None = None
    service_time: str
    pricing_cycle: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    line_count: int
    created_at: datetime
    lines: list[QuotationLineOut] = Field(default_factory=list)
