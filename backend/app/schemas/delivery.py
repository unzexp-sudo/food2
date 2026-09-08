"""Pydantic schemas for the delivery module.

Response shapes match docs/AGENT_CONTRACTS.md §4 (Delivery, ProofOfDelivery).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Deliveries ---------------------------------------------------------------
class DeliveryGenerateIn(BaseModel):
    delivery_date: date


class DeliveryAssignIn(BaseModel):
    driver_id: str


class DeliveryStatusIn(BaseModel):
    status: Literal["picked", "out_for_delivery"]


class DeliveryCompleteLineIn(BaseModel):
    delivery_line_id: str
    delivered_quantity: float = Field(ge=0)


class DeliveryCompleteIn(BaseModel):
    """JSON body for /complete (multipart fields mirror these names)."""

    lines: list[DeliveryCompleteLineIn] = Field(min_length=1)
    received_by: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None


class DeliveryLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_line_id: str
    product_name_en: str
    product_name_zh: str
    quantity: float
    delivered_quantity: float


class ProofOfDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    photo_url: str | None = None
    signature_url: str | None = None
    received_by: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    delivered_at: datetime | None = None


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    delivery_number: str
    order_id: str
    order_number: str | None = None
    customer_id: str
    customer_name_en: str
    customer_name_zh: str
    route: str | None = None
    driver_id: str | None = None
    driver_name: str | None = None
    status: str
    scheduled_date: date
    picked_at: datetime | None = None
    out_at: datetime | None = None
    delivered_at: datetime | None = None
    lines: list[DeliveryLineOut] = Field(default_factory=list)
    pod: ProofOfDeliveryOut | None = None
