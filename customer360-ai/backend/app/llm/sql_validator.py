"""SQL validation for the NL-to-SQL module (safety-critical).

Only SELECT statements pass. DML/DDL, semicolons, stacked statements,
comments, and unsafe keywords are rejected before anything executes.

On top of the SELECT-only check this module enforces *tenant scope*: a query
that does not constrain ``organization_id`` to the caller's organization is
rejected, and single-table queries get the predicate injected automatically.
This is the last line of defence for multi-tenant isolation — the generated
SQL is never trusted.
"""
from __future__ import annotations

import re

from app.core.exceptions import ValidationError

FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|merge|call|exec|execute|"
    r"copy|vacuum|analyze|reindex|pg_sleep|load_file|into\s+outfile|information_schema|"
    r"pg_read_file|pg_ls_dir|set\s+role|do|lock)\b",
    re.IGNORECASE,
)

FORBIDDEN_CLAUSES = (";", "--", "/*", "*/", "//")

# Tables the analyst may read. Anything else (audit logs, users, orgs, model
# artifacts) is out of scope even for a SELECT.
ALLOWED_TABLES = frozenset(
    {
        "customers",
        "products",
        "transactions",
        "customer_features",
        "segments",
        "customer_segments",
        "predictions",
        "recommendations",
    }
)

_TABLE_REF = re.compile(
    r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)"
    r"(?:\s+(?:as\s+)?([a-z_][a-z0-9_]*))?",
    re.IGNORECASE,
)

_ALIAS_STOPWORDS = frozenset(
    {
        "where",
        "group",
        "order",
        "having",
        "limit",
        "offset",
        "join",
        "inner",
        "left",
        "right",
        "full",
        "cross",
        "on",
        "union",
        "except",
        "intersect",
    }
)

_TAIL_CLAUSES = (" group by ", " order by ", " having ", " limit ", " offset ", " union ", " intersect ", " except ")
_SUBQUERY = re.compile(r"\(\s*select\b", re.IGNORECASE)
_WHERE = re.compile(r"\bwhere\b", re.IGNORECASE)

MAX_ROWS = 500
MAX_LIMIT = 1000
_ALLOWED_LIMIT = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)


def validate_select_only(sql: str) -> str:
    """Return the normalized SQL if it is SELECT-only, else raise ValidationError."""
    if not sql or not sql.strip():
        raise ValidationError("Generated SQL is empty.")
    stripped = sql.strip()
    if not stripped.lower().startswith("select"):
        raise ValidationError("Only SELECT queries are allowed.")
    if any(token in sql for token in FORBIDDEN_CLAUSES):
        raise ValidationError("Multiple statements and comments are not allowed.")
    if FORBIDDEN_KEYWORDS.search(sql):
        raise ValidationError("SQL contains a forbidden keyword.")
    return sql


def referenced_tables(sql: str) -> list[str]:
    """Return the (lowercase) table names referenced in FROM/JOIN clauses."""
    tables: list[str] = []
    for match in _TABLE_REF.finditer(sql):
        name = match.group(1).lower()
        if name in _ALIAS_STOPWORDS or name == "select":
            continue
        if name not in tables:
            tables.append(name)
    return tables


def _table_aliases(sql: str) -> dict[str, str]:
    """Map alias -> table for each FROM/JOIN reference (alias defaults to table)."""
    mapping: dict[str, str] = {}
    for match in _TABLE_REF.finditer(sql):
        table = match.group(1).lower()
        alias = (match.group(2) or table).lower()
        if alias in _ALIAS_STOPWORDS:
            alias = table
        mapping[alias] = table
    return mapping


def assert_allowed_tables(sql: str) -> list[str]:
    """Reject queries touching tables outside the curated analyst schema."""
    tables = referenced_tables(sql)
    if not tables:
        raise ValidationError("Could not determine which table the query reads.")
    blocked = [table for table in tables if table not in ALLOWED_TABLES]
    if blocked:
        raise ValidationError(f"Table not available to the analyst: {', '.join(sorted(blocked))}.")
    return tables


