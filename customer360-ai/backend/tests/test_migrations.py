"""Migration/metadata consistency.

`alembic check` is the authoritative guard, and CI runs it against real
PostgreSQL. It cannot run here (no local Postgres, and autogenerating against
SQLite reports phantom diffs for SERIAL/timestamptz), so this module asserts the
same invariant the cheap way: every index a hand-written revision creates is
declared in the model metadata, and every revision drops what it created.

That is not a substitute for `alembic check`, but it catches the specific class
of bug where a migration creates an index the model does not know about - which
Phase 11 shipped for `monitoring_runs`, and which would otherwise have surfaced
as a CI failure on the first run of the new drift check.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

import app.models  # noqa: F401
from app.db.base import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BACKEND_DIR / "migrations" / "versions"
# 0001 is a raw SQL dump; the later revisions are hand-written Python.
INDEX_REVISIONS = ["0002_monitoring_runs.py", "0003_query_indexes.py"]
TABLE_REVISIONS = ["0004_token_store.py"]
HAND_WRITTEN = INDEX_REVISIONS + TABLE_REVISIONS

CREATE_INDEX = re.compile(r"""create_index\(\s*["']([^"']+)["']""")
DROP_INDEX = re.compile(r"""drop_index\(\s*["']([^"']+)["']""")
# 0004 writes its DDL through op.execute, so the tables are found in SQL text
# rather than through a helper call.
CREATE_TABLE = re.compile(r"CREATE TABLE\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
DROP_TABLE = re.compile(r"""drop_table\(\s*["']([^"']+)["']""", re.IGNORECASE)


def _load(filename: str):
    path = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _created(module) -> set[str]:
    """Index names a revision's upgrade() creates.

    Reads the module's INDEXES tuple when it has one (0003 declares them once and
    loops), otherwise the literal names in the upgrade body (0002 writes them out).
    """
    declared = getattr(module, "INDEXES", None)
    if declared:
        return {name for name, _table, _columns in declared}
    return set(CREATE_INDEX.findall(inspect.getsource(module.upgrade)))


def _dropped(module) -> set[str]:
    """Index names a revision's downgrade() drops, including looped ones."""
    source = inspect.getsource(module.downgrade)
    literal = set(DROP_INDEX.findall(source))
    declared = getattr(module, "INDEXES", None)
    if declared and "reversed(INDEXES)" in source:
        # The loop drops the same tuple the upgrade created, in reverse order.
        return {name for name, _table, _columns in declared}
    return literal


def _metadata_indexes() -> dict[str, set[str]]:
    return {table.name: {index.name for index in table.indexes} for table in Base.metadata.tables.values()}


def _all_metadata_index_names() -> set[str]:
    return {name for indexes in _metadata_indexes().values() for name in indexes}


def test_revision_chain_is_linear() -> None:
    script = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))
    revisions = [rev.revision for rev in script.walk_revisions()]
    assert revisions == ["0004", "0003", "0002", "0001"], "a missing or forked revision breaks `alembic upgrade head`"


@pytest.mark.parametrize("filename", HAND_WRITTEN)
def test_migration_declares_a_downgrade(filename: str) -> None:
    module = _load(filename)
    assert callable(module.downgrade)
    assert module.down_revision is not None


@pytest.mark.parametrize("filename", INDEX_REVISIONS)
def test_migration_indexes_are_declared_in_the_models(filename: str) -> None:
    created = _created(_load(filename))
    assert created, f"{filename} creates no index; the parser found nothing to check"
    metadata_names = _all_metadata_index_names()
    undeclared = created - metadata_names
    assert not undeclared, (
        f"{filename} creates {sorted(undeclared)} but no model declares it - alembic check would "
        "report drift and ask to drop an index the queries rely on"
    )


@pytest.mark.parametrize("filename", INDEX_REVISIONS)
def test_downgrade_drops_what_upgrade_created(filename: str) -> None:
    module = _load(filename)
    created, dropped = _created(module), _dropped(module)
    assert created == dropped, f"{filename}: an index left behind by downgrade fails the next upgrade"


@pytest.mark.parametrize("filename", TABLE_REVISIONS)
def test_created_tables_are_dropped_on_downgrade(filename: str) -> None:
    """A table left behind makes the next `upgrade` fail on "already exists"."""
    module = _load(filename)
    created = set(CREATE_TABLE.findall(inspect.getsource(module.upgrade)))
    dropped = set(DROP_TABLE.findall(inspect.getsource(module.downgrade)))
    assert created, f"{filename} creates no table; the parser found nothing to check"
    assert created == dropped


@pytest.mark.parametrize("filename", TABLE_REVISIONS)
def test_created_tables_exist_in_the_model_metadata(filename: str) -> None:
    created = set(CREATE_TABLE.findall(inspect.getsource(_load(filename).upgrade)))
    metadata_tables = set(Base.metadata.tables)
    missing = created - metadata_tables
    assert not missing, (
        f"{filename} creates {sorted(missing)} but no model declares them - alembic check reports "
        "drift and the application queries a table the migration never made"
    )


def test_monitoring_run_index_survives_metadata_round_trip() -> None:
    """The index the drift run-history and prediction-baseline queries depend on."""
    assert "ix_monitoring_runs_org_version_created" in _metadata_indexes()["monitoring_runs"]


def test_phase_12_query_indexes_are_declared() -> None:
    indexes = _metadata_indexes()
    assert "ix_predictions_org_version_customer" in indexes["predictions"]
    assert "ix_model_versions_org_active" in indexes["model_versions"]
    assert "ix_cfeatures_org_version" in indexes["customer_features"]


def test_no_duplicate_index_names_across_tables() -> None:
    """PostgreSQL requires index names to be unique per schema."""
    seen: set[str] = set()
    for table, indexes in _metadata_indexes().items():
        for name in indexes:
            assert name not in seen, f"{name} is declared on more than one table ({table})"
            seen.add(name)


def test_token_store_indexes_are_declared() -> None:
    """Same drift trap as the Phase 11 monitoring index, for the new tables."""
    indexes = _metadata_indexes()
    assert "ix_refresh_tokens_user_revoked" in indexes["refresh_tokens"]
    assert "ix_refresh_tokens_family" in indexes["refresh_tokens"]
    assert "ix_login_failures_locked_until" in indexes["login_failures"]


def test_refresh_token_unique_constraint_is_named_in_both_places() -> None:
    """A mismatch here makes `alembic check` ask for a constraint the app relies on."""
    migration = inspect.getsource(_load("0004_token_store.py").upgrade)
    assert "uq_refresh_tokens_jti" in migration
    assert "uq_login_failures_email" in migration

    from sqlalchemy import UniqueConstraint

    from app.models.auth_tokens import LoginFailure, RefreshToken

    for model, expected in ((RefreshToken, "uq_refresh_tokens_jti"), (LoginFailure, "uq_login_failures_email")):
        names = {
            constraint.name
            for constraint in model.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert expected in names, f"{model.__tablename__} is missing the {expected} constraint"
