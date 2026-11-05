"""Generate the initial Alembic revision from SQLAlchemy metadata.

Usage: python scripts/generate_initial_migration.py
Writes: backend/migrations/versions/0001_initial_schema.py
Runs offline (no database required); the revision works with PostgreSQL.
"""
from __future__ import annotations

from pathlib import Path

import app.models  # noqa: F401 — ensure all tables are registered
import sqlalchemy as sa  # noqa: F401
from app.db.base import metadata
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

OUT = Path(__file__).resolve().parent.parent / "backend" / "migrations" / "versions" / "0001_initial_schema.py"
REVISION = "0001"
DOWN = None


def _normalize(ddl: str) -> str:
    return ddl.expandtabs(4).strip().replace("\n", "\n    ")


def build_revision() -> str:
    buffer: list[str] = []
    buffer.append('"""initial schema\n\nRevision ID: 0001\nCreate Date: 2026-09-24\n"""\n')
    buffer.append("from __future__ import annotations\n\n")
    buffer.append("import sqlalchemy as sa  # noqa: F401\n")
    buffer.append("from alembic import op\n")
    buffer.append("from sqlalchemy.dialects import postgresql  # noqa: F401\n\n")
    buffer.append(f"revision = {REVISION!r}\n")
    buffer.append(f"down_revision = {DOWN!r}\n")
    buffer.append("branch_labels = None\n")
    buffer.append("depends_on = None\n\n\n")

    upgrade: list[str] = ["def upgrade() -> None:\n"]
    downgrade: list[str] = ["def downgrade() -> None:\n"]

    dialect = postgresql.dialect()
    for table in metadata.sorted_tables:
        ddl = _normalize(str(CreateTable(table).compile(dialect=dialect)))
        upgrade.append(f"    op.execute(sa.text('''\n    {ddl}\n    '''))\n")

    index_defs: set[str] = set()
    for table in metadata.sorted_tables:
        for index in table.indexes:
            ddl = _normalize(str(CreateIndex(index).compile(dialect=dialect)))
            if ddl not in index_defs:
                index_defs.add(ddl)
                upgrade.append(f"    op.execute(sa.text('''\n    {ddl}\n    '''))\n")

    for table in reversed(metadata.sorted_tables):
        downgrade.append(f"    op.execute(sa.text('DROP TABLE IF EXISTS {table.name} CASCADE'))\n")

    buffer.append("\n".join(upgrade) + "\n\n\n")
    buffer.append("\n".join(downgrade) + "\n")
    return "".join(buffer)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_revision(), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()