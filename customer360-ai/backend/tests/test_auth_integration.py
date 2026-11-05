"""Auth integration tests.

The `api_client` / `api_session_factory` fixtures come from `conftest.py`: they
run the app against an in-memory SQLite database, so these exercise real request
handling without a local PostgreSQL.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.security import decode_token
from app.models.user import Organization


def test_two_organizations_can_share_a_display_name(
    api_client: TestClient, api_session_factory
) -> None:
    """A duplicate org name used to 500 on the UNIQUE slug constraint.

    The caller supplies a display name, not a slug, so two teams both called
    "Acme" must both be registrable.
    """
    first = api_client.post(
        "/api/v1/auth/register",
        json={"email": "a@example.com", "password": "password123", "organization_name": "Acme"},
    )
    second = api_client.post(
        "/api/v1/auth/register",
        json={"email": "b@example.com", "password": "password123", "organization_name": "Acme"},
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    with api_session_factory() as db:
        slugs = [row.slug for row in db.query(Organization).all()]
        names = {row.name for row in db.query(Organization).all()}
    assert names == {"Acme"}
    assert len(set(slugs)) == 2, f"slugs must stay unique: {slugs}"
    # The first registration keeps the predictable slug.
    assert "acme" in slugs


def test_register_creates_org_and_returns_tokens(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/v1/auth/register",
        json={"email": "admin@example.com", "password": "password123", "organization_name": "Acme"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body


def test_login_with_valid_credentials(api_client: TestClient) -> None:
    api_client.post(
        "/api/v1/auth/register",
        json={"email": "admin@example.com", "password": "password123", "organization_name": "Acme"},
    )
    resp = api_client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "password123"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_with_wrong_password(api_client: TestClient) -> None:
    api_client.post(
        "/api/v1/auth/register",
        json={"email": "admin@example.com", "password": "password123"},
    )
    resp = api_client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong-pass"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_me_requires_auth(api_client: TestClient) -> None:
    resp = api_client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user(api_client: TestClient) -> None:
    token = api_client.post(
        "/api/v1/auth/register",
        json={"email": "admin@example.com", "password": "password123", "organization_name": "Acme"},
    ).json()["access_token"]
    resp = api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@example.com"
    assert resp.json()["role"] == "ADMIN"


def test_duplicate_email_rejected(api_client: TestClient) -> None:
    payload = {"email": "admin@example.com", "password": "password123"}
    first = api_client.post("/api/v1/auth/register", json=payload)
    second = api_client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    assert second.status_code == 409


# ------------------------------------------------------------------ refresh / logout
# Phase 12: the refresh and logout paths had no coverage at all, which left the
# two endpoints an attacker actually interacts with after login unverified.


def _registered(api_client: TestClient) -> dict:
    resp = api_client.post(
        "/api/v1/auth/register",
        json={"email": "admin@example.com", "password": "password123", "organization_name": "Acme"},
    )
    assert resp.status_code == 201
    return resp.json()


def test_refresh_issues_a_usable_access_token(api_client: TestClient) -> None:
    tokens = _registered(api_client)
    resp = api_client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    claims = decode_token(resp.json()["access_token"])
    assert claims["sub"] == decode_token(tokens["access_token"])["sub"]
    assert claims["type"] == "access"


def test_refreshed_access_token_works(api_client: TestClient) -> None:
    tokens = _registered(api_client)
    refreshed = api_client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    me = api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {refreshed['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == "admin@example.com"


def test_access_token_cannot_be_used_to_refresh(api_client: TestClient) -> None:
    # Otherwise a leaked short-lived access token becomes a permanent credential.
    tokens = _registered(api_client)
    resp = api_client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert resp.status_code == 401


def test_garbage_refresh_token_is_rejected(api_client: TestClient) -> None:
    resp = api_client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert resp.status_code == 401


def test_refresh_for_a_deactivated_user_is_rejected(api_client: TestClient, api_session_factory) -> None:
    tokens = _registered(api_client)
    from sqlalchemy import select

    from app.models.user import User

    db = api_session_factory()
    try:
        db.execute(select(User).where(User.email == "admin@example.com")).scalar_one().is_active = False
        db.commit()
    finally:
        db.close()

    resp = api_client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def test_deactivated_user_cannot_use_an_existing_access_token(api_client: TestClient, api_session_factory) -> None:
    tokens = _registered(api_client)
    from sqlalchemy import select

    from app.models.user import User

    db = api_session_factory()
    try:
        db.execute(select(User).where(User.email == "admin@example.com")).scalar_one().is_active = False
        db.commit()
    finally:
        db.close()

    me = api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 401


def test_logout_requires_authentication(api_client: TestClient) -> None:
    assert api_client.post("/api/v1/auth/logout").status_code == 401


def test_logout_succeeds_for_an_authenticated_user(api_client: TestClient) -> None:
    tokens = _registered(api_client)
    resp = api_client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 204


def test_weak_password_is_rejected(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "password": "123", "organization_name": "Acme"},
    )
    assert resp.status_code == 422


def test_password_is_never_returned_by_the_api(api_client: TestClient) -> None:
    tokens = _registered(api_client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = api_client.get("/api/v1/auth/me", headers=headers)
    assert "password" not in me.text.lower()

