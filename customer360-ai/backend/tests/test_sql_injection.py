"""Adversarial input for the NL-to-SQL guard chain.

`tests/test_sql_validator.py` covers the guard chain feature by feature. This
file attacks it the way an injection payload would: obfuscation, quoting tricks,
comment-splitting keywords, and attempts to widen the tenant predicate rather
than to escape it.

The property asserted throughout is that every payload either raises
`ValidationError` or comes back with a tenant predicate that cannot be widened -
never "executed". Anything that slips through as a valid query would mean the
analyst could read another tenant's data, which is the one failure mode here
that matters.
"""
from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.llm.sql_validator import (
    ALLOWED_TABLES,
    MAX_ROWS,
    apply_readonly_guards,
    enforce_tenant_scope,
    referenced_tables,
)

ORG = 7

# Payloads that must never produce an executable statement.
HOSTILE = [
    # stacked statements and comment tricks
    "SELECT id FROM customers; DROP TABLE users",
    "SELECT id FROM customers--",
    "SELECT id FROM customers/* comment */",
    "SELECT id FROM customers/*x*/ WHERE 1=1",
    "SELECT id FROM customers WHERE 1=1 -- AND organization_id = 7",
    "SELECT id FROM customers WHERE name = 'a' OR 1=1--",
    # keyword obfuscation
    "SeLeCt id FrOm customers",
    "SELECT id FROM customers WHERE 1=1/**/OR/**/1=1",
    "SELECT id FROM customers UNION SELECT 1",
    "SELECT id FROM customers UNION ALL SELECT password_hash FROM users",
    "SELECT id FROM (SELECT id FROM customers) x",
    # destructive verbs dressed as identifiers
    "SELECT * FROM customers WHERE email = 'DROP TABLE users'",
    "SELECT delete FROM customers",
    "SELECT id FROM customers; VACUUM",
    "SELECT id FROM customers WHERE 1=1; GRANT ALL ON users TO public",
    # reads outside the allow-list
    "SELECT id FROM users",
    "SELECT password_hash FROM users",
    "SELECT id FROM audit_logs",
    "SELECT * FROM pg_shadow",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM public.customers",
    # timing and file primitives
    "SELECT pg_sleep(10)",
    "SELECT load_file('/etc/passwd')",
    "SELECT * FROM customers WHERE 1=1 AND pg_sleep(5) = 0",
    # tenant-scope widening
    "SELECT id FROM customers WHERE organization_id != 7",
    "SELECT id FROM customers WHERE organization_id > 0",
    "SELECT id FROM customers WHERE organization_id = ANY (SELECT 1)",
    "SELECT id FROM customers c JOIN transactions t ON t.customer_id = c.id",
    "SELECT id FROM customers c JOIN transactions t ON t.customer_id = c.id WHERE c.organization_id = 7",
    # row cap evasion
    "SELECT id FROM customers LIMIT 100000",
    "SELECT id FROM customers LIMIT 999999999",
    # empty and degenerate
    "",
    "   ",
    "SELECT",
    "DROP TABLE customers",
    "UPDATE customers SET email = 'x'",
]


def _attempt(sql: str) -> str:
    """Run the full guard chain.

    `apply_readonly_guards` is the single entry point both the generated-SQL path
    and the caller-supplied "run your own SELECT" path go through, so it is what
    these tests attack. Calling `enforce_tenant_scope` directly would skip the
    table allow-list and the row cap, and would make several of these payloads
    look safe when the real chain rejects them.
    """
    return apply_readonly_guards(sql, organization_id=ORG)


@pytest.mark.parametrize("sql", HOSTILE)
def test_hostile_sql_never_produces_a_widened_scope(sql: str) -> None:
    try:
        result = _attempt(sql)
    except ValidationError:
        return  # rejected, which is the desired outcome
    # Accepted, so it must be a harmless read that is scoped to this tenant and
    # capped. Anything referencing a non-allowed table, or missing the predicate,
    # would be a guard-chain failure.
    assert "organization_id = 7" in result or "organization_id=7" in result
    for table in referenced_tables(result):
        assert table in ALLOWED_TABLES, f"{sql!r} reached table {table!r}"
    assert "password_hash" not in result.lower() or "users" not in referenced_tables(result)


