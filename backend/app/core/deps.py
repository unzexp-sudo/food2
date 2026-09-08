"""Auth dependencies: current user, role guards, service-key guard."""
from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

import jwt as pyjwt
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models import User

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_roles(*roles: str):
    """Dependency factory. 'admin' always passes."""

    def guard(user: User = Depends(get_current_user)) -> User:
        if user.role == "admin" or user.role in roles:
            return user
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Requires role: {', '.join(roles)}",
        )

    return guard


def _user_from_credentials(
    credentials: HTTPAuthorizationCredentials | None,
    db: Session,
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_service_or_roles(*roles: str):
    """Accept EITHER the shared service key OR a JWT with one of `roles`.

    Used by the WeCom intake endpoints, which the Gateway calls with
    `X-ERP-Service-Key` but staff may also exercise from the UI.

    Returns the acting `User`, or `None` for a service-key caller (system actor).
    """

    def guard(
        request: Request,
        db: Session = Depends(get_db),
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> User | None:
        provided = request.headers.get("X-ERP-Service-Key") or ""
        if settings.service_key and hmac.compare_digest(provided, settings.service_key):
            return None
        user = _user_from_credentials(credentials, db)
        if user.role == "admin" or user.role in roles:
            return user
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Requires role: {', '.join(roles)}",
        )

    return guard
