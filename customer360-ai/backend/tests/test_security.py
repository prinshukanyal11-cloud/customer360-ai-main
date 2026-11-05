"""Phase 12 security controls: config guard, security headers, rate limiting, token parsing.

Each test here corresponds to a control that `docs/security.md` claimed and the
code did not implement, so a regression fails loudly instead of quietly restoring
a documented-but-absent protection.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import Settings
from app.core.security import create_access_token, decode_token
from app.middleware.rate_limit import FixedWindowLimiter, limiter

# --------------------------------------------------------------------------- config guard


def _prod_settings(**overrides) -> Settings:
    """A production config that satisfies every guard, so one field can be broken at a time."""
    base = {
        "app_env": "production",
        "debug": False,
        "jwt_secret": "j" * 40,
        "secret_key": "s" * 40,
        "cors_origins": "https://app.example.com",
        "database_url_ro": "postgresql+psycopg://ro@localhost/app",
    }
    return Settings(**{**base, **overrides})


def test_production_config_with_real_secrets_starts() -> None:
    assert _prod_settings().is_production is True


def test_production_rejects_placeholder_jwt_secret() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET is still the shipped placeholder"):
        _prod_settings(jwt_secret="change-me")


def test_production_rejects_placeholder_secret_key() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY is still the shipped placeholder"):
        _prod_settings(secret_key="change-me")


def test_production_rejects_short_secret() -> None:
    # Not a shipped default, but an offline-brute-forceable length is the same defect.
    with pytest.raises(ValueError, match="shorter than 32 characters"):
        _prod_settings(jwt_secret="tooshort")


def test_production_rejects_debug_enabled() -> None:
    with pytest.raises(ValueError, match="DEBUG is true"):
        _prod_settings(debug=True)


def test_production_rejects_wildcard_cors() -> None:
    # allow_credentials=True plus "*" means any site can call the API as a user.
    with pytest.raises(ValueError, match="CORS_ORIGINS contains"):
        _prod_settings(cors_origins="*")


def test_production_rejects_missing_readonly_database_url() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL_RO is empty"):
        _prod_settings(database_url_ro="")


def test_production_reports_every_problem_at_once() -> None:
    with pytest.raises(ValueError) as excinfo:
        _prod_settings(jwt_secret="change-me", debug=True, cors_origins="*")
    message = str(excinfo.value)
    assert "JWT_SECRET" in message and "DEBUG" in message and "CORS_ORIGINS" in message


def test_development_allows_shipped_defaults() -> None:
    # A fresh clone must run without ceremony; the guard is production-only.
    settings = Settings(_env_file=None)
    assert settings.is_production is False
    assert settings.jwt_secret == "change-me"


def test_production_recognises_prod_alias() -> None:
    assert _prod_settings(app_env="prod").is_production is True
    assert _prod_settings(app_env="PRODUCTION").is_production is True


# --------------------------------------------------------------------------- security headers


def test_security_headers_present_on_api_response(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "geolocation=()" in response.headers["Permissions-Policy"]
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"


def test_hsts_absent_outside_production(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings

    # Asserting HSTS over plain local HTTP would pin localhost to HTTPS in a dev browser.
    monkeypatch.setattr(settings, "app_env", "development")
    assert client.get("/api/v1/health").headers.get("Strict-Transport-Security") is None


def test_hsts_present_in_production(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "production")
    hsts = client.get("/api/v1/health").headers.get("Strict-Transport-Security", "")
    assert "max-age=31536000" in hsts and "includeSubDomains" in hsts


def test_csp_applied_to_docs_but_not_to_api(client: TestClient) -> None:
    docs = client.get("/docs")
    assert "script-src" in docs.headers.get("Content-Security-Policy", "")
    assert client.get("/api/v1/health").headers.get("Content-Security-Policy") is None


def test_security_headers_can_be_disabled(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.middleware.security_headers.settings.security_headers_enabled", False)
    assert "X-Frame-Options" not in client.get("/api/v1/health").headers


# --------------------------------------------------------------------------- rate limiting


def test_limiter_allows_up_to_the_limit_then_blocks() -> None:
    window = FixedWindowLimiter()
    for _ in range(3):
        allowed, remaining, _retry = window.hit("k", 3)
        assert allowed is True
        assert remaining >= 0
    allowed, remaining, retry_after = window.hit("k", 3)
    assert allowed is False
    assert remaining == 0
    assert retry_after > 0


def test_limiter_keys_are_independent() -> None:
    window = FixedWindowLimiter()
    for _ in range(2):
        window.hit("noisy", 2)
    assert window.hit("noisy", 2)[0] is False
    assert window.hit("quiet", 2)[0] is True


def test_limiter_window_resets_after_it_expires() -> None:
    window = FixedWindowLimiter()
    now = 1_000.0
    for _ in range(2):
        window.hit("k", 2, now=now)
    assert window.hit("k", 2, now=now)[0] is False
    # Past the 60s window the caller is a new request, not a permanently banned one.
    assert window.hit("k", 2, now=now + 61)[0] is True


def test_limiter_prunes_expired_entries() -> None:
    from app.middleware.rate_limit import PRUNE_AFTER_ENTRIES

    window = FixedWindowLimiter()
    now = 1_000.0
    for i in range(PRUNE_AFTER_ENTRIES + 50):
        window.hit(f"k{i}", 5, now=now)
    assert len(window._windows) > PRUNE_AFTER_ENTRIES
    # One request after the window has passed triggers the sweep of stale keys.
    window.hit("late", 5, now=now + 61)
    assert len(window._windows) < PRUNE_AFTER_ENTRIES + 50


# Paths that are neither exempt nor health-checked, so the limiter actually
# applies. Both need no database: the limiter runs before routing and auth, and
# an unknown auth path 404s without touching the session.
LIMITED_PATH = "/api/v1/reports/catalog"
AUTH_PATH = "/api/v1/auth/no-such-endpoint"


def test_api_returns_429_with_retry_headers_when_limited(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    limiter.reset()

    codes = [client.get(LIMITED_PATH).status_code for _ in range(4)]
    assert codes[:2] == [401, 401]  # inside budget: the request is served normally
    assert codes[2:] == [429, 429]


def test_rate_limited_response_uses_the_standard_error_envelope(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    limiter.reset()

    client.get(LIMITED_PATH)
    blocked = client.get(LIMITED_PATH)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["Retry-After"]) > 0
    assert blocked.headers["X-RateLimit-Limit"] == "1"
    limiter.reset()


def test_successful_response_advertises_the_remaining_budget(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 10)
    limiter.reset()

    response = client.get(LIMITED_PATH)
    assert response.headers["X-RateLimit-Limit"] == "10"
    assert response.headers["X-RateLimit-Remaining"] == "9"


def test_auth_routes_get_a_tighter_budget_than_the_general_limit(client: TestClient, monkeypatch) -> None:
    """Credential guessing is throttled well before a normal caller runs out."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 100)
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    limiter.reset()

    codes = [client.get(AUTH_PATH).status_code for _ in range(3)]
    assert codes == [404, 404, 429], "the auth bucket must bite long before the general limit of 100"


