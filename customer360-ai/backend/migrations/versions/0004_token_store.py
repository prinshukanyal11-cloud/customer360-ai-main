"""refresh token store and login lockout (Phase 13)

Closes two gaps that `docs/security.md` recorded as open.

`refresh_tokens` makes the refresh JWT revocable. Logout previously wrote an audit
row and returned 204 while the token itself stayed valid for a further seven days,
so a leaked token was indistinguishable from a live one. One row is kept per
issued token holding only its SHA-256, which gives logout something to revoke,
gives refresh rotation a way to recognise a replayed predecessor, and keeps a
database disclosure from yielding usable credentials.

`login_failures` counts failed sign-ins per submitted address. Rate limiting
bounds how fast one source can guess and forgets when the window rolls; this
survives the roll. It is keyed on the submitted email rather than a user id on
purpose - the lockout response is then identical whether or not the address is
registered, so the table cannot be used to enumerate users. Stale rows are pruned
because otherwise every probe against a non-existent address would add a row.

Indexes follow the predicates rather than the columns: (user_id, revoked_at) is
the "revoke everything this user holds" filter, family_id is the reuse-detection
fan-out, and expires_at drives pruning.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE refresh_tokens (
            id SERIAL NOT NULL,
            user_id INTEGER NOT NULL,
            jti VARCHAR(64) NOT NULL,
            token_hash VARCHAR(64) NOT NULL,
            family_id VARCHAR(36) NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            revoked_at TIMESTAMP WITH TIME ZONE,
            revoked_reason VARCHAR(32),
            user_agent VARCHAR(255),
            ip_address VARCHAR(64),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_refresh_tokens PRIMARY KEY (id),
            CONSTRAINT uq_refresh_tokens_jti UNIQUE (jti),
            CONSTRAINT fk_refresh_tokens_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_user_revoked", "refresh_tokens", ["user_id", "revoked_at"])
    op.create_index("ix_refresh_tokens_family", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens_expires", "refresh_tokens", ["expires_at"])

    op.execute(
        """
        CREATE TABLE login_failures (
            id SERIAL NOT NULL,
            email VARCHAR(320) NOT NULL,
            failed_count INTEGER DEFAULT '0' NOT NULL,
            first_failed_at TIMESTAMP WITH TIME ZONE NOT NULL,
            last_failed_at TIMESTAMP WITH TIME ZONE NOT NULL,
            locked_until TIMESTAMP WITH TIME ZONE,
            last_failed_ip VARCHAR(64),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_login_failures PRIMARY KEY (id),
            CONSTRAINT uq_login_failures_email UNIQUE (email)
        )
    """
    )
    op.create_index("ix_login_failures_locked_until", "login_failures", ["locked_until"])


def downgrade() -> None:
    op.drop_table("login_failures")
    op.drop_table("refresh_tokens")