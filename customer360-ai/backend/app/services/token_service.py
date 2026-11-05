"""Server-side refresh-token lifecycle: issue, rotate, revoke.

Why this exists
---------------
The refresh token is a JWT, which is verifiable but not revocable: whoever holds
a valid string can mint access tokens until `exp`. Logout therefore did nothing
security-wise. This module keeps a row per issued refresh token holding only a
SHA-256 of the token, which gives three properties a stateless token cannot:

* **Revocation** - logout marks the user's live tokens revoked.
* **Rotation with reuse detection** - a refresh mints a new token and revokes
  its predecessor. Presenting an already-revoked token means two parties hold the
  same string, i.e. a copy escaped, so the entire rotation family is revoked and
  both parties are forced to sign in again. This is the OAuth 2.0 security BCP
  recommendation and it is what turns theft from silent into loud.
* **Expiry enforcement independent of the token** - the row records `expires_at`
  so pruning cannot extend a token's life.

Storing a hash rather than the token means a database disclosure does not hand
the attacker usable credentials. SHA-256 (not bcrypt) is the right primitive here:
the input is a high-entropy random JWT, so there is nothing to brute-force, and a
memory-hard KDF on every refresh would only add latency.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.models.auth_tokens import RefreshToken

# Reasons are recorded rather than inferred later, so an operator reading the
# table can tell a normal rotation apart from a reuse alarm.
REVOKE_ROTATED = "rotated"
REVOKE_LOGOUT = "logout"
REVOKE_REUSE = "reuse_detected"
REVOKE_EXPIRED = "expired"
REVOKE_INACTIVE = "inactive_user"


@dataclass(frozen=True)
class IssuedRefreshToken:
    token: str
    record: RefreshToken


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decode_refresh(token: str) -> dict:
    """Validate the JWT envelope and return its claims.

    Signature, expiry and subject are checked by `decode_token`; this adds the
    hash check so a token that is correctly signed but was never issued by us
    (for example minted before the store was introduced) cannot be redeemed.
    """
    from app.core.security import decode_token

    try:
        return decode_token(token, expected_type="refresh")
    except ValueError:
        raise UnauthorizedError("Invalid or expired refresh token.") from None


def issue_refresh_token(
    db: Session,
    *,
    user_id: int,
    family_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedRefreshToken:
    """Mint a refresh token and persist the row that makes it revocable."""
    from app.core.security import create_refresh_token, new_jti

    jti = new_jti()
    token = create_refresh_token(str(user_id), jti=jti)
    expires_at = _now() + timedelta(minutes=settings.refresh_token_expire_minutes)
    record = RefreshToken(
        user_id=user_id,
        jti=jti,
        token_hash=hash_refresh_token(token),
        family_id=family_id or str(uuid.uuid4()),
        expires_at=expires_at,
        user_agent=_clip(user_agent, 255),
        ip_address=_clip(ip_address, 64),
    )
    db.add(record)
    return IssuedRefreshToken(token=token, record=record)


def _clip(value: str | None, limit: int) -> str | None:
    """Truncate a free-text header for storage, or store nothing."""
    if not value:
        return None
    return str(value)[:limit]


def rotate_refresh_token(
    db: Session,
    *,
    token: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedRefreshToken:
    """Exchange a live refresh token for a fresh one, or refuse.

    Raises `UnauthorizedError` for a token we never issued, one already used or
    revoked, and one belonging to a user who is no longer active.
    """
    claims = _decode_refresh(token)
    record = db.execute(
        select(RefreshToken).where(RefreshToken.jti == str(claims.get("jti")))
    ).scalar_one_or_none()

    if record is None:
        # Signed correctly but absent from the store: either minted before the
        # store existed or purged after expiry. Either way we cannot vouch for it.
        raise UnauthorizedError("Invalid or expired refresh token.")

    now = _now()
    if record.is_revoked:
        # Someone is presenting a token we already retired. Either a legitimate
        # client replayed after a successful refresh, or a copy leaked. Both are
        # indistinguishable from here, and both are resolved the same way.
        revoke_family(db, family_id=record.family_id, reason=REVOKE_REUSE)
        # Committed here rather than left to the caller. This branch raises, and
        # raising unwinds the request transaction - so an uncommitted revocation
        # would be rolled back and the stolen token would keep working, which is
        # the exact outcome reuse detection exists to prevent.
        db.commit()
        raise UnauthorizedError(
            "Refresh token has already been used or revoked; all sessions have been signed out."
        )

    if _as_utc(record.expires_at) <= now:
        record.revoked_at = now
        record.revoked_reason = REVOKE_EXPIRED
        # No commit needed: expires_at is authoritative and this row is already
        # unusable, so losing the annotation on rollback costs nothing.
        raise UnauthorizedError("Invalid or expired refresh token.")

    if record.token_hash != hash_refresh_token(token):
        raise UnauthorizedError("Invalid or expired refresh token.")

    # Rotate: retire the presented token, mint its successor in the same family so
    # a later replay of the old token still resolves to this family.
    record.revoked_at = now
    record.revoked_reason = REVOKE_ROTATED
    issued = issue_refresh_token(
        db,
        user_id=record.user_id,
        family_id=record.family_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return issued


def revoke_family(db: Session, *, family_id: str, reason: str) -> int:
    """Revoke every live token in a rotation family. Returns rows changed."""
    # Flushed first so the UPDATE sees tokens issued earlier in this transaction.
    # The session can run with autoflush disabled, in which case a pending INSERT
    # would be invisible to the UPDATE and the freshly minted token - precisely
    # the one that must not survive - would be left live.
    db.flush()
    now = _now()
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    return int(result.rowcount or 0)


def revoke_user_tokens(db: Session, *, user_id: int, reason: str = REVOKE_LOGOUT) -> int:
    """Revoke every live refresh token for a user (logout, password change)."""
    db.flush()
    now = _now()
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    return int(result.rowcount or 0)


def live_token_count(db: Session, *, user_id: int) -> int:
    stmt = select(RefreshToken.id).where(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked_at.is_(None),
        RefreshToken.expires_at > _now(),
    )
    return len(db.execute(stmt).scalars().all())


def purge_expired(db: Session, *, limit: int = 500) -> int:
    """Delete rows for tokens that expired long ago.

    Called opportunistically on the issue path. Without it the table only grows,
    because revoked rows are the whole point of the design. Rows are kept for a
    grace period past expiry so that a token presented slightly late is answered
    with "expired" rather than "unknown".
    """
    cutoff = _now() - timedelta(days=1)
    stale = (
        select(RefreshToken.id)
        .where(RefreshToken.expires_at < cutoff)
        .order_by(RefreshToken.id)
        .limit(limit)
    )
    result = db.execute(delete(RefreshToken).where(RefreshToken.id.in_(stale)))
    return int(result.rowcount or 0)


def _as_utc(value: datetime | None) -> datetime:
    """SQLite hands back naive datetimes; compare them as UTC."""
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
