"""Smoke tests that run with no external services."""
from __future__ import annotations

from app.main import app


def test_app_routes_registered() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/v1/auth/register" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/me" in paths
    assert "/api/v1/health" in paths
    assert "/api/v1/ready" in paths


def test_root_returns_metadata(client) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Customer360 AI"
    assert "docs" in body


def test_unknown_route_returns_404(client) -> None:
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404


def test_validation_error_envelope(client) -> None:
    resp = client.post("/api/v1/auth/register", json={"email": "not-an-email", "password": "x"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"