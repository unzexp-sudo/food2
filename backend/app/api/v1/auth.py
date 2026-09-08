"""AUTH MODULE — owner: auth/system agent.

Endpoints (see docs/AGENT_CONTRACTS.md §5):
  POST /login              {email, password} → {token, user}                (public)
  GET  /me                 → User                                            (any)
  POST /change-password    {old_password, new_password}                      (any)

- Login verifies password via app.core.security.verify_password, returns
  create_access_token(user.id, user.role). Audit-logs logins.
- 401 on bad credentials. Inactive users cannot log in.
- User responses never include password_hash.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserOut,
)
from app.services.system import user_to_dict

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _user_response(user: User) -> UserOut:
    return UserOut.model_validate(user_to_dict(user))


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Public login. Returns token + user. Inactive users get 401."""
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        # Don't leak whether the email exists. Audit only when the user exists.
        if user is not None:
            log_audit(
                db, None, "User", user.id, "login_failed",
                summary=f"Failed login for {user.email}",
            )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        log_audit(
            db, None, "User", user.id, "login_failed",
            summary=f"Inactive user login attempt: {user.email}",
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is inactive")
    token = create_access_token(user.id, user.role)
    log_audit(
        db, user, "User", user.id, "login",
        after=user_to_dict(user), summary=f"User {user.email} logged in",
    )
    db.commit()
    return LoginResponse(token=token, user=_user_response(user))


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return _user_response(current_user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verify old password, hash new, save, audit."""
    if not verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Old password is incorrect")
    before = user_to_dict(current_user)
    current_user.password_hash = hash_password(payload.new_password)
    db.flush()
    log_audit(
        db, current_user, "User", current_user.id, "update",
        before=before, after=user_to_dict(current_user),
        summary=f"User {current_user.email} changed password",
    )
    db.commit()
    return None
