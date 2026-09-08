"""System module service layer (auth/system agent).

Business logic for users, audit logs, and system settings. Services never
commit on their own unless ``commit=True`` is passed — the router owns the
transaction boundary, then calls ``db.commit()``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.security import hash_password
from app.models import AuditLog, SystemSetting, User

VALID_ROLES: frozenset[str] = frozenset(
    {"admin", "ops", "warehouse", "finance", "driver"}
)


# --- users --------------------------------------------------------------------
def user_to_dict(user: User) -> dict[str, Any]:
    """Public user representation — never includes password_hash."""
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def list_users(
    db: Session,
    *,
    q: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> tuple[list[User], int]:
    query = db.query(User)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(User.email.ilike(like), User.name.ilike(like))
        )
    if role:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active.is_(is_active))
    total = query.count()
    return query.all(), total


def get_user(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def validate_role(role: str) -> None:
    if role not in VALID_ROLES:
        raise ValueError(
            f"Invalid role '{role}'. Must be one of: {sorted(VALID_ROLES)}"
        )


def create_user(
    db: Session,
    *,
    email: str,
    name: str,
    role: str,
    password: str,
    is_active: bool = True,
    actor: User | None = None,
    commit: bool = False,
) -> User:
    validate_role(role)
    if db.query(User).filter(User.email == email).first() is not None:
        raise ValueError(f"Email already registered: {email}")
    user = User(
        email=email,
        name=name,
        role=role,
        password_hash=hash_password(password),
        is_active=is_active,
    )
    db.add(user)
    db.flush()
    log_audit(
        db, actor, "User", user.id, "create",
        before=None, after=user_to_dict(user),
        summary=f"Created user {user.email} (role {user.role})",
    )
    if commit:
        db.commit()
    return user


def update_user(
    db: Session,
    user: User,
    *,
    name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    password: str | None = None,
    actor: User | None = None,
    commit: bool = False,
) -> User:
    before = user_to_dict(user)
    if name is not None:
        user.name = name
    if role is not None:
        validate_role(role)
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    if password is not None:
        user.password_hash = hash_password(password)
    db.flush()
    log_audit(
        db, actor, "User", user.id, "update",
        before=before, after=user_to_dict(user),
        summary=f"Updated user {user.email}",
    )
    if commit:
        db.commit()
    return user


def deactivate_user(
    db: Session,
    user: User,
    *,
    actor: User | None = None,
    commit: bool = False,
) -> User:
    """Soft delete — flip is_active to False. Never remove the row."""
    before = user_to_dict(user)
    user.is_active = False
    db.flush()
    log_audit(
        db, actor, "User", user.id, "delete",
        before=before, after=user_to_dict(user),
        summary=f"Deactivated user {user.email}",
    )
    if commit:
        db.commit()
    return user


# --- audit logs ---------------------------------------------------------------
def list_audit_logs(
    db: Session,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: str | None = None,
) -> tuple[list[AuditLog], int]:
    query = db.query(AuditLog)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.filter(AuditLog.entity_id == entity_id)
    if actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    # Newest first
    query = query.order_by(AuditLog.created_at.desc())
    total = query.count()
    return query.all(), total


# --- settings -----------------------------------------------------------------
def get_all_settings(db: Session) -> dict[str, Any]:
    rows = db.query(SystemSetting).all()
    return {row.key: row.value for row in rows}


def upsert_settings(
    db: Session,
    updates: dict[str, Any],
    *,
    actor: User | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """Upsert each key row from a partial dict. Returns the new merged map."""
    before_map = get_all_settings(db)
    for key, value in updates.items():
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if row is None:
            row = SystemSetting(key=key, value=value)
            db.add(row)
        else:
            row.value = value
        db.flush()
        log_audit(
            db, actor, "SystemSetting", row.id, "update",
            before={key: before_map.get(key)},
            after={key: value},
            summary=f"Updated setting '{key}'",
        )
    if commit:
        db.commit()
    return get_all_settings(db)


def get_public_settings(db: Session) -> dict[str, Any]:
    """Subset any logged-in user may see: auto_confirm, cutoff_time only."""
    all_settings = get_all_settings(db)
    public: dict[str, Any] = {}
    if "auto_confirm" in all_settings:
        public["auto_confirm"] = all_settings["auto_confirm"]
    if "cutoff_time" in all_settings:
        public["cutoff_time"] = all_settings["cutoff_time"]
    return public
