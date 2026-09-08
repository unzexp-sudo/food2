from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDMixin


class Delivery(TimestampMixin):
    __tablename__ = "deliveries"

    delivery_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders.id"), index=True, nullable=False
    )
    route: Mapped[str | None] = mapped_column(String(100))  # zone from customer, editable
    driver_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(30), default="scheduled", index=True, nullable=False
    )  # scheduled|picked|out_for_delivery|delivered|failed|partial
    scheduled_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    picked_at: Mapped[datetime | None] = mapped_column(DateTime)
    out_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))

    lines: Mapped[list["DeliveryLine"]] = relationship(
        back_populates="delivery", cascade="all, delete-orphan"
    )
    pod: Mapped["ProofOfDelivery | None"] = relationship(
        back_populates="delivery", cascade="all, delete-orphan", uselist=False
    )


class DeliveryLine(UUIDMixin):
    __tablename__ = "delivery_lines"

    delivery_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deliveries.id"), index=True, nullable=False
    )
    order_line_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("order_lines.id"), index=True, nullable=False
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False)  # planned from pick
    delivered_quantity: Mapped[float] = mapped_column(Float, default=0, nullable=False)

    delivery: Mapped[Delivery] = relationship(back_populates="lines")


class ProofOfDelivery(UUIDMixin):
    __tablename__ = "proof_of_delivery"

    delivery_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deliveries.id"), unique=True, nullable=False
    )
    photo_path: Mapped[str | None] = mapped_column(String(1000))
    signature_path: Mapped[str | None] = mapped_column(String(1000))
    received_by: Mapped[str | None] = mapped_column(String(100))
    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_lng: Mapped[float | None] = mapped_column(Float)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)

    delivery: Mapped[Delivery] = relationship(back_populates="pod")
