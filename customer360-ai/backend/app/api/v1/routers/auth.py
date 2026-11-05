"""Authentication endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.core.constants import AuditAction
from app.core.exceptions import UnauthorizedError
from app.core.security_deps import CurrentUser, DbSession
from app.models.user import User
from app.schemas.schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserOut
from app.services.audit_service import audit
from app.services.auth_service import AuthService
from app.services.token_service import (
    REVOKE_INACTIVE,
    REVOKE_LOGOUT,
    revoke_user_tokens,
    rotate_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(tokens: dict[str, str]) -> TokenResponse:
    return TokenResponse(access_token=tokens["access_token"], refresh_token=tokens["refresh_token"])


def _request_context(request: Request) -> tuple[str | None, str | None]:
    client_host = request.client.host if request.client else None
    return request.headers.get("user-agent"), client_host


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(payload: RegisterRequest, db: DbSession, request: Request) -> TokenResponse:
    user, tokens = AuthService(db).register(
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        organization_name=payload.organization_name,
    )
    client_host = request.client.host if request.client else None
    audit(db, user.organization_id, user.id, AuditAction.AUTH_REGISTER, ip=client_host)
    return _token_response(tokens)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession, request: Request) -> TokenResponse:
    user, tokens = AuthService(db).login(
        email=payload.email,
        password=payload.password,
        ip_address=request.client.host if request.client else None,
    )
    audit(db, user.organization_id, user.id, AuditAction.AUTH_LOGIN, ip=request.client.host if request.client else None)
    return _token_response(tokens)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: DbSession, request: Request) -> TokenResponse:
    from app.core.security import create_access_token
    from app.repositories.user import UserRepository

    user_agent, client_host = _request_context(request)

    # Rotating here rather than in the router means a replayed token is caught by
    # the store: the presented token is retired and, if it had already been
    # retired, its whole family is revoked as a likely leak.
    issued = rotate_refresh_token(db, token=payload.refresh_token, user_agent=user_agent, ip_address=client_host)

    user = UserRepository(db).get_by_id(issued.record.user_id)
    if user is None or not user.is_active:
        # A deactivated or deleted account must not be able to trade a live
        # refresh token for access tokens.
        revoke_user_tokens(db, user_id=issued.record.user_id, reason=REVOKE_INACTIVE)
        db.commit()
        raise UnauthorizedError("User is not active or does not exist.")

    db.commit()
    access = create_access_token(str(user.id), {"role": user.role, "org": user.organization_id})
    return TokenResponse(access_token=access, refresh_token=issued.token)


@router.post("/logout", status_code=204)
def logout(db: DbSession, request: Request, user: CurrentUser) -> Response:
    client_host = request.client.host if request.client else None

    # Revoke, do not merely record. A logout that leaves the refresh token usable
    # is a logout that only ends the session in the browser that performed it.
    # Committed before the audit row because audit() commits internally and rolls
    # back on failure - the revocation must not ride on an audit write.
    revoked = revoke_user_tokens(db, user_id=user.id, reason=REVOKE_LOGOUT)
    db.commit()
    audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.AUTH_LOGOUT,
        ip=client_host,
        metadata_json={"refresh_tokens_revoked": revoked},
    )
    return Response(status_code=204)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user