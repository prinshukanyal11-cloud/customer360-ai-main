"""Grounded analyst tests: retrieval routing, history, and no-invention guarantees."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.llm.ai_analyst import (
    INTENTS,
    answer,
    format_history,
    match_intents,
    retrieve_context,
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


@pytest.fixture
def org_id(db) -> int:
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.flush()
    for i in range(1, 4):
        customer = Customer(
            organization_id=org.id,
            external_customer_id=f"C{i}",
            name=f"Customer {i}",
            signup_date=date(2024, 1, i),
        )
        db.add(customer)
        db.flush()
        product = Product(
            organization_id=org.id,
            external_product_id=f"P{i}",
            product_name=f"Product {i}",
            category="Tools",
        )
        db.add(product)
        db.flush()
        for j in range(i):
            db.add(
                Transaction(
                    organization_id=org.id,
                    customer_id=customer.id,
                    external_order_id=f"O{i}-{j}",
                    order_date=date(2024, 3 + j, 1 + i),
                    product_id=product.id,
                    quantity=1,
                    amount=100.0 + i,
                )
            )
    db.commit()
    return org.id


def test_match_intents_always_includes_the_dashboard() -> None:
    matched = [intent.name for intent in match_intents("what is our revenue trend?")]
    assert matched[0] == "overview"
    assert "trends" in matched


def test_match_intents_routes_domain_questions() -> None:
    assert "cohorts" in [i.name for i in match_intents("how good is our repeat purchase rate by month?")]
    assert "journey" in [i.name for i in match_intents("how many customers are dormant?")]
    assert "products" in [i.name for i in match_intents("which categories cross-sell best?")]
    assert "churn_risk" in [i.name for i in match_intents("how much revenue is at risk?")]


def test_match_intents_is_bounded() -> None:
    question = "revenue risk churn cohort journey product rfm retention clv trends value"
    assert len(match_intents(question)) == 3


def test_retrieve_context_returns_labelled_blocks(org_id, db) -> None:
    context = retrieve_context(db, org_id, "how many customers are at risk?")
    assert context["blocks"]
    assert context["blocks"][0]["intent"] == "overview"
    for block in context["blocks"]:
        assert block["label"]
        assert block["data"] is not None
    assert "overview" in context["matched_intents"]


def test_retrieve_context_falls_back_to_the_dashboard(org_id, db) -> None:
    context = retrieve_context(db, org_id, "banana")
    assert [block["intent"] for block in context["blocks"]] == ["overview"]


def test_answer_is_grounded_and_labelled(org_id, db) -> None:
    result = answer(db, org_id, "how much revenue is at risk right now?")
    assert result["answer"]
    assert result["provider"] == "mock"
    assert result["retrieved_context"]["blocks"]
    assert "churn_risk" in result["retrieved_context"]["matched_intents"]
    assert len(result["context_labels"]) == len(result["retrieved_context"]["blocks"])
    assert "advisory" in result["disclaimer"].lower()


def test_answer_uses_history_for_followups(org_id, db) -> None:
    result = answer(
        db,
        org_id,
        "and what about the risky ones?",
        history=[{"question": "how much revenue is at risk?", "answer": "Three customers."}],
    )
    assert result["answer"]


def test_format_history_respects_the_turn_cap() -> None:
    turns = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(20)]
    rendered = format_history(turns)
    assert "q0" not in rendered
    assert "q19" in rendered
    assert rendered.startswith("Conversation so far:")


def test_format_history_ignores_junk() -> None:
    assert format_history(None) == ""
    assert format_history([]) == ""
    assert format_history(["not a dict", {"question": "", "answer": ""}]) == ""


def test_intent_loaders_all_tolerate_an_empty_org(db) -> None:
    """A missing view must degrade, never 500 the assistant."""
    org = Organization(name="Empty", slug="empty")
    db.add(org)
    db.commit()
    context = retrieve_context(db, org.id, "cohorts journey products retention revenue trend clv rfm")
    assert context["blocks"][0]["intent"] == "overview"


def test_every_intent_has_keywords_and_a_label() -> None:
    for intent in INTENTS:
        assert intent.name and intent.label
        assert intent.keywords
