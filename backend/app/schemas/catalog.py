"""Pydantic schemas for the catalog module (master data agent).

Response shapes match docs/AGENT_CONTRACTS.md §4.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- ProductCategory ----------------------------------------------------------
class CategoryCreate(BaseModel):
    name_en: str = Field(min_length=1, max_length=100)
    name_zh: str = Field(min_length=1, max_length=100)
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    name_zh: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name_en: str
    name_zh: str
    is_active: bool


# --- Unit ---------------------------------------------------------------------
class UnitCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name_en: str = Field(min_length=1, max_length=50)
    name_zh: str = Field(min_length=1, max_length=50)


class UnitUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=20)
    name_en: str | None = Field(default=None, min_length=1, max_length=50)
    name_zh: str | None = Field(default=None, min_length=1, max_length=50)


class UnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name_en: str
    name_zh: str


# --- Product ------------------------------------------------------------------
class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    name_en: str = Field(min_length=1, max_length=200)
    name_zh: str = Field(min_length=1, max_length=200)
    category_id: str | None = None
    default_unit_id: str | None = None
    shelf_life_days: int | None = None
    is_active: bool = True


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=50)
    name_en: str | None = Field(default=None, min_length=1, max_length=200)
    name_zh: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: str | None = None
    default_unit_id: str | None = None
    shelf_life_days: int | None = None
    is_active: bool | None = None


class ProductOut(BaseModel):
    """Product with embedded category names and default unit code (§4)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    sku: str
    name_en: str
    name_zh: str
    category_id: str | None = None
    category_name_en: str | None = None
    category_name_zh: str | None = None
    default_unit_id: str | None = None
    default_unit_code: str | None = None
    shelf_life_days: int | None = None
    is_active: bool


# --- ProductWholesalerMapping (returned by GET /products/{id}/wholesalers) ----
class ProductMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: str
    wholesaler_id: str
    supplier_sku: str | None = None
    cost_price: float | None = None
