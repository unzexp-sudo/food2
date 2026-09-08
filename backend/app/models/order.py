from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDMixin


class Order(TimestampMixin):
    __tablename__ = "orders"

    order_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customers.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default="draft", index=True, nullable=False
    )  # draft|pending_confirmation|needs_clarification|confirmed|consolidated|fulfilled|invoiced|rejected
    delivery_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)  # text|image|pdf|excel|email_body|manual|standing
    intake_document_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("intake_documents.id"))
    standing_template_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("standing_order_templates.id")
    )
    overall_confidence: Mapped[float | None] = mapped_column(Float)
    confirmed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.line_no"
    )


class OrderLine(UUIDMixin):
    __tablename__ = "order_lines"

    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders.id"), index=True, nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)  # original customer wording
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id"), index=True)
    product_display: Mapped[str | None] = mapped_column(String(200))  # shown when unmatched
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("units.id"))
    unit_price: Mapped[float | None] = mapped_column(Float)  # locked at confirm time
    confidence: Mapped[float | None] = mapped_column(Float)  # 0–1
    match_method: Mapped[str | None] = mapped_column(String(30))  # alias_exact|catalog_exact|fuzzy|manual|unmatched

    order: Mapped[Order] = relationship(back_populates="lines")
    product: Mapped["Product | None"] = relationship()
    unit: Mapped["Unit | None"] = relationship()
