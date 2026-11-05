"""Shared pytest fixtures.

SQLite is used for unit tests so they run without a local PostgreSQL.
Integration tests against PostgreSQL use DATABASE_URL_TEST.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.security_deps import get_db
from app.db.base import Base
from app.main import app


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch) -> None:
    import app.services.dataset_service as ds

    monkeypatch.setattr(ds, "UPLOAD_DIR", tmp_path / "uploads")


@pytest.fixture(autouse=True)
def _rate_limiter_off(monkeypatch) -> Generator[None, None, None]:
    """Disable the limiter for the general suite.

    Every TestClient request arrives from the same host, so a live per-IP budget
    would start rejecting unrelated tests once the suite passes ~120 calls. Tests
    that are actually about throttling re-enable it explicitly.
    """
    from app.core.config import settings
    from app.middleware.rate_limit import limiter

    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- db-backed client
# The `client` fixture above runs the real app with the real session dependency,
# which is fine for requests that never touch a table. Anything that reads or
# writes needs the SQLite override instead, so these live here rather than being
# imported from whichever test module happened to define them first.


@pytest.fixture()
def api_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield session_factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def api_client(api_session_factory) -> Generator[TestClient, None, None]:
    def override_get_db():
        db = api_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()