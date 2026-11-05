"""Authentication + registration business logic."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.constants import Roles
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import Organization, User
from app.repositories.user import OrganizationRepository, UserRepository
from app.services import lockout_service
from app.services.token_service import issue_refresh_token, purge_expired


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "organization"


def _unique_slug(db: Session, base: str) -> str:
    """A slug that is free, so two organizations can share a display name.

    `slug` is a UNIQUE column derived from the organization name, which made the
    second registration using a name already in use fail on the constraint and
    surface as a 500. Rejecting the caller instead would be worse: they chose a
    display name, not a slug, and two teams may legitimately both be called
    "Acme". The suffix is only added when the plain slug is taken, so the common
    case stays predictable, and the slug is an internal identifier - nothing
    resolves an organization by it.
    """
    orgs = OrganizationRepository(db)
    if orgs.get_by_slug(base) is None:
        return base
    for _ in range(8):
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
        if orgs.get_by_slug(candidate) is None:
            return candidate
    # Vanishingly unlikely; a full table of 8 matching slugs is reported honestly
    # rather than looped on.
    raise ConflictError("Could not allocate a unique organization identifier; try again.")


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _issue(self, user: User, *, ip_address: str | None = None) -> tuple[User, dict[str, str]]:
        """Mint an access/refresh pair and record the refresh token for revocation."""
        refresh = issue_refresh_token(self.db, user_id=user.id, ip_address=ip_address)
        tokens = {
            "access_token": create_access_token(
                str(user.id), {"role": user.role, "org": user.organization_id}
            ),
            "refresh_token": refresh.token,
        }
        return user, tokens

    def register(
        self,
        *,
        email: str,
        password: str,
        full_name: str | None,
        organization_name: str | None,
    ) -> tuple[User, dict[str, str]]:
        users = UserRepository(self.db)
        if users.get_by_email(email):
            raise ConflictError("An account with this email already exists.")

        org_name = organization_name or "Default Organization"
        org_repo = OrganizationRepository(self.db)
        org = Organization(name=org_name, slug=_unique_slug(self.db, _slugify(org_name)))
        org_repo.add(org)
        self.db.flush()

        # First user of an org becomes ADMIN
        user = User(
            organization_id=org.id,
            email=email.lower(),
            full_name=full_name,
            password_hash=hash_password(password),
            role=Roles.ADMIN,
        )
        users.add(user)
        self.db.flush()
        # A successful registration clears any failure counter left on the
        # address by attempts against an account that did not exist yet.
        lockout_service.record_success(self.db, email=email)
        # Keeps the refresh-token table from growing without bound on a busy
        # deployment; best effort, so a failure here must not block sign-up.
        purge_expired(self.db)
        self.db.commit()
        self.db.refresh(user)

        return self._issue(user)

    def login(
        self,
        *,
        email: str,
        password: str,
        ip_address: str | None = None,
    ) -> tuple[User, dict[str, str]]:
        # Checked before any credential work so a locked address is refused
        # without spending a password verification, and without the response
        # revealing whether the address exists.
        lockout_service.assert_not_locked(self.db, email=email)

        users = UserRepository(self.db)
        user = users.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            # One message for "no such account" and "wrong password": the caller
            # must not be able to learn which addresses are registered.
            lockout_service.record_failure(self.db, email=email, ip_address=ip_address)
            self.db.commit()
            raise UnauthorizedError(lockout_service.GENERIC_MESSAGE)
        if not user.is_active:
            raise UnauthorizedError("This account has been deactivated.")

        user.last_login_at = datetime.now(timezone.utc)
        lockout_service.record_success(self.db, email=email)
        issued_user, tokens = self._issue(user, ip_address=ip_address)
        self.db.commit()
        return issued_user, tokens