def test_health_stays_reachable_while_saturated(client: TestClient, monkeypatch) -> None:
    """An orchestrator probe must not be throttled, or a traffic spike kills the container."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    limiter.reset()

    for _ in range(5):
        assert client.get("/api/v1/health").status_code == 200
    limiter.reset()


def test_forwarded_header_cannot_bypass_the_limit_by_default(client: TestClient, monkeypatch) -> None:
    """Without an explicit opt-in, a forged X-Forwarded-For must not reset the budget."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_trust_proxy", False)
    limiter.reset()

    for _ in range(2):
        client.get(LIMITED_PATH, headers={"X-Forwarded-For": "203.0.113.1"})
    spoofed = client.get(LIMITED_PATH, headers={"X-Forwarded-For": "198.51.100.9"})
    assert spoofed.status_code == 429
    limiter.reset()


def test_forwarded_header_is_honoured_behind_a_trusted_proxy(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    monkeypatch.setattr(settings, "rate_limit_trust_proxy", True)
    limiter.reset()

    assert client.get(LIMITED_PATH, headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 401
    # A different real client gets its own budget instead of sharing the proxy's.
    assert client.get(LIMITED_PATH, headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 401
    assert client.get(LIMITED_PATH, headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 429
    limiter.reset()


def test_exempt_paths_matching_survives_the_api_prefix(monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_exempt_paths", "/health,/ready")
    assert any("/api/v1/health".endswith(entry) for entry in settings.rate_limit_exempt_path_list)


# --------------------------------------------------------------------------- token parsing


def test_token_with_non_numeric_subject_is_rejected(client: TestClient) -> None:
    # A token whose sub cannot be loaded must be a 401, not an unhandled ValueError -> 500.
    token = jwt.encode(
        {"sub": "not-a-user", "type": "access", "exp": int(time.time()) + 300},
        "change-me",
        algorithm="HS256",
    )
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_token_without_a_subject_is_rejected(client: TestClient) -> None:
    token = jwt.encode({"type": "access", "exp": int(time.time()) + 300}, "change-me", algorithm="HS256")
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_with_non_positive_subject_is_rejected() -> None:
    token = jwt.encode({"sub": "0", "type": "access", "exp": int(time.time()) + 300}, "change-me", algorithm="HS256")
    with pytest.raises(ValueError, match="Invalid token subject"):
        decode_token(token)


def test_refresh_token_cannot_be_used_as_an_access_token() -> None:
    token = jwt.encode({"sub": "1", "type": "refresh", "exp": int(time.time()) + 300}, "change-me", algorithm="HS256")
    with pytest.raises(ValueError, match="Unexpected token type"):
        decode_token(token)


def test_token_signed_with_another_key_is_rejected(monkeypatch) -> None:
    from app.core.config import settings

    token = create_access_token("1")
    monkeypatch.setattr(settings, "jwt_secret", "a-completely-different-secret")
    with pytest.raises(ValueError, match="Invalid or expired token"):
        decode_token(token)


def test_valid_access_token_still_round_trips() -> None:
    assert decode_token(create_access_token("42"))["sub"] == "42"
