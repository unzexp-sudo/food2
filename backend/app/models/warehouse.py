from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDMixin


class InboundReceipt(TimestampMixin):
    __tablename__ = "inbound_receipts"

    receipt_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    po_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("purchase_orders.id"), index=True, nullable=False
    )
    received_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="posted", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["InboundReceiptLine"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class InboundReceiptLine(UUIDMixin):
    __tablename__ = "inbound_receipt_lines"

    receipt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inbound_receipts.id"), index=True, nullable=False
    )
    po_line_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("purchase_order_lines.id"), nullable=False
    )
    quantity_received: Mapped[float] = mapped_column(Float, nullable=False)
    quantity_damaged: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    receipt: Mapped[InboundReceipt] = relationship(back_populates="lines")
    po_line: Mapped["PurchaseOrderLine"] = relationship()


class PickList(TimestampMixin):
    __tablename__ = "pick_lists"

    pick_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    delivery_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="open", nullable=False
    )  # open|picking|picked|cancelled

    lines: Mapped[list["PickLine"]] = relationship(
        back_populates="pick_list", cascade="all, delete-orphan"
    )


class PickLine(UUIDMixin):
    __tablename__ = "pick_lines"

    pick_list_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pick_lists.id"), index=True, nullable=False
    )
    order_line_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("order_lines.id"), index=True, nullable=False
    )
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)  # planned
    picked_quantity: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)  # open|picked|short

    pick_list: Mapped[PickList] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship()


class InventoryMovement(TimestampMixin):
    """Stock ledger.

    `TimestampMixin` (rather than plain `UUIDMixin`) is intentional: a stock
    ledger without a timestamp is unauditable, and it also gives the ledger
    endpoint a stable, chronological sort key. Ordering by the random UUID
    primary key made pagination non-deterministic — rows could repeat or
    vanish across pages, and the newest movement was not guaranteed to be on
    page 1.
    """

    __tablename__ = "inventory_movements"

    product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id"), index=True, nullable=False
    )
    quantity_delta: Mapped[float] = mapped_column(Float, nullable=False)  # +in / -out
    ref_type: Mapped[str | None] = mapped_column(String(50))  # inbound_receipt|pick|loss
    ref_id: Mapped[str | None] = mapped_column(String(36))
    note: Mapped[str | None] = mapped_column(Text)
