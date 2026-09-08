from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import UUIDMixin


class ProductCategory(UUIDMixin):
    __tablename__ = "product_categories"

    name_en: Mapped[str] = mapped_column(String(100), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Unit(UUIDMixin):
    __tablename__ = "units"

    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # jin|kg|box|bag|piece
    name_en: Mapped[str] = mapped_column(String(50), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(50), nullable=False)


class Product(UUIDMixin):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name_en: Mapped[str] = mapped_column(String(200), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("product_categories.id"))
    default_unit_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("units.id"))
    shelf_life_days: Mapped[int | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category: Mapped[ProductCategory | None] = relationship()
    default_unit: Mapped[Unit | None] = relationship()
