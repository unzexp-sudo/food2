from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDMixin


class Quotation(TimestampMixin):
    """A customer-facing sales quotation (mirrors the Order pattern).

    Maps to the Guanmai "merchandise / in-sale" card: code = sales invoice id,
    customer = card title, lines = items sold, status = Activated badge.
    """

    __tablename__ = "quotations"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customers.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default="active", index=True, nullable=False
    )  # active|inactive
    external_name: Mapped[str | None] = mapped_column(String(200))  # shown to the customer
    service_time: Mapped[str] = mapped_column(String(50), default="default", nullable=False)
    pricing_cycle: Mapped[str | None] = mapped_column(String(30))  # daily|weekly|null
    tags: Mapped[str | None] = mapped_column(Text)  # JSON array of strings
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))

    lines: Mapped[list["QuotationLine"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan", order_by="QuotationLine.line_no"
    )


class QuotationLine(UUIDMixin):
    __tablename__ = "quotation_lines"

    quotation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quotations.id"), index=True, nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("products.id"), index=True
    )
    product_display: Mapped[str | None] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("units.id"))
    unit_price: Mapped[float | None] = mapped_column(Float)

    quotation: Mapped[Quotation] = relationship(back_populates="lines")
    product: Mapped["Product | None"] = relationship()
    unit: Mapped["Unit | None"] = relationship()
