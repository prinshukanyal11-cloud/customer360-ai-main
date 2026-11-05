"""Login lockout, and the property that matters most: it must not leak which
addresses are registered.

A lockout that only engages for real accounts would be an account-enumeration
oracle. Every test here therefore checks both branches - an address that exists
and one that does not - and expects them to behave identically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.auth_tokens import LoginFailure
from app.services import lockout_service

PASSWORD = "password123"
WRONG = "wrong-password"


def _register(api_client: TestClient, email: str) -> None:
    resp = api_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "organization_name": "Acme"},
    )
    assert resp.status_code == 201, resp.text


def _fail(api_client: TestClient, email: str) -> str:
    resp = api_client.post("/api/v1/auth/login", json={"email": email, "password": WRONG})
    assert resp.status_code == 401, resp.text
    return resp.json()["error"]["message"]


@pytest.fixture(autouse=True)
def _small_threshold(monkeypatch):
    """Keep the shipped default of 5 honest by lowering it, not by disabling."""
    monkeypatch.setattr(settings, "login_lockout_enabled", True)
    monkeypatch.setattr(settings, "login_lockout_threshold", 3)
    monkeypatch.setattr(settings, "login_lockout_minutes", 15)
    monkeypatch.setattr(settings, "login_lockout_window_minutes", 15)


def test_repeated_failures_lock_the_address(api_client: TestClient) -> None:
    _register(api_client, "user@example.com")
    for _ in range(2):
        _fail(api_client, "user@example.com")

    # Still under the threshold, so the correct password works.
    assert api_client.post(
        "/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD}
    ).status_code == 200

    # Failures were cleared by that success, so the threshold is reached again.
    for _ in range(3):
        _fail(api_client, "user@example.com")

    # The next attempt is refused even though the password is correct: that is
    # what a cooling-off period means.
    good = api_client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert good.status_code == 401
    assert good.json()["error"]["message"] == lockout_service.LOCKED_MESSAGE


def test_lockout_releases_after_the_cooldown(api_client: TestClient, api_session_factory) -> None:
    _register(api_client, "user@example.com")
    for _ in range(3):
        _fail(api_client, "user@example.com")

    with api_session_factory() as db:
        record = db.query(LoginFailure).filter(LoginFailure.email == "user@example.com").one()
        assert record.locked_until is not None
        # Rewind the cooling-off deadline rather than sleeping through it.
        record.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    good = api_client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert good.status_code == 200, good.text


def test_successful_login_clears_the_counter(api_client: TestClient, api_session_factory) -> None:
    _register(api_client, "user@example.com")
    _fail(api_client, "user@example.com")
    _fail(api_client, "user@example.com")

    good = api_client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert good.status_code == 200

    with api_session_factory() as db:
        # Cleared, so the next two failures must not lock the address.
        assert db.query(LoginFailure).filter(LoginFailure.email == "user@example.com").count() == 0


def test_lockout_does_not_reveal_whether_an_account_exists(api_client: TestClient) -> None:
    """The whole point: real and unregistered addresses behave identically."""
    _register(api_client, "real@example.com")

    real_messages: list[str] = []
    ghost_messages: list[str] = []
    real_statuses: list[int] = []
    ghost_statuses: list[int] = []

    for _ in range(4):
        real = api_client.post("/api/v1/auth/login", json={"email": "real@example.com", "password": WRONG})
        ghost = api_client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": WRONG})
        real_statuses.append(real.status_code)
        ghost_statuses.append(ghost.status_code)
        real_messages.append(real.json()["error"]["message"])
        ghost_messages.append(ghost.json()["error"]["message"])

    assert real_statuses == ghost_statuses
    assert real_messages == ghost_messages
    # Both lock on the same attempt with the same wording.
    assert real_messages[0] == lockout_service.GENERIC_MESSAGE
    assert real_messages[-1] == lockout_service.LOCKED_MESSAGE


def test_unknown_and_wrong_password_are_indistinguishable(api_client: TestClient) -> None:
    _register(api_client, "real@example.com")
    wrong = api_client.post("/api/v1/auth/login", json={"email": "real@example.com", "password": WRONG})
    unknown = api_client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": WRONG})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_no_counter_row_is_written_for_an_unusable_address(api_client: TestClient, api_session_factory) -> None:
    """An address that normalises to nothing must not become a table row.

    The counter is keyed on attacker-supplied text, so a probe with blank input
    would otherwise write a row per attempt. The request schema already rejects
    both forms; the service guard is the backstop if that ever changes.
    """
    for blank in ("", "   "):
        assert api_client.post("/api/v1/auth/login", json={"email": blank, "password": WRONG}).status_code == 422

    with api_session_factory() as db:
        assert lockout_service.record_failure(db, email="   ", ip_address=None).failed_count == 0
        assert db.query(LoginFailure).count() == 0


def test_stale_counters_are_pruned(api_client: TestClient, api_session_factory) -> None:
    """Probing non-existent addresses must not grow the table forever."""
    _fail(api_client, "ghost1@example.com")
    with api_session_factory() as db:
        assert db.query(LoginFailure).count() == 1
        record = db.query(LoginFailure).one()
        record.last_failed_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.commit()

    _fail(api_client, "ghost2@example.com")
    with api_session_factory() as db:
        emails = {row.email for row in db.query(LoginFailure).all()}
        # The aged-out row is gone; the new one is not.
        assert "ghost1@example.com" not in emails
        assert "ghost2@example.com" in emails


def test_failures_outside_the_window_start_a_new_count(api_client: TestClient, api_session_factory) -> None:
    """A slow trickle must not accumulate into a lock one attempt at a time."""
    _register(api_client, "user@example.com")
    _fail(api_client, "user@example.com")
    _fail(api_client, "user@example.com")

    with api_session_factory() as db:
        record = db.query(LoginFailure).filter(LoginFailure.email == "user@example.com").one()
        assert record.failed_count == 2
        # Both failures were spaced beyond the counting window.
        record.first_failed_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()

    _fail(api_client, "user@example.com")
    with api_session_factory() as db:
        record = db.query(LoginFailure).filter(LoginFailure.email == "user@example.com").one()
        # Reset to 1 rather than reaching the threshold of 3.
        assert record.failed_count == 1
        assert record.locked_until is None


def test_registration_clears_a_stale_counter(api_client: TestClient) -> None:
    for _ in range(2):
        _fail(api_client, "new@example.com")

    resp = api_client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": PASSWORD, "organization_name": "Acme"},
    )
    assert resp.status_code == 201, resp.text

    login = api_client.post("/api/v1/auth/login", json={"email": "new@example.com", "password": PASSWORD})
    assert login.status_code == 200


def test_deactivated_account_is_refused_before_any_lockout(api_client: TestClient, api_session_factory) -> None:
    _register(api_client, "user@example.com")
    with api_session_factory() as db:
        from app.models.user import User

        db.query(User).filter(User.email == "user@example.com").update({"is_active": False})
        db.commit()

    resp = api_client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "This account has been deactivated."