"""SQL guard tests: the safety contract for NL-to-SQL.

These are the tests that matter most in this module - a leak here is a
cross-tenant data breach, so every guard is pinned individually.
"""
from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.llm.sql_validator import (
    ALLOWED_TABLES,
    apply_readonly_guards,
    assert_allowed_tables,
    cap_rows,
    enforce_tenant_scope,
    referenced_tables,
    validate_select_only,
)

ORG = 42


def test_rejects_non_select() -> None:
    for sql in (
        "UPDATE customers SET name = 'x' WHERE organization_id = 42",
        "DELETE FROM transactions",
        "DROP TABLE customers",
        "INSERT INTO customers (id) VALUES (1)",
        "TRUNCATE customers",
        "GRANT ALL ON customers TO public",
    ):
        with pytest.raises(ValidationError):
            apply_readonly_guards(sql, organization_id=ORG)


def test_rejects_stacked_statements_and_comments() -> None:
    for sql in (
        "SELECT * FROM customers; DROP TABLE customers",
        "SELECT * FROM customers -- drop everything",
        "SELECT * FROM customers /* sneaky */",
    ):
        with pytest.raises(ValidationError):
            apply_readonly_guards(sql, organization_id=ORG)


def test_rejects_dangerous_constructs() -> None:
    for sql in (
        "SELECT * FROM customers WHERE 1=1 UNION SELECT password FROM users",
        "SELECT pg_sleep(10) FROM customers",
        "SELECT load_file('/etc/passwd')",
        "SELECT * FROM information_schema.tables",
    ):
        with pytest.raises(ValidationError):
            apply_readonly_guards(sql, organization_id=ORG)


def test_rejects_tables_outside_the_analyst_schema() -> None:
    for table in ("users", "organizations", "audit_logs", "model_versions", "campaigns"):
        with pytest.raises(ValidationError):
            apply_readonly_guards(f"SELECT * FROM {table}", organization_id=ORG)
    assert "users" not in ALLOWED_TABLES
    assert "customers" in ALLOWED_TABLES


def test_rejects_subqueries() -> None:
    with pytest.raises(ValidationError):
        apply_readonly_guards(
            "SELECT * FROM customers WHERE name IN (SELECT name FROM customers WHERE organization_id = 7)",
            organization_id=ORG,
        )


def test_rejects_empty_and_malformed() -> None:
    with pytest.raises(ValidationError):
        validate_select_only("")
    with pytest.raises(ValidationError):
        apply_readonly_guards("SELECT 1", organization_id=ORG)  # no table to scope


def test_injects_tenant_scope_without_where() -> None:
    guarded = apply_readonly_guards("SELECT * FROM customers", organization_id=ORG)
    assert "WHERE organization_id = 42" in guarded
    assert "LIMIT 500" in guarded


def test_injects_tenant_scope_with_where_and_order_by() -> None:
    guarded = apply_readonly_guards(
        "SELECT name FROM customer_features WHERE frequency > 2 ORDER BY name",
        organization_id=ORG,
    )
    assert "AND organization_id = 42" in guarded
    assert guarded.index("organization_id") < guarded.index("ORDER BY")


def test_injects_tenant_scope_with_group_by() -> None:
    guarded = apply_readonly_guards(
        "SELECT rfm_segment, COUNT(*) FROM customer_features GROUP BY rfm_segment",
        organization_id=ORG,
    )
    assert "WHERE organization_id = 42" in guarded
    assert guarded.index("organization_id") < guarded.index("GROUP BY")


def test_keeps_explicit_matching_scope() -> None:
    sql = "SELECT * FROM customers WHERE organization_id = 42"
    assert enforce_tenant_scope(sql, organization_id=ORG).strip() == sql


def test_never_honours_a_foreign_organization() -> None:
    """A model-supplied org id cannot widen access; the caller's org always wins."""
    guarded = apply_readonly_guards("SELECT * FROM customers WHERE organization_id = 7", organization_id=ORG)
    assert "organization_id = 42" in guarded


def test_join_requires_every_table_to_be_scoped() -> None:
    unsafe = "SELECT c.name, t.amount FROM customers c JOIN transactions t ON t.customer_id = c.id"
    with pytest.raises(ValidationError):
        apply_readonly_guards(unsafe, organization_id=ORG)

    safe = (
        "SELECT c.name, t.amount FROM customers c JOIN transactions t ON t.customer_id = c.id "
        f"WHERE c.organization_id = {ORG} AND t.organization_id = {ORG}"
    )
    guarded = apply_readonly_guards(safe, organization_id=ORG)
    assert f"c.organization_id = {ORG}" in guarded
    assert f"t.organization_id = {ORG}" in guarded


def test_join_with_foreign_org_is_rejected() -> None:
    unsafe = (
        "SELECT c.name FROM customers c JOIN transactions t ON t.customer_id = c.id "
        f"WHERE c.organization_id = {ORG} AND t.organization_id = 7"
    )
    with pytest.raises(ValidationError):
        apply_readonly_guards(unsafe, organization_id=ORG)


def test_row_cap_is_clamped() -> None:
    assert cap_rows("SELECT * FROM customers", max_rows=200).endswith("LIMIT 200")
    assert cap_rows("SELECT * FROM customers LIMIT 5", max_rows=200).endswith("LIMIT 5")
    assert cap_rows("SELECT * FROM customers LIMIT 5000", max_rows=200).endswith("LIMIT 200")
    # absolute ceiling, even if a caller asks for more
    assert cap_rows("SELECT * FROM customers", max_rows=99999).endswith("LIMIT 1000")


def test_referenced_tables() -> None:
    assert referenced_tables("SELECT * FROM customers c JOIN transactions t ON t.customer_id = c.id") == [
        "customers",
        "transactions",
    ]
    assert referenced_tables("SELECT * FROM customers WHERE name = 'x'") == ["customers"]


def test_assert_allowed_tables_returns_tables() -> None:
    assert assert_allowed_tables("SELECT * FROM products") == ["products"]