def enforce_tenant_scope(sql: str, *, organization_id: int) -> str:
    """Guarantee the query is scoped to one organization.

    Single-table queries get ``organization_id = <org>`` injected. Multi-table
    queries must already constrain every table (by alias or unqualified), so a
    join can never leak another tenant's rows.
    """
    aliases = _table_aliases(sql)
    if not aliases:
        raise ValidationError("Could not determine which table the query reads.")
    if _SUBQUERY.search(sql):
        # A derived table can hide the real scope; refuse rather than guess.
        raise ValidationError("Subqueries are not allowed in analyst SQL.")
    if len(set(aliases.values())) == 1:
        return _inject_single_table_scope(sql, organization_id)
    return _verify_multi_table_scope(sql, aliases, organization_id=organization_id)


def _inject_single_table_scope(sql: str, organization_id: int) -> str:
    stripped = sql.rstrip().rstrip(";")
    if _has_org_predicate(stripped, alias=None, organization_id=organization_id):
        return stripped
    head, tail = _split_tail(stripped)
    predicate = f"organization_id = {organization_id}"
    joined = f"{head} AND {predicate}" if _WHERE.search(head) else f"{head} WHERE {predicate}"
    return " ".join(part for part in (joined, tail) if part)


def _split_tail(sql: str) -> tuple[str, str]:
    """Split a SELECT into the part before GROUP BY/ORDER BY/LIMIT and the rest."""
    lowered = sql.lower()
    positions = [lowered.find(clause) for clause in _TAIL_CLAUSES]
    positions = [p for p in positions if p != -1]
    if not positions:
        return sql, ""
    cut = min(positions)
    return sql[:cut].rstrip(), sql[cut:]


def _verify_multi_table_scope(sql: str, aliases: dict[str, str], *, organization_id: int) -> str:
    missing = [
        alias
        for alias in sorted(set(aliases))
        if not _has_org_predicate(sql, alias=alias, organization_id=organization_id)
    ]
    if missing:
        raise ValidationError(
            "Joined queries must scope every table to the organization "
            f"(missing organization_id for: {', '.join(missing)})."
        )
    return sql.rstrip().rstrip(";")


def _has_org_predicate(sql: str, *, alias: str | None, organization_id: int | None = None) -> bool:
    """True when the query pins organization_id to the caller's org.

    With an ``alias`` the predicate must be *qualified* (``t.organization_id =``)
    so that scoping one table of a join can never vouch for another.
    """
    if alias is None:
        pattern = re.compile(r"\borganization_id\s*=\s*(\d+)", re.IGNORECASE)
    else:
        pattern = re.compile(rf"\b{re.escape(alias)}\s*\.\s*\borganization_id\s*=\s*(\d+)", re.IGNORECASE)
    return any(
        organization_id is None or int(match.group(1)) == organization_id
        for match in pattern.finditer(sql)
    )


def apply_readonly_guards(sql: str, *, organization_id: int, max_rows: int = MAX_ROWS) -> str:
    """Full guard chain: SELECT-only -> allowed tables -> tenant scope -> row cap."""
    clean = validate_select_only(sql)
    assert_allowed_tables(clean)
    scoped = enforce_tenant_scope(clean, organization_id=organization_id)
    return cap_rows(scoped, max_rows=max_rows)


def cap_rows(sql: str, *, max_rows: int = MAX_ROWS) -> str:
    """Clamp the row cap so a model cannot ask for an unbounded result set."""
    ceiling = min(int(max_rows), MAX_LIMIT)
    match = _ALLOWED_LIMIT.search(sql)
    if match is None:
        guarded = sql.rstrip()
        return f"{guarded} LIMIT {ceiling}"
    if int(match.group(1)) > ceiling:
        return sql[: match.start(1)] + str(ceiling) + sql[match.end(1) :]
    return sql
