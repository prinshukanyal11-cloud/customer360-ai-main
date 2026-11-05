"""Refresh-token revocation, rotation and reuse detection.

These are the behaviours that made logout mean something. A refresh JWT is
verifiable but not revocable on its own, so each test here checks the property
that actually matters: a token the server has retired cannot mint access tokens
again, and presenting one twice is treated as evidence of a leak.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.auth_tokens import RefreshToken
from app.services.token_service import (
    REVOKE_LOGOUT,
    REVOKE_REUSE,
    REVOKE_ROTATED,
    hash_refresh_token,
    live_token_count,
)

PASSWORD = "password123"


def _register(api_client: TestClient, email: str = "user@example.com") -> dict:
    resp = api_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "organization_name": "Acme"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rows(api_session_factory, email: str) -> list[RefreshToken]:
    from app.models.user import User

    with api_session_factory() as db:
        user = db.query(User).filter(User.email == email).one()
        return db.query(RefreshToken).filter(RefreshToken.user_id == user.id).all()


def test_registration_stores_a_hashed_refresh_token(api_client: TestClient, api_session_factory) -> None:
    body = _register(api_client)
    rows = _rows(api_session_factory, "user@example.com")

    assert len(rows) == 1
    stored = rows[0]
    # The raw token is never persisted, only its digest, so a database
    # disclosure does not hand over a usable credential.
    assert stored.token_hash == hash_refresh_token(body["refresh_token"])
    assert body["refresh_token"] not in stored.token_hash
    assert stored.revoked_at is None


def test_logout_revokes_the_refresh_token(api_client: TestClient, api_session_factory) -> None:
    body = _register(api_client)
    refresh = body["refresh_token"]

    logout = api_client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert logout.status_code == 204

    rows = _rows(api_session_factory, "user@example.com")
    assert rows[0].revoked_reason == REVOKE_LOGOUT

    # The real assertion: the token that logout did not visibly touch is dead.
    after = api_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert after.status_code == 401
    assert "already been used or revoked" in after.json()["error"]["message"]


def test_refresh_rotates_the_token(api_client: TestClient, api_session_factory) -> None:
    first = _register(api_client)["refresh_token"]

    rotated = api_client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()["refresh_token"]
    assert second != first

    rows = _rows(api_session_factory, "user@example.com")
    by_hash = {row.token_hash: row for row in rows}
    # Two rows: the retired predecessor and its live successor, in one family so
    # a later replay still resolves to the same chain.
    assert len(rows) == 2
    assert by_hash[hash_refresh_token(first)].revoked_reason == REVOKE_ROTATED
    assert by_hash[hash_refresh_token(first)].family_id == by_hash[hash_refresh_token(second)].family_id
    assert by_hash[hash_refresh_token(second)].revoked_at is None


def test_rotated_refresh_token_still_yields_access(api_client: TestClient) -> None:
    first = _register(api_client)["refresh_token"]
    rotated = api_client.post("/api/v1/auth/refresh", json={"refresh_token": first}).json()

    me = api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {rotated['access_token']}"})
    assert me.status_code == 200


def test_replaying_a_rotated_token_revokes_the_whole_family(api_client: TestClient, api_session_factory) -> None:
    first = _register(api_client)["refresh_token"]
    second = api_client.post("/api/v1/auth/refresh", json={"refresh_token": first}).json()["refresh_token"]

    replay = api_client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401
    assert "all sessions have been signed out" in replay.json()["error"]["message"]

    # The successor dies too. The stolen copy and the legitimate holder are
    # indistinguishable from here, so neither keeps working.
    after = api_client.post("/api/v1/auth/refresh", json={"refresh_token": second})
    assert after.status_code == 401

    rows = _rows(api_session_factory, "user@example.com")
    # The predecessor keeps its "rotated" annotation; reuse detection stamps the
    # successor, which was still live when the leak was spotted.
    by_hash = {row.token_hash: row for row in rows}
    assert by_hash[hash_refresh_token(second)].revoked_reason == REVOKE_REUSE
    assert all(row.revoked_at is not None for row in rows)
    with api_session_factory() as db:
        from app.models.user import User

        user = db.query(User).filter(User.email == "user@example.com").one()
        assert live_token_count(db, user_id=user.id) == 0


def test_refresh_rejects_an_unknown_but_well_formed_token(api_client: TestClient) -> None:
    """A signed refresh token we never issued must not be redeemable."""
    from app.core.security import create_refresh_token

    forged = create_refresh_token("1", jti="f" * 32)
    resp = api_client.post("/api/v1/auth/refresh", json={"refresh_token": forged})
    assert resp.status_code == 401
    assert "already been used" not in resp.json()["error"]["message"]


def test_refresh_rejects_an_access_token(api_client: TestClient) -> None:
    body = _register(api_client)
    resp = api_client.post("/api/v1/auth/refresh", json={"refresh_token": body["access_token"]})
    assert resp.status_code == 401


def test_deactivated_user_cannot_trade_a_refresh_token(api_client: TestClient, api_session_factory) -> None:
    body = _register(api_client)
    with api_session_factory() as db:
        from app.models.user import User

        db.query(User).filter(User.email == "user@example.com").update({"is_active": False})
        db.commit()

    resp = api_client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "User is not active or does not exist."

    # Nothing is left usable. The token presented was already marked rotated
    # before the account check ran, so the assertion is on liveness rather than
    # on one particular reason string.
    rows = _rows(api_session_factory, "user@example.com")
    assert all(row.revoked_at is not None for row in rows)
    with api_session_factory() as db:
        from app.models.user import User

        user = db.query(User).filter(User.email == "user@example.com").one()
        assert live_token_count(db, user_id=user.id) == 0


def test_logout_records_how_many_sessions_it_ended(api_client: TestClient, api_session_factory) -> None:
    body = _register(api_client)
    current = body["refresh_token"]
    for _ in range(2):
        rotated = api_client.post("/api/v1/auth/refresh", json={"refresh_token": current})
        assert rotated.status_code == 200, rotated.text
        current = rotated.json()["refresh_token"]

    api_client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {body['access_token']}"})

    from app.models.campaign import AuditLog

    with api_session_factory() as db:
        entry = (
            db.query(AuditLog)
            .filter(AuditLog.action == "auth.logout")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert entry is not None
        # Logout ends every live session, not just the current one. Two rotations
        # retired two predecessors, leaving exactly one live token to revoke.
        assert entry.metadata_json["refresh_tokens_revoked"] == 1

    rows = _rows(api_session_factory, "user@example.com")
    assert all(row.revoked_at is not None for row in rows)


@pytest.mark.parametrize("bad", ["", "not-a-jwt", "a.b.c"])
def test_malformed_refresh_token_is_rejected(api_client: TestClient, bad: str) -> None:
    resp = api_client.post("/api/v1/auth/refresh", json={"refresh_token": bad})
    assert resp.status_code == 401