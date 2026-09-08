from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDMixin, utcnow


class AuditLog(UUIDMixin):
    __tablename__ = "audit_logs"

    entity_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # create|update|delete|confirm|reject|send|receive|complete|...
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_name: Mapped[str | None] = mapped_column(String(100))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SystemSetting(UUIDMixin):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
