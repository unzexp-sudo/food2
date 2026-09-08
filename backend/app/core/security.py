"""Password hashing (PBKDF2, stdlib) and JWT helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import settings

_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, AttributeError):
        return False


def create_access_token(user_id: str, role: str, expires_minutes: int | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload: dict[str, Any] = {"sub": user_id, "role": role, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError on invalid/expired tokens."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
