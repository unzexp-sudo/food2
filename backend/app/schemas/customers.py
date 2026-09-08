"""Pydantic schemas for the customers module (master data agent).

Response shapes match docs/AGENT_CONTRACTS.md §4.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Customer -----------------------------------------------------------------
class CustomerBase(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name_en: str = Field(min_length=1, max_length=200)
    name_zh: str = Field(min_length=1, max_length=200)
    type: Literal["school", "restaurant", "canteen", "other"] = "other"
    contact_name: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    delivery_zone: str | None = None
    notes: str | None = None
    status: Literal["active", "inactive"] = "active"


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name_en: str | None = Field(default=None, min_length=1, max_length=200)
    name_zh: str | None = Field(default=None, min_length=1, max_length=200)
    type: Literal["school", "restaurant", "canteen", "other"] | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    delivery_zone: str | None = None
    notes: str | None = None
    status: Literal["active", "inactive"] | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name_en: str
    name_zh: str
    type: str
    contact_name: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    delivery_zone: str | None = None
    notes: str | None = None
    status: str
    created_at: datetime


# --- CustomerContact ----------------------------------------------------------
class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str | None = None
    role: str | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    name: str
    phone: str | None = None
    role: str | None = None


# --- CustomerProductAlias -----------------------------------------------------
class AliasCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=200)
    product_id: str = Field(min_length=1)


class AliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    alias: str
    product_id: str
