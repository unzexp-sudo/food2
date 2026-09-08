"""Audit trail helper. Adds an AuditLog row; the caller's commit persists it."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def log_audit(
    db: Session,
    actor: User | None,
    entity_type: str,
    entity_id: str,
    action: str,
    before: Any = None,
    after: Any = None,
    summary: str | None = None,
) -> AuditLog:
    row = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_id=actor.id if actor else None,
        actor_name=actor.name if actor else None,
        before=before,
        after=after,
        summary=summary,
    )
    db.add(row)
    db.flush()
    return row
