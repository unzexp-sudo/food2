from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDMixin


class Customer(TimestampMixin):
    __tablename__ = "customers"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name_en: Mapped[str] = mapped_column(String(200), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(30), default="other", nullable=False)  # school|restaurant|canteen|other
    contact_name: Mapped[str | None] = mapped_column(String(100))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(String(500))
    delivery_zone: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    contacts: Mapped[list["CustomerContact"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    aliases: Mapped[list["CustomerProductAlias"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class CustomerContact(UUIDMixin):
    __tablename__ = "customer_contacts"

    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customers.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
    role: Mapped[str | None] = mapped_column(String(100))

    customer: Mapped[Customer] = relationship(back_populates="contacts")


class CustomerProductAlias(UUIDMixin):
    __tablename__ = "customer_product_aliases"
    __table_args__ = (UniqueConstraint("customer_id", "alias", name="uq_alias_customer"),)

    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customers.id"), index=True, nullable=False
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="aliases")
