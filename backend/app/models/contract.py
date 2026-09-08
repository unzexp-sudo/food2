from __future__ import annotations

from datetime import date

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDMixin


class ContractPrice(UUIDMixin):
    __tablename__ = "contract_prices"

    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customers.id"), index=True, nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id"), index=True, nullable=False
    )
    unit_id: Mapped[str] = mapped_column(String(36), ForeignKey("units.id"), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)


class StandingOrderTemplate(TimestampMixin):
    __tablename__ = "standing_order_templates"

    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customers.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    delivery_days: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["mon","thu"]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    lines: Mapped[list["StandingOrderTemplateLine"]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class StandingOrderTemplateLine(UUIDMixin):
    __tablename__ = "standing_order_template_lines"

    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("standing_order_templates.id"), index=True, nullable=False
    )
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), ForeignKey("units.id"), nullable=False)

    template: Mapped[StandingOrderTemplate] = relationship(back_populates="lines")
