"""Prepare a throwaway SQLite database for the Playwright suite.

The API deliberately does not create tables at import time - in a real
deployment the schema comes from Alembic, and having the app quietly
``create_all`` would hide a missing migration. The browser tests still need
somewhere to run without a local PostgreSQL, so this script builds the schema
from the model metadata, the same way the pytest suite does.

Run before uvicorn. Exits non-zero if anything fails, so the Playwright
webServer does not start against a half-built database.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine

import app.models  # noqa: F401  imported for its side effect: model registration
from app.core.config import settings
from app.db.base import Base


def main() -> int:
    url = settings.database_url
    if not url.startswith("sqlite"):
        # A real database is already migrated; nothing to prepare.
        print(f"E2E DB is {url.split(':', 1)[0]}, skipping schema creation.")
        return 0

    db_path = Path(url.split("///", 1)[-1])
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    # Start clean each run. Without this the file grows across runs and a test
    # that asserts on a rejection could pass or fail depending on history.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for stale in (db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
        stale.unlink(missing_ok=True)

    engine = create_engine(url, future=True)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    print(f"E2E DB ready at {db_path} ({len(Base.metadata.tables)} tables).")
    return 0


if __name__ == "__main__":
    sys.exit(main())