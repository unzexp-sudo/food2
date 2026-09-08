from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDMixin


class ConsolidationBatch(TimestampMixin):
    __tablename__ = "consolidation_batches"

    batch_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    delivery_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)  # open|closed
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))

    lines: Mapped[list["ConsolidationBatchLine"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ConsolidationBatchLine(UUIDMixin):
    __tablename__ = "consolidation_batch_lines"
    __table_args__ = (UniqueConstraint("batch_id", "order_line_id", name="uq_batch_order_line"),)

    batch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("consolidation_batches.id"), index=True, nullable=False
    )
    order_line_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("order_lines.id"), index=True, nullable=False
    )
    purchase_order_line_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("purchase_order_lines.id")
    )

    batch: Mapped[ConsolidationBatch] = relationship(back_populates="lines")
    order_line: Mapped["OrderLine"] = relationship()
    purchase_order_line: Mapped["PurchaseOrderLine | None"] = relationship()


class PurchaseOrder(TimestampMixin):
    __tablename__ = "purchase_orders"

    po_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    wholesaler_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wholesalers.id"), index=True, nullable=False
    )
    category_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("product_categories.id"))
    batch_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("consolidation_batches.id"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(30), default="draft", index=True, nullable=False
    )  # draft|sent|partially_received|received|closed|cancelled
    total_amount: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="po", cascade="all, delete-orphan"
    )


class PurchaseOrderLine(UUIDMixin):
    __tablename__ = "purchase_order_lines"

    po_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("purchase_orders.id"), index=True, nullable=False
    )
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=False)
    quantity_ordered: Mapped[float] = mapped_column(Float, nullable=False)
    unit_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("units.id"))
    cost_price: Mapped[float | None] = mapped_column(Float)
    quantity_received: Mapped[float] = mapped_column(Float, default=0, nullable=False)

    po: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship()
