"""Pydantic schemas for the warehouse module (inbound receipts, pick lists, inventory).

Response shapes match docs/AGENT_CONTRACTS.md §4 (InboundReceipt, PickList, InventoryMovement).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Inbound receipts ---------------------------------------------------------
class InboundReceiptLineIn(BaseModel):
    po_line_id: str
    quantity_received: float = Field(gt=0)
    quantity_damaged: float = Field(default=0, ge=0)
    notes: str | None = None


class InboundReceiptCreate(BaseModel):
    po_id: str
    lines: list[InboundReceiptLineIn] = Field(min_length=1)


class InboundReceiptLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    po_line_id: str
    product_name_en: str
    product_name_zh: str
    quantity_ordered: float
    quantity_received: float
    quantity_damaged: float
    discrepancy: bool
    notes: str | None = None


class InboundReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    receipt_number: str
    po_id: str
    po_number: str | None = None
    received_by: str | None = None
    received_at: datetime
    status: str
    notes: str | None = None
    lines: list[InboundReceiptLineOut] = Field(default_factory=list)


# PO received summary (GET /purchase-orders/{id}/received)
class POReceivedLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    po_line_id: str
    product_id: str
    product_name_en: str
    product_name_zh: str
    quantity_ordered: float
    quantity_received: float
    quantity_damaged: float
    discrepancy: bool


class POReceivedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    po_id: str
    po_number: str
    status: str
    total_ordered: float
    total_received: float
    total_damaged: float
    lines: list[POReceivedLineOut] = Field(default_factory=list)
    receipts: list[InboundReceiptOut] = Field(default_factory=list)


# --- Pick lists ---------------------------------------------------------------
class PickListGenerateIn(BaseModel):
    delivery_date: date


class PickLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    order_number: str | None = None
    customer_name_en: str | None = None
    customer_name_zh: str | None = None
    product_id: str
    product_name_en: str
    product_name_zh: str
    quantity: float  # planned
    picked_quantity: float
    status: str


class PickListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    pick_number: str
    delivery_date: date
    status: str
    lines: list[PickLineOut] = Field(default_factory=list)


class PickLinePickIn(BaseModel):
    picked_quantity: float = Field(ge=0)


# --- Inventory ----------------------------------------------------------------
class InventoryLossIn(BaseModel):
    product_id: str
    quantity: float = Field(gt=0)
    reason: str = Field(min_length=1)


class InventoryAdjustIn(BaseModel):
    product_id: str
    quantity_delta: float  # signed: + in / - out
    reason: str = Field(min_length=1)


class InventoryMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: str
    product_name_en: str | None = None
    product_name_zh: str | None = None
    quantity_delta: float
    ref_type: str | None = None
    ref_id: str | None = None
    note: str | None = None
