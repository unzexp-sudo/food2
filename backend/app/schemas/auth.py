"""Auth + system Pydantic schemas (auth/system agent — see AGENT_CONTRACTS.md §5)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --- Shared user shape (never exposes password_hash) ---------------------------
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    role: str
    is_active: bool
    created_at: datetime


# --- auth endpoints ------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


# --- system: users -------------------------------------------------------------
class UserCreate(BaseModel):
    email: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    role: str
    password: str = Field(min_length=1)
    is_active: bool = True


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=1)


# --- system: audit logs --------------------------------------------------------
class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    entity_type: str
    entity_id: str
    action: str
    actor_id: str | None = None
    actor_name: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    summary: str | None = None
    created_at: datetime


# --- system: settings ----------------------------------------------------------
class SettingsUpdate(BaseModel):
    """Partial update of the settings map. Keys are arbitrary; values are JSON."""
    model_config = ConfigDict(extra="allow")

    auto_confirm: dict[str, Any] | None = None
    cutoff_time: str | None = None
    auto_invoice: dict[str, Any] | None = None
