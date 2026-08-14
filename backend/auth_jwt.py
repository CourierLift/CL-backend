"""Canonical password hashing and JWT implementation."""

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from .settings import settings


def _password_bytes(plain: str) -> bytes:
    encoded = plain.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return encoded


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_password_bytes(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(plain), hashed.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def create_access_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.CL_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "user_id": user_id,
        "role": role,
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(
        payload,
        settings.CL_SECRET_KEY,
        algorithm=settings.CL_JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> tuple[int, str]:
    try:
        data = jwt.decode(
            token,
            settings.CL_SECRET_KEY,
            algorithms=[settings.CL_JWT_ALGORITHM],
        )
        raw_user_id = data.get("sub") or data.get("user_id")
        if raw_user_id is None:
            raise JWTError("Missing user id")
        return int(raw_user_id), str(data.get("role", "customer"))
    except (JWTError, TypeError, ValueError) as exc:
        raise ValueError("Invalid or expired token") from exc
