"""System module schemas (kept in app/schemas/auth.py to avoid duplication).

This module simply re-exports the system-related schemas so callers can import
from a stable path. The canonical definitions live in app/schemas/auth.py
because the auth and system routers share UserOut and the same Pydantic patterns.
"""
from __future__ import annotations

from app.schemas.auth import (
    AuditLogOut,
    SettingsUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)

__all__ = [
    "AuditLogOut",
    "SettingsUpdate",
    "UserCreate",
    "UserOut",
    "UserUpdate",
]
