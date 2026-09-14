"""IDENTITY MODULE — conversation → customer binding (spec §2.4).

  GET  /api/v1/identity/unbound                 R: ops/admin
  POST /api/v1/identity/bind                    R: ops/admin
  GET  /api/v1/identity/bindings                R: ops/admin
  POST /api/v1/identity/{identity_id}/unbind    R: ops/admin

Every binding here is a human decision. Nothing in this module guesses a
customer: a display name is user-editable, so it is shown as evidence and never
used as a key.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.models import User
from app.schemas.identity import IdentityBind, IdentityUnbind
from app.services.identity import (
    bind_chat,
    binding_out,
    list_bindings,
    list_unbound_chats,
    unbind_identity,
)

router = APIRouter(prefix="/api/v1/identity", tags=["identity"])


@router.get("/unbound", response_model=None)
def list_unbound_endpoint(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "admin")),
):
    """Conversations waiting to be bound — one row per chat, not per message.

    "Chat X — 4 messages waiting, never bound" is a single decision; four rows
    would turn a queue into noise.
    """
    items, total = list_unbound_chats(db)
    return {"items": items, "total": total}


@router.post("/bind", response_model=None, status_code=status.HTTP_201_CREATED)
def bind_endpoint(
    payload: IdentityBind,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops", "admin")),
):
    """Bind one conversation to one customer and release everything held for it.

    The database refuses a second confirmed binding for the same chat, so this
    is the only place a conversation can start resolving — and it always has a
    name and a timestamp attached.
    """
    try:
        identity, released = bind_chat(
            db,
            kind=payload.kind,
            value=payload.value,
            customer_id=payload.customer_id,
            actor=actor,
            evidence=payload.evidence,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    out = binding_out(identity)
    out["released"] = released
    return out


@router.get("/bindings", response_model=None)
def list_bindings_endpoint(
    customer_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ops", "admin")),
):
    """Every binding we hold, including who made it and what they saw."""
    return list_bindings(db, customer_id=customer_id)


@router.post("/{identity_id}/unbind", response_model=None)
def unbind_endpoint(
    identity_id: str,
    payload: IdentityUnbind,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("ops", "admin")),
):
    """Reverse a binding and list every order that used it.

    History is never rewritten: confirmed orders keep the customer they were
    created with. The returned list is what a human has to re-check, and it is
    the reason an unbind is safe to perform at all.
    """
    try:
        affected = unbind_identity(
            db, identity_id, actor=actor, reason=payload.reason
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return {"identity_id": identity_id, "affected_orders": affected}
