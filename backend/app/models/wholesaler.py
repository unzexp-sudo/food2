from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampMixin, UUIDMixin


class Wholesaler(TimestampMixin):
    __tablename__ = "wholesalers"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name_en: Mapped[str] = mapped_column(String(200), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(100))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProductWholesalerMapping(UUIDMixin):
    __tablename__ = "product_wholesaler_mapping"

    product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id"), index=True, nullable=False
    )
    wholesaler_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wholesalers.id"), index=True, nullable=False
    )
    supplier_sku: Mapped[str | None] = mapped_column(String(50))
    cost_price: Mapped[float | None] = mapped_column(Float)


class SupplierRule(UUIDMixin):
    __tablename__ = "supplier_rules"

    category_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("product_categories.id"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("products.id"), index=True
    )
    wholesaler_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wholesalers.id"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    moq: Mapped[float | None] = mapped_column(Float)  # minimum order quantity
    lead_time_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
