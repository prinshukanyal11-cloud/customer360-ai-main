"""Password hashing and JWT token handling."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode: dict[str, Any] = {"sub": subject, "type": "access", "exp": expire, "iat": datetime.now(timezone.utc)}
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def new_jti() -> str:
    """A unique token identifier, used to tie a refresh JWT to its stored row."""
    return uuid.uuid4().hex


def create_refresh_token(subject: str, jti: str | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.refresh_token_expire_minutes)
    to_encode = {
        "sub": subject,
        "type": "refresh",
        # Without a jti the token cannot be matched to the row that makes it
        # revocable, so it is always present.
        "jti": jti or new_jti(),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise ValueError("Invalid or expired token.") from None
    if payload.get("type") != expected_type:
        raise ValueError("Unexpected token type.")
    # Callers do int(payload["sub"]) to load the user. Validating the subject
    # here means a token with a missing or non-numeric sub is rejected as the
    # auth failure it is, instead of raising out of the dependency and surfacing
    # as a 500 with a stack trace.
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.isdigit() or int(subject) <= 0:
        raise ValueError("Invalid token subject.")
    return payload
