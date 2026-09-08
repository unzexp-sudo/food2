"""Model mixins & helpers. Schema is FIXED — see docs/AGENT_CONTRACTS.md."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)


class UUIDMixin(Base):
    __abstract__ = True

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)


class TimestampMixin(UUIDMixin):
    __abstract__ = True

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
