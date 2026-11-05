"""Central dependency resolution (DB session, current user, RBAC)."""
from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.session import SessionLocal
from app.models.user import User
from app.repositories.user import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    if credentials is None:
        raise UnauthorizedError("Missing authentication credentials.")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except ValueError:
        raise UnauthorizedError("Invalid or expired token.") from None
    user = UserRepository(db).get_by_id(int(payload["sub"]))
    if user is None or not user.is_active:
        raise UnauthorizedError("User is not active or does not exist.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: str) -> Callable:
    def _dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ForbiddenError("You do not have permission to perform this action.")
        return user

    return _dependency


def ensure_org_scope(db: Session, user: User) -> None:
    """Guidance for repository/service layers: every query is org-scoped."""
    if user.organization_id is None:
        raise ForbiddenError("User is not attached to an organization.")