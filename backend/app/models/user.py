from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin


class User(TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # admin|ops|warehouse|finance|driver
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
