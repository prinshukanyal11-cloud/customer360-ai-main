"""Server-side refresh-token store and login-failure tracking.

Refresh tokens used to be stateless JWTs: logout wrote an audit row and
returned 204, but nothing invalidated the token, so a leaked one stayed usable
for its full lifetime. Storing a hash of each issued token makes revocation
possible and lets a rotated token be recognised when it is presented a second
time, which is the signal that a copy has escaped.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixin import TimestampMixin
from app.models.user import User


class RefreshToken(TimestampMixin, Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("jti", name="uq_refresh_tokens_jti"),
        # Logout and "revoke everything for this user" both filter on
        # (user_id, revoked_at), so the partial-state lookup is the hot path.
        Index("ix_refresh_tokens_user_revoked", "user_id", "revoked_at"),
        # Reuse detection revokes a whole rotation chain at once.
        Index("ix_refresh_tokens_family", "family_id"),
        Index("ix_refresh_tokens_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # jti identifies the JWT; token_hash proves the presented string is the one
    # we issued. Neither stores the token itself.
    jti: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship("User", lazy="noload")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class LoginFailure(TimestampMixin, Base):
    """Failed sign-in counters, keyed by the submitted email.

    Keying on the submitted email rather than on a user id is deliberate: the
    lockout decision has to be identical whether or not the address belongs to a
    real account, otherwise the response itself discloses which emails are
    registered.
    """

    __tablename__ = "login_failures"
    __table_args__ = (
        UniqueConstraint("email", name="uq_login_failures_email"),
        Index("ix_login_failures_locked_until", "locked_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failed_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None
