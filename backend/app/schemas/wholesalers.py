"""Pydantic schemas for the wholesalers module (master data agent).

Response shapes match docs/AGENT_CONTRACTS.md §4.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Wholesaler ---------------------------------------------------------------
class WholesalerCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name_en: str = Field(min_length=1, max_length=200)
    name_zh: str = Field(min_length=1, max_length=200)
    contact_name: str | None = None
    contact_phone: str | None = None
    is_active: bool = True


class WholesalerUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name_en: str | None = Field(default=None, min_length=1, max_length=200)
    name_zh: str | None = Field(default=None, min_length=1, max_length=200)
    contact_name: str | None = None
    contact_phone: str | None = None
    is_active: bool | None = None


class WholesalerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name_en: str
    name_zh: str
    contact_name: str | None = None
    contact_phone: str | None = None
    is_active: bool
    created_at: datetime


# --- ProductWholesalerMapping --------------------------------------------------
class MappingCreate(BaseModel):
    product_id: str = Field(min_length=1)
    wholesaler_id: str = Field(min_length=1)
    supplier_sku: str | None = None
    cost_price: float | None = None


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: str
    wholesaler_id: str
    supplier_sku: str | None = None
    cost_price: float | None = None


# --- SupplierRule -------------------------------------------------------------
class SupplierRuleCreate(BaseModel):
    category_id: str | None = None
    product_id: str | None = None
    wholesaler_id: str = Field(min_length=1)
    priority: int = 0
    moq: float | None = None
    lead_time_days: int = 1
    is_default: bool = False


class SupplierRuleUpdate(BaseModel):
    category_id: str | None = None
    product_id: str | None = None
    wholesaler_id: str | None = None
    priority: int | None = None
    moq: float | None = None
    lead_time_days: int | None = None
    is_default: bool | None = None


class SupplierRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category_id: str | None = None
    product_id: str | None = None
    wholesaler_id: str
    priority: int
    moq: float | None = None
    lead_time_days: int
    is_default: bool
