"""Login lockout after repeated failures.

Rate limiting bounds how fast one source can guess; it does nothing about a
slow, distributed attempt against a known address, and it forgets the moment the
window rolls. This adds the missing half: a per-address failure counter that
locks sign-in for a cooling-off period once the threshold is reached.

Two properties matter more than the feature itself:

**No account enumeration.** The counter is keyed by the *submitted* email, not
by a user id, and the lockout is checked before the password is verified. An
address that does not exist accumulates failures exactly like one that does, so
the locked response is byte-identical either way and cannot be used to enumerate
registered users. For the same reason the failure path never distinguishes
"unknown email" from "wrong password".

**The store cannot be used to flood the table.** Every failed sign-in for an
address that does not exist would otherwise write a row an attacker controls, so
stale rows are pruned and a counter resets once it falls outside the counting
window rather than growing forever.

Lockout is deliberately generous (see `login_lockout_threshold`). An attacker who
can reach the sign-in form can lock a known colleague out - that is inherent to
lockout, and the reason the threshold is not lower. `login_lockout_enabled=false`
turns it off for environments that prefer not to hold the state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.models.auth_tokens import LoginFailure

# Identical for a locked real account and a locked address that does not exist,
# so the response cannot be used to probe for registered users.
LOCKED_MESSAGE = "Too many failed sign-in attempts. Try again later."
GENERIC_MESSAGE = "Incorrect email or password."


@dataclass(frozen=True)
class LockoutState:
    locked: bool
    failed_count: int
    locked_until: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def get_record(db: Session, *, email: str) -> LoginFailure | None:
    stmt = select(LoginFailure).where(LoginFailure.email == _normalize(email))
    return db.execute(stmt).scalar_one_or_none()


def is_locked(db: Session, *, email: str) -> LockoutState:
    """Read-only: report whether sign-in is currently blocked for this address."""
    record = get_record(db, email=email)
    if record is None:
        return LockoutState(locked=False, failed_count=0)

    locked_until = _as_utc(record.locked_until)
    if locked_until is not None and locked_until <= _now():
        return LockoutState(locked=False, failed_count=record.failed_count)

    return LockoutState(
        locked=locked_until is not None,
        failed_count=record.failed_count,
        locked_until=locked_until,
    )


def assert_not_locked(db: Session, *, email: str) -> None:
    """Block sign-in while locked, discarding counters that have expired."""
    record = get_record(db, email=email)
    if record is None:
        return

    locked_until = _as_utc(record.locked_until)
    if locked_until is None:
        return
    if locked_until > _now():
        raise UnauthorizedError(LOCKED_MESSAGE)

    # Cooling-off elapsed. Clear it so the next failure starts a fresh count
    # instead of resuming the run that locked the address.
    db.delete(record)
    db.flush()


def record_failure(db: Session, *, email: str, ip_address: str | None = None) -> LockoutState:
    """Count one failed sign-in and lock once the threshold is reached."""
    if not settings.login_lockout_enabled:
        return LockoutState(locked=False, failed_count=0)

    normalized = _normalize(email)
    if not normalized:
        # Nothing worth counting, and an attacker-controlled key space we would
        # rather not write to the database.
        return LockoutState(locked=False, failed_count=0)

    now = _now()
    # Runs before the read below so the caller's own counter is never the row
    # being pruned.
    prune_stale(db)
    record = get_record(db, email=normalized)
    if record is None:
        record = LoginFailure(
            email=normalized,
            failed_count=0,
            first_failed_at=now,
            last_failed_at=now,
        )
        db.add(record)
        db.flush()

    window_start = now - timedelta(minutes=settings.login_lockout_window_minutes)
    if _as_utc(record.first_failed_at) < window_start:
        # The burst that created this counter has aged out of the counting
        # window, so this failure begins a new run.
        record.first_failed_at = now
        record.failed_count = 0

    record.failed_count += 1
    record.last_failed_at = now
    if ip_address:
        record.last_failed_ip = str(ip_address)[:64]

    locked = False
    if record.failed_count >= settings.login_lockout_threshold:
        record.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
        locked = True

    return LockoutState(locked=locked, failed_count=record.failed_count, locked_until=record.locked_until)


def record_success(db: Session, *, email: str) -> None:
    """Clear the counter after a successful sign-in."""
    record = get_record(db, email=email)
    if record is not None:
        db.delete(record)
        db.flush()


def prune_stale(db: Session, *, limit: int = 200) -> int:
    """Delete counters that are too old to lock anything.

    Without this, probing addresses that do not exist would grow the table by one
    row per attempt, indefinitely. Runs before the current address is read so the
    caller's own counter is never the row being removed.
    """
    cutoff = _now() - timedelta(
        minutes=max(settings.login_lockout_window_minutes, settings.login_lockout_minutes) * 2
    )
    stale = (
        select(LoginFailure.id)
        .where(LoginFailure.last_failed_at < cutoff)
        .order_by(LoginFailure.id)
        .limit(limit)
    )
    result = db.execute(delete(LoginFailure).where(LoginFailure.id.in_(stale)))
    return int(result.rowcount or 0)