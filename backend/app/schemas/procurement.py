"""Pydantic schemas for the procurement module (consolidation + POs).

Response shapes match docs/AGENT_CONTRACTS.md §4.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Consolidation ------------------------------------------------------------
class ConsolidationRunIn(BaseModel):
    delivery_date: date


class ConsolidationBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    batch_number: str
    delivery_date: date
    cutoff_at: datetime
    status: str
    order_count: int
    exception_count: int
    created_by: str | None = None
    created_at: datetime


class ConsolidationExceptionOut(BaseModel):
    order_id: str
    order_number: str | None = None
    order_line_id: str
    product_display: str | None = None
    reason: str


class PurchaseOrderSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    po_number: str
    wholesaler_id: str
    wholesaler_name_en: str
    wholesaler_name_zh: str
    category_id: str | None = None
    category_name_en: str | None = None
    category_name_zh: str | None = None
    batch_id: str | None = None
    status: str
    total_amount: float
    sent_at: datetime | None = None
    notes: str | None = None
    created_at: datetime


class ConsolidationBatchDetailOut(ConsolidationBatchOut):
    purchase_orders: list[PurchaseOrderSummaryOut] = Field(default_factory=list)
    exceptions: list[ConsolidationExceptionOut] = Field(default_factory=list)


class ConsolidationRunResult(BaseModel):
    batch: ConsolidationBatchOut
    purchase_orders: list[PurchaseOrderSummaryOut]
    exceptions: list[ConsolidationExceptionOut]


# --- Purchase orders ----------------------------------------------------------
class PurchaseOrderLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    po_id: str
    product_id: str
    product_name_en: str
    product_name_zh: str
    quantity_ordered: float
    unit_id: str | None = None
    unit_code: str | None = None
    cost_price: float | None = None
    quantity_received: float
    source_orders: list[str] = Field(default_factory=list)


class PurchaseOrderDetailOut(PurchaseOrderSummaryOut):
    lines: list[PurchaseOrderLineOut] = Field(default_factory=list)


class PurchaseOrderLineEdit(BaseModel):
    id: str
    quantity_ordered: float | None = Field(default=None, gt=0)
    cost_price: float | None = Field(default=None, ge=0)


class PurchaseOrderLinesPatch(BaseModel):
    lines: list[PurchaseOrderLineEdit] = Field(min_length=1)
