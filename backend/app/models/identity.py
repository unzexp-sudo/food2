"""WeCom conversation → ERP customer bindings.

A WeCom chat has no shared key with an ERP customer until a human creates
one, so the mapping is a ledger of its own rather than a column on
`customers`. The invariant that matters is enforced by the database, not by
application code:

    UNIQUE (kind, value) WHERE status = 'confirmed'

Many chats may point at one customer (a buyer and their colleague); one chat
may NEVER point at two customers. A wrong bind sends goods and an invoice to
the wrong account, so this is the one place where we do not trust the
application to behave.

Nothing writes `status='confirmed'` on its own. Every confirmed row traces
back to a recorded human decision (`confirmed_by`, `confirmed_at`) and carries
the evidence snapshot that human was looking at.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampMixin

IDENTITY_KINDS = (
    "wecom_external_userid",  # 1:1 contact — stable per external contact
    "wecom_chat_id",          # group chat — stable even as members change
)

# Lifecycle: a binding is proposed by an extractor or an upstream system, and
# only ever becomes `confirmed` when a human says so. `rejected` is kept
# (rather than deleted) so a rejected chat is not re-proposed forever.
IDENTITY_STATUSES = ("proposed", "confirmed", "rejected")


class CustomerIdentity(TimestampMixin):
    __tablename__ = "customer_identities"

    __table_args__ = (
        # The whole point of the table. Partial indexes are supported by both
        # SQLite and PostgreSQL, so the "one chat → one customer" rule holds in
        # dev and in production alike. `proposed` rows are deliberately outside
        # the index: several proposals for the same chat are fine, two
        # confirmed bindings are not.
        Index(
            "uq_identity_confirmed",
            "kind",
            "value",
            unique=True,
            sqlite_where=text("status = 'confirmed'"),
            postgresql_where=text("status = 'confirmed'"),
        ),
    )

    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customers.id"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="proposed", nullable=False, index=True
    )
    confirmed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Snapshot of what the human actually saw: contact name, alias, corp name,
    # phone, msgid, document_id. A bind is reversible, so the reason it was
    # made has to survive the person who made it.
    evidence: Mapped[dict | None] = mapped_column(JSON, default=dict)
