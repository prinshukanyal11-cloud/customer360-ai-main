"""NL-to-SQL tests: intent matching, guarded execution, tenant isolation."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.exceptions import ValidationError
from app.db.base import Base
from app.llm.nl2sql import (
    INTENTS,
    extract_sql,
    generate_sql,
    match_intent,
    run_query,
    sql_for_intent,
)
from app.models.transaction import Customer, Product, Transaction
from app.models.user import Organization


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed(session) -> tuple[int, int]:
    """Two organizations with interleaved customers; returns (org_a, org_b)."""
    org_a = Organization(name="Acme", slug="acme")
    org_b = Organization(name="Globex", slug="globex")
    session.add_all([org_a, org_b])
    session.flush()
    for org, prefix, amount in ((org_a, "A", 100.0), (org_b, "B", 999.0)):
        for i in range(1, 3):
            customer = Customer(
                organization_id=org.id,
                external_customer_id=f"{prefix}{i}",
                name=f"{prefix} Customer {i}",
            )
            session.add(customer)
            session.flush()
            product = Product(
                organization_id=org.id,
                external_product_id=f"{prefix}-P{i}",
                product_name=f"{prefix} Product {i}",
                category=f"{prefix}-Cat",
            )
            session.add(product)
            session.flush()
            session.add(
                Transaction(
                    organization_id=org.id,
                    customer_id=customer.id,
                    external_order_id=f"{prefix}-O{i}",
                    order_date=date(2024, 3, i),
                    product_id=product.id,
                    quantity=1,
                    amount=amount,
                )
            )
    session.commit()
    return org_a.id, org_b.id


def test_match_intent_covers_common_questions() -> None:
    assert match_intent("how many customers do we have?").name == "customer_counts"
    assert match_intent("which customers are at risk of churning?").name == "at_risk_customers"
    assert match_intent("show me the revenue trend by month").name == "revenue_by_month"
    assert match_intent("which product categories perform best?").name == "category_performance"
    assert match_intent("who should we prioritize?").name == "top_customers"
    assert match_intent("what did the swallows carry") is None


def test_every_intent_template_is_tenant_scoped() -> None:
    """No template may ship without an org predicate on each table it reads."""
    for intent in INTENTS:
        sql = sql_for_intent(intent, 7)
        assert "organization_id = 7" in sql, intent.name
        assert sql.upper().startswith("SELECT"), intent.name


def test_generate_sql_uses_templates_when_configured() -> None:
    sql, source, intent = generate_sql("how many customers do we have?", 7)
    assert intent == "customer_counts"
    assert source == "template:customer_counts"
    assert "organization_id = 7" in sql
    assert "{org}" not in sql


def test_generate_sql_refuses_open_questions_without_a_provider() -> None:
    with pytest.raises(ValidationError) as excinfo:
        generate_sql("what is the airspeed velocity of a swallow", 7)
    assert "customer_counts" in str(excinfo.value)


def test_extract_sql_strips_fences_and_prose() -> None:
    assert extract_sql("```sql\nSELECT 1 FROM customers\n```").startswith("SELECT")
    assert extract_sql("Here you go:\nSELECT name FROM customers;\nHope that helps").endswith("customers")
    with pytest.raises(ValidationError):
        extract_sql("I am not sure how to answer that.")


def test_run_query_executes_scoped_template(db) -> None:
    org_a, _org_b = _seed(db)
    result = run_query(db, org_a, "how many customers do we have?")
    assert result["executed"] is True
    assert result["source"] == "template:customer_counts"
    assert result["row_count"] == 1
    assert result["rows"][0]["customers"] == 2
    assert "organization_id = %d" % org_a in result["sql"]


def test_run_query_never_leaks_another_tenant(db) -> None:
    org_a, org_b = _seed(db)
    result = run_query(db, org_a, "show me the most recent orders")
    names = {row["name"] for row in result["rows"]}
    assert names == {"A Customer 1", "A Customer 2"}
    assert not any(name.startswith("B") for name in names)
    assert f"organization_id = {org_a}" in result["sql"]
    assert f"organization_id = {org_b}" not in result["sql"]


def test_run_query_enforces_tenant_scope_on_manual_sql(db) -> None:
    org_a, _org_b = _seed(db)
    result = run_query(
        db,
        org_a,
        "all customers please",
        generated_sql="SELECT name FROM customers ORDER BY name",
    )
    assert result["source"] == "manual"
    assert {row["name"] for row in result["rows"]} == {"A Customer 1", "A Customer 2"}


def test_manual_sql_for_a_foreign_org_returns_nothing(db) -> None:
    org_a, org_b = _seed(db)
    result = run_query(
        db,
        org_a,
        "sneaky",
        generated_sql=f"SELECT name FROM customers WHERE organization_id = {org_b}",
    )
    assert result["rows"] == []


def test_manual_sql_is_rejected_when_unsafe(db) -> None:
    _seed(db)
    with pytest.raises(ValidationError):
        run_query(db, 1, "drop everything", generated_sql="DELETE FROM customers")


def test_execution_errors_are_reported_as_validation_errors(db) -> None:
    _seed(db)
    with pytest.raises(ValidationError) as excinfo:
        run_query(db, 1, "broken", generated_sql="SELECT no_such_column FROM customers")
    assert "could not be executed" in str(excinfo.value)


def test_customer_count_ignores_other_orgs_in_the_table(db) -> None:
    org_a, org_b = _seed(db)
    total = db.execute(select(Transaction)).scalars().all()
    assert len({t.organization_id for t in total}) == 2
    assert org_a != org_b
    result = run_query(db, org_a, "how many customers do we have?")
    assert result["rows"][0]["customers"] == 2