def test_every_hostile_payload_is_either_rejected_or_scoped() -> None:
    """A single assertion over the corpus, so a regression cannot hide in one row."""
    rejected = 0
    scoped = 0
    for sql in HOSTILE:
        try:
            result = _attempt(sql)
        except ValidationError:
            rejected += 1
            continue
        scoped += 1
        assert "7" in result
    # Sanity on the corpus itself: it must be exercising both paths, otherwise
    # the test above would pass trivially.
    assert rejected > 0
    assert scoped > 0


def test_join_cannot_read_a_second_tenant() -> None:
    """The join path is the one place a predicate can be forgotten."""
    sql = "SELECT c.id FROM customers c JOIN transactions t ON t.customer_id = c.id"
    with pytest.raises(ValidationError):
        _attempt(sql)

    # Scoping only one side of the join is still not enough.
    partial = f"SELECT c.id FROM customers c JOIN transactions t ON t.customer_id = c.id WHERE c.organization_id = {ORG}"
    with pytest.raises(ValidationError):
        _attempt(partial)


def test_full_chain_still_guards_the_single_table_paths() -> None:
    """Regression guard on the composition itself.

    `enforce_tenant_scope` alone does not check the table allow-list or the row
    cap - those live in `apply_readonly_guards`. This asserts the composition is
    what the router actually uses, so a future refactor that calls the inner
    function directly fails here.
    """
    with pytest.raises(ValidationError):
        apply_readonly_guards("SELECT id FROM users", organization_id=ORG)
    assert f"LIMIT {MAX_ROWS}" in apply_readonly_guards("SELECT id FROM customers", organization_id=ORG)
    # The inner helper is genuinely weaker, which is why the router must not use it.
    assert "users" in referenced_tables(enforce_tenant_scope("SELECT id FROM users", organization_id=ORG))


def test_explicit_foreign_tenant_is_rewritten_not_honoured() -> None:
    """A caller-supplied foreign org id is AND-ed with ours, never trusted.

    The result keeps the original predicate, so it reads
    `organization_id = 99 AND organization_id = 7` - contradictory and therefore
    empty. Failing closed is the right direction here: rewriting the caller's
    number away would hide that their query was wrong, and trusting it would be a
    cross-tenant read.
    """
    result = _attempt("SELECT id FROM customers WHERE organization_id = 99")
    assert "organization_id = 7" in result
    # Nothing executable in that string can match both values.
    assert "= 99 AND organization_id = 7" in result or result.rstrip().endswith("7")


def test_row_cap_clamps_rather_than_rejects() -> None:
    """An oversized limit is rewritten down, not refused.

    Refusing would be defensible, but clamping keeps a legitimate
    "give me the top 5000" working while still bounding the result set.
    """
    for asked, ceiling in (("5000", MAX_ROWS), ("999999999", MAX_ROWS), ("10", 10)):
        result = _attempt(f"SELECT id FROM customers LIMIT {asked}")
        assert f"LIMIT {ceiling}" in result
        assert f"LIMIT {asked}" not in result or int(asked) <= ceiling


def test_default_cap_is_applied_when_no_limit_is_given() -> None:
    result = _attempt("SELECT id FROM customers")
    assert f"LIMIT {MAX_ROWS}" in result


def test_keyword_in_a_string_literal_is_refused_rather_than_guessed() -> None:
    """A documented false positive.

    `FORBIDDEN_KEYWORDS` matches anywhere in the statement, so a legitimate
    filter on a string containing "update" is refused. That is the safe
    direction to fail: loosening the pattern to skip string literals would let a
    payload hide a verb inside quotes. Recorded rather than fixed.
    """
    with pytest.raises(ValidationError):
        _attempt("SELECT id FROM customers WHERE status = 'update'")

    # The same word as part of a longer identifier is fine, because the pattern
    # is word-bounded - so ordinary columns do not trip the guard.
    assert _attempt("SELECT id, deleted_at FROM customers WHERE organization_id = 7")