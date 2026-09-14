"""Conversation → customer identity ledger (docs/IDENTITY_IMPLEMENTATION_SPEC.md §2).

Three rules drive everything here:

1. **Nothing auto-binds.** A chat is only bound when a human picks the
   customer, and that decision is recorded (`confirmed_by`, `confirmed_at`,
   evidence snapshot). We never infer a customer from a display name — it is
   user-editable and changes without notice.
2. **An unbound chat is a delay, a mis-bound chat is a disaster.** So unknown
   chats are *held* rather than guessed at, and the hold is released the moment
   a binding exists.
3. **The database enforces one customer per chat** (partial unique index on
   `customer_identities`), not this module. This code is a convenience layer on
   top of a guarantee it cannot grant.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import log_audit
from app.models import Customer, CustomerIdentity, IntakeDocument, Order, User
from app.models.identity import IDENTITY_KINDS

# --- document_meta["identity"] statuses --------------------------------------
BOUND = "bound"
UNBOUND = "unbound"

# How a document came to have a customer. "identity" is the only one that is an
# ERP binding; the rest are assertions inherited from elsewhere and are visible
# as such so nobody mistakes them for a checked bind.
METHOD_IDENTITY = "identity"
METHOD_UPSTREAM = "upstream_asserted"
METHOD_PARENT = "parent_inherited"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Chat keys ----------------------------------------------------------------

def chat_key(*, external_userid: str | None, chat_id: str | None) -> tuple[str, str] | None:
    """The (kind, value) we bind on. Prefer `chat_id`, else `external_userid`.

    Orders arrive in **group chats**, and the group is what belongs to the
    customer: its members change over time, the customer does not. Binding on
    `external_userid` instead would identify one person inside the room, so the
    same customer would fragment into a separate binding for every staff member
    who happened to write — and a mis-bound colleague would silently send
    somebody else's order to that customer.

    A 1:1 conversation has no `chat_id` and falls back to the contact.

    A display name is never acceptable for either: a customer can rename
    themselves at will, and binding on a name would let a rename silently
    redirect somebody else's orders.
    """
    if chat_id:
        return ("wecom_chat_id", str(chat_id))
    if external_userid:
        return ("wecom_external_userid", str(external_userid))
    return None


def _meta_status_expr():
    """SQL expression for document_meta['identity']['status'].

    MUST be `.as_string()`, not `cast(..., String)` — on SQLite the cast form
    compiles to `CAST(JSON_QUOTE(JSON_EXTRACT(...)) AS VARCHAR)`, and JSON_QUOTE
    wraps the value in literal double quotes so the comparison silently never
    matches. (Same trap as the WeCom msgid idempotency anchor.)
    """
    return IntakeDocument.document_meta["identity"]["status"].as_string()


def _meta_of(doc: IntakeDocument) -> dict:
    return dict(doc.document_meta or {})


def _set_meta(db: Session, doc: IntakeDocument, extra: dict) -> None:
    meta = _meta_of(doc)
    meta.update(extra)
    doc.document_meta = meta
    flag_modified(doc, "document_meta")
    db.flush()


def identity_block_of(doc: IntakeDocument) -> dict:
    return (doc.document_meta or {}).get("identity") or {}


def is_unbound(doc: IntakeDocument) -> bool:
    return identity_block_of(doc).get("status") == UNBOUND


# --- Resolve ------------------------------------------------------------------

def resolve_chat(
    db: Session,
    *,
    external_userid: str | None = None,
    chat_id: str | None = None,
) -> CustomerIdentity | None:
    """The confirmed identity for this conversation, or None.

    `chat_id` is tried first — the room belongs to the customer and survives its
    members changing — then `external_userid` for 1:1 conversations. Only
    `confirmed` rows count; a proposal is not a binding.
    """
    for kind, value in (("wecom_chat_id", chat_id),
                        ("wecom_external_userid", external_userid)):
        if not value:
            continue
        row = (
            db.query(CustomerIdentity)
            .filter(
                CustomerIdentity.kind == kind,
                CustomerIdentity.value == str(value),
                CustomerIdentity.status == "confirmed",
            )
            .first()
        )
        if row is not None:
            return row
    return None


def resolve_for_document(
    db: Session,
    doc: IntakeDocument,
    *,
    external_userid: str | None = None,
    chat_id: str | None = None,
    upstream_customer_id: str | None = None,
) -> dict:
    """Build the `document_meta["identity"]` block for a freshly ingested doc.

    Called once, at ingest. It never invents a customer: either an ERP binding
    exists, or somebody upstream asserted one, or the document is unbound and
    must be held.
    """
    key = chat_key(external_userid=external_userid, chat_id=chat_id)
    identity = resolve_chat(db, external_userid=external_userid, chat_id=chat_id)

    if identity is not None:
        # An ERP binding beats anything the gateway thinks — the ERP owns the
        # ledger, and "one chat, one customer" is enforced here.
        return {
            "status": BOUND,
            "identity_id": identity.id,
            "kind": identity.kind,
            "value": identity.value,
            "chat_key": f"{identity.kind}:{identity.value}",
            "method": METHOD_IDENTITY,
            "confirmed_by": identity.confirmed_by,
            "customer_id": identity.customer_id,
        }

    if doc.customer_id:
        # Not an ERP binding — an assertion we inherited (the gateway sending a
        # customer_id it was given earlier, or a reply re-using its parent's
        # customer). Recorded as such so a reviewer can see the difference
        # between "a human bound this chat here" and "something told us so".
        method = METHOD_UPSTREAM if upstream_customer_id else METHOD_PARENT
        return {
            "status": BOUND,
            "kind": key[0] if key else None,
            "value": key[1] if key else None,
            "chat_key": f"{key[0]}:{key[1]}" if key else None,
            "method": method,
            "confirmed_by": None,
            "customer_id": doc.customer_id,
        }

    return {
        "status": UNBOUND,
        "kind": key[0] if key else None,
        "value": key[1] if key else None,
        "chat_key": f"{key[0]}:{key[1]}" if key else None,
        "reason": "no_chat_key" if key is None else "no_confirmed_identity",
    }


def assert_document_bound(db: Session, doc: IntakeDocument) -> None:
    """Refuse to turn a held document into an order.

    The one gate that closes the hole: an order may not exist without an
    account, so there is no path from `unbound` to `confirmed` that does not
    pass through `POST /identity/bind`.
    """
    if is_unbound(doc):
        block = identity_block_of(doc)
        raise ValueError(
            "This conversation is not bound to a customer yet — "
            f"bind it first (chat_key={block.get('chat_key')}). "
            "An order without a confirmed customer is never created."
        )


# --- Held documents -----------------------------------------------------------

def _held_documents(db: Session) -> list[IntakeDocument]:
    return db.query(IntakeDocument).filter(_meta_status_expr() == UNBOUND).all()


def _held_for(db: Session, kind: str, value: str) -> list[IntakeDocument]:
    return [
        d for d in _held_documents(db)
        if (identity_block_of(d).get("kind") == kind
            and identity_block_of(d).get("value") == value)
    ]


def release_held(
    db: Session,
    identity: CustomerIdentity,
    *,
    actor: User | None = None,
) -> int:
    """Give every held document for this chat its customer. Returns the count.

    Bindings apply to future documents and to held documents on release; they
    never retroactively re-attribute an order that is already confirmed.
    """
    released = 0
    for doc in _held_for(db, identity.kind, identity.value):
        doc.customer_id = identity.customer_id
        db.flush()
        _set_meta(db, doc, {
            "identity": {
                "status": BOUND,
                "identity_id": identity.id,
                "kind": identity.kind,
                "value": identity.value,
                "chat_key": f"{identity.kind}:{identity.value}",
                "method": METHOD_IDENTITY,
                "confirmed_by": identity.confirmed_by,
                "customer_id": identity.customer_id,
                "released_by": getattr(actor, "id", None),
            }
        })
        released += 1
    return released


# --- Bind ---------------------------------------------------------------------

def bind_chat(
    db: Session,
    *,
    kind: str,
    value: str,
    customer_id: str,
    actor: User | None = None,
    evidence: dict | None = None,
) -> tuple[CustomerIdentity, int]:
    """A human binds one conversation to one customer.

    Returns (identity, released_document_count). Raises ValueError if the chat
    is already bound to a *different* customer — one chat may never point at
    two customers. The partial unique index is the real guard; the check here
    only turns the inevitable IntegrityError into a message someone can read.
    """
    if kind not in IDENTITY_KINDS:
        raise ValueError(f"Unknown identity kind: {kind}")
    value = (value or "").strip()
    if not value:
        raise ValueError("value is required")
    if db.get(Customer, customer_id) is None:
        raise ValueError(f"Customer not found: {customer_id}")

    existing = (
        db.query(CustomerIdentity)
        .filter(
            CustomerIdentity.kind == kind,
            CustomerIdentity.value == value,
            CustomerIdentity.status == "confirmed",
        )
        .first()
    )
    if existing is not None:
        if existing.customer_id != customer_id:
            raise ValueError(
                f"This conversation is already bound to customer {existing.customer_id}. "
                "Unbind it first — a chat may only ever point at one customer."
            )
        identity = existing
    else:
        # Re-use a proposal for the same chat if one exists, so the history of
        # "we suggested this before" is upgraded rather than duplicated.
        identity = (
            db.query(CustomerIdentity)
            .filter(
                CustomerIdentity.kind == kind,
                CustomerIdentity.value == value,
                CustomerIdentity.status != "confirmed",
            )
            .first()
        )
        if identity is None:
            identity = CustomerIdentity(kind=kind, value=value)
            db.add(identity)

        identity.customer_id = customer_id
        identity.status = "confirmed"
        identity.confirmed_by = getattr(actor, "id", None)
        identity.confirmed_at = _now()
        if evidence is not None:
            merged = dict(identity.evidence or {})
            merged.update(evidence)
            identity.evidence = merged

        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise ValueError(
                "This conversation is already bound to another customer — "
                "the database refused the second binding."
            )

    released = release_held(db, identity, actor=actor)

    log_audit(
        db, actor, "CustomerIdentity", identity.id, "bind",
        before=None,
        after={
            "kind": identity.kind,
            "value": identity.value,
            "customer_id": identity.customer_id,
            "status": identity.status,
            "released_documents": released,
        },
        summary=(
            f"Conversation {kind}:{value} bound to customer {customer_id} "
            f"({released} held document(s) released)"
        ),
    )
    db.flush()
    return identity, released


def unbind_identity(
    db: Session,
    identity_id: str,
    *,
    actor: User | None = None,
    reason: str | None = None,
) -> list[str]:
    """Reverse a binding. Returns the ids of every order that used it.

    Unbinding never rewrites history: confirmed orders keep the customer they
    were created with. It returns the list so a human can re-check them, which
    is the only honest thing to do with an order that used a wrong bind.
    """
    identity = db.get(CustomerIdentity, identity_id)
    if identity is None:
        raise ValueError(f"Identity not found: {identity_id}")
    if identity.status != "confirmed":
        raise ValueError(f"Identity {identity_id} is not bound (status={identity.status})")

    affected = orders_for_identity(db, identity)

    before = {
        "status": identity.status,
        "customer_id": identity.customer_id,
        "confirmed_by": identity.confirmed_by,
    }
    identity.status = "rejected"
    evidence = dict(identity.evidence or {})
    evidence["unbound_by"] = getattr(actor, "id", None)
    evidence["unbound_at"] = _now().isoformat()
    evidence["unbind_reason"] = reason
    identity.evidence = evidence
    db.flush()

    log_audit(
        db, actor, "CustomerIdentity", identity.id, "unbind",
        before=before,
        after={"status": identity.status, "affected_orders": affected},
        summary=(
            f"Conversation {identity.kind}:{identity.value} unbound from "
            f"customer {before['customer_id']} ({len(affected)} order(s) to re-check)"
        ),
    )
    db.flush()
    return affected


def orders_for_identity(db: Session, identity: CustomerIdentity) -> list[str]:
    """Every order created from this conversation, oldest first."""
    doc_ids = [
        d.id for d in _documents_for_chat(db, identity.kind, identity.value)
    ]
    if not doc_ids:
        return []
    rows = (
        db.query(Order.id)
        .filter(Order.intake_document_id.in_(doc_ids))
        .order_by(Order.created_at)
        .all()
    )
    return [r[0] for r in rows]


def _documents_for_chat(db: Session, kind: str, value: str) -> list[IntakeDocument]:
    """All intake documents that arrived from this conversation (bound or not)."""
    docs = []
    for d in db.query(IntakeDocument).all():
        block = identity_block_of(d)
        if block.get("kind") == kind and block.get("value") == value:
            docs.append(d)
    return docs


# --- Listing ------------------------------------------------------------------

def list_unbound_chats(db: Session) -> tuple[list[dict], int]:
    """One row per conversation waiting to be bound.

    Grouped by chat, not by message: "Chat X — 4 messages waiting" is the
    decision a human has to make, and four rows for it would just be noise.
    """
    groups: dict[str, dict] = {}
    for doc in _held_documents(db):
        block = identity_block_of(doc)
        kind = block.get("kind")
        value = block.get("value")
        key = block.get("chat_key") or f"unknown:{doc.id}"
        g = groups.setdefault(
            key,
            {
                "chat_key": key,
                "kind": kind,
                "value": value,
                "display_name": None,
                "corp_name": None,
                "alias": None,
                "waiting_count": 0,
                "last_message_at": None,
                "document_ids": [],
            },
        )
        g["waiting_count"] += 1
        g["document_ids"].append(doc.id)
        g["display_name"] = g["display_name"] or block.get("display_name")
        g["corp_name"] = g["corp_name"] or block.get("corp_name")
        g["alias"] = g.get("alias") or block.get("alias")

        # The WeCom receive time when we have it, else when the ERP recorded it.
        stamp = ((doc.document_meta or {}).get("wecom") or {}).get("received_at")
        at = stamp or (doc.created_at.isoformat() if doc.created_at else None)
        if at and (g["last_message_at"] is None or at > g["last_message_at"]):
            g["last_message_at"] = at

    items = sorted(
        groups.values(),
        key=lambda g: (g["last_message_at"] or ""),
        reverse=True,
    )
    return items, len(items)


def list_bindings(
    db: Session,
    *,
    customer_id: str | None = None,
) -> list[dict]:
    q = db.query(CustomerIdentity)
    if customer_id:
        q = q.filter(CustomerIdentity.customer_id == customer_id)
    rows = q.order_by(CustomerIdentity.created_at).all()
    return [binding_out(r) for r in rows]


def binding_out(row: CustomerIdentity) -> dict:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "kind": row.kind,
        "value": row.value,
        "status": row.status,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "evidence": row.evidence or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
