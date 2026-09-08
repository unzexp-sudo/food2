"""SYSTEM MODULE — owner: auth/system agent.

Endpoints (see docs/AGENT_CONTRACTS.md §5):
  GET/POST          /users                 list (paged, filter q/role/is_active) & create   R: admin
  GET/PATCH/DELETE  /users/{id}            retrieve/update/deactivate                          R: admin
  GET               /audit-logs            paged; filters entity_type, entity_id, actor_id      R: admin, ops
  GET/PUT           /settings              system settings map                                   R: admin
  GET               /settings/public       subset for any logged-in user (cutoff_time, auto_confirm)

- Never return password_hash. Creating a user hashes via app.core.security.hash_password.
- DELETE = soft delete (is_active=False), not row removal.
- Settings live in the SystemSetting table as key/value rows. PUT accepts partial
  dict, upserts rows. GET returns a merged dict {key: value}.
- Audit-log user changes and settings changes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.core.pagination import clamp_page, page_response
from app.models import User
from app.schemas.auth import (
    AuditLogOut,
    SettingsUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services.system import (
    deactivate_user,
    get_all_settings,
    get_public_settings,
    get_user,
    list_audit_logs,
    list_users,
    update_user,
    upsert_settings,
    user_to_dict,
    validate_role,
)
from app.services.system import create_user as svc_create_user

router = APIRouter(prefix="/api/v1", tags=["system"])


def _user_out(user: User) -> UserOut:
    return UserOut.model_validate(user_to_dict(user))


# --- users --------------------------------------------------------------------
@router.get("/users", response_model=None)
def list_users_endpoint(
    q: str | None = Query(default=None, description="Search name/email"),
    role: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    page, page_size = clamp_page(page, page_size)
    users, total = list_users(db, q=q, role=role, is_active=is_active)
    items = [_user_out(u).model_dump(mode="json") for u in users]
    return page_response(items, total, page, page_size)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user_endpoint(
    payload: UserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("admin")),
):
    try:
        user = svc_create_user(
            db,
            email=payload.email,
            name=payload.name,
            role=payload.role,
            password=payload.password,
            is_active=payload.is_active,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _user_out(user)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user_endpoint(
    user_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    user = get_user(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return _user_out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user_endpoint(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("admin")),
):
    user = get_user(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    try:
        update_user(
            db,
            user,
            name=payload.name,
            role=payload.role,
            is_active=payload.is_active,
            password=payload.password,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return _user_out(user)


@router.delete("/users/{user_id}", response_model=UserOut)
def delete_user_endpoint(
    user_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("admin")),
):
    """Soft delete — is_active=False, row remains."""
    user = get_user(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    deactivate_user(db, user, actor=actor)
    db.commit()
    return _user_out(user)


# --- audit logs ---------------------------------------------------------------
@router.get("/audit-logs", response_model=None)
def list_audit_logs_endpoint(
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "ops")),
):
    page, page_size = clamp_page(page, page_size)
    logs, total = list_audit_logs(
        db, entity_type=entity_type, entity_id=entity_id, actor_id=actor_id
    )
    items = [
        AuditLogOut.model_validate(
            {
                "id": log.id,
                "entity_type": log.entity_type,
                "entity_id": log.entity_id,
                "action": log.action,
                "actor_id": log.actor_id,
                "actor_name": log.actor_name,
                "before": log.before,
                "after": log.after,
                "summary": log.summary,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
        ).model_dump(mode="json")
        for log in logs
    ]
    return page_response(items, total, page, page_size)


# --- settings -----------------------------------------------------------------
@router.get("/settings", response_model=None)
def get_settings_endpoint(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    return get_all_settings(db)


@router.put("/settings", response_model=None)
def put_settings_endpoint(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("admin")),
):
    """Partial update: upsert each provided key. Audit each change."""
    updates: dict[str, Any] = {
        k: v
        for k, v in payload.model_dump(exclude_unset=True).items()
        if v is not None
    }
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No settings provided")
    result = upsert_settings(db, updates, actor=actor)
    db.commit()
    return result


@router.get("/settings/public", response_model=None)
def get_public_settings_endpoint(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Any logged-in user: returns {auto_confirm, cutoff_time} only."""
    return get_public_settings(db)


# Expose the validator at module level for callers/tests that want it.
__all__ = ["router", "validate_role"]
