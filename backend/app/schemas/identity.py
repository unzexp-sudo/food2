"""Payloads for conversation binding and delivery confirmation.

Kept in one new file so this work does not collide with the module-owned
schema files (`schemas/customers.py`, `schemas/orders.py`).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class IdentityBind(BaseModel):
    """POST /identity/bind — one conversation, one customer, one human."""

    kind: str = Field(description="wecom_external_userid | wecom_chat_id")
    value: str = Field(min_length=1, max_length=200)
    customer_id: str
    # Optional snapshot of what the human saw (contact name, corp, phone,
    # msgid, document_id). Stored verbatim as the evidence of the decision.
    evidence: dict | None = None


class IdentityUnbind(BaseModel):
    """POST /identity/{id}/unbind — reversible, and always explained."""

    reason: str | None = Field(default=None, max_length=1000)


class DeliveryConfirm(BaseModel):
    """POST /orders/{id}/confirm-delivery.

    `delivery_address` may be omitted only when the order already carries one
    (pre-filled from the customer) — but supplying it again is how a human
    corrects a pre-fill, and either way the *confirmation* is the timestamp.
    """

    delivery_address: str | None = Field(default=None, max_length=1000)
    contact_name: str | None = Field(default=None, max_length=100)
    contact_phone: str | None = Field(default=None, max_length=50)


class AddressVerify(BaseModel):
    """POST /customers/{id}/verify-address — a person checked this address."""

    address: str = Field(min_length=1, max_length=500)
    contact_name: str | None = Field(default=None, max_length=100)
    contact_phone: str | None = Field(default=None, max_length=50)
    delivery_zone: str | None = Field(default=None, max_length=100)
