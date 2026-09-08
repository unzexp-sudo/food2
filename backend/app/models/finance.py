from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDMixin


class Invoice(TimestampMixin):
    __tablename__ = "invoices"

    invoice_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customers.id"), index=True, nullable=False
    )
    order_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="draft", nullable=False
    )  # draft|issued|partial|paid|void
    total_amount: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    paid_amount: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime)

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceLine(UUIDMixin):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("invoices.id"), index=True, nullable=False
    )
    order_line_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("order_lines.id"))
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Payment(TimestampMixin):
    __tablename__ = "payments"

    number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    invoice_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("invoices.id"), index=True
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False)  # inbound|outbound
    counterparty: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str | None] = mapped_column(String(50))  # cash|bank_transfer|wechat|alipay|other
    paid_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
