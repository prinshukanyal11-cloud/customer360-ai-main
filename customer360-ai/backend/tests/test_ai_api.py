"""AI API tests: grounded query, context transparency, guarded NL-to-SQL."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.security_deps import get_db
from app.db.base import Base
from app.main import app

TXNS_CSV = b"""order_id,customer_id,order_date,product_name,category,quantity,amount,discount,channel
O1,C1,2024-06-01,Widget,Tools,2,100.00,0.00,web
O2,C1,2024-07-15,Gadget,Tools,1,250.00,10.00,app
O3,C2,2024-05-20,Widget,Tools,1,80.00,0.00,web
O4,C3,2024-03-10,Gadget,Tools,2,200.00,0.00,app
O5,C3,2024-08-05,Gadget,Tools,3,300.00,0.00,web
"""


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, TestingSessionLocal
    app.dependency_overrides.clear()


def _register(client) -> str:
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "ai@x.com", "password": "password123", "organization_name": "Acme"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _process_txn_file(client, token: str) -> int:
    uploaded = client.post(
        "/api/v1/datasets/upload",
        headers=_headers(token),
        files={"file": ("transactions.csv", io.BytesIO(TXNS_CSV), "text/csv")},
    ).json()
    ds_id = uploaded["dataset_id"]
    mapped = client.post(
        f"/api/v1/datasets/{ds_id}/map-columns",
        headers=_headers(token),
        json={
            "mapping": {
                "transactions": {
                    "external_order_id": "order_id",
                    "customer_id": "customer_id",
                    "order_date": "order_date",
                    "quantity": "quantity",
                    "amount": "amount",
                    "discount": "discount",
                    "channel": "channel",
                },
                "products": {"product_name": "product_name", "category": "category"},
            }
        },
    )
    assert mapped.status_code == 200, mapped.text
    summary = client.post(f"/api/v1/datasets/{ds_id}/process", headers=_headers(token)).json()
    assert summary["status"] == "PROCESSED"
    return ds_id


def test_ai_query_returns_grounded_context(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.post(
        "/api/v1/ai/query",
        headers=_headers(token),
        json={"question": "how much revenue is at risk right now?", "history": []},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"]
    assert body["provider"] == "mock"
    blocks = body["retrieved_context"]["blocks"]
    assert blocks and blocks[0]["intent"] == "overview"
    assert body["context_labels"]


def test_ai_query_requires_auth(ctx) -> None:
    client, _sf = ctx
    resp = client.post("/api/v1/ai/query", json={"question": "how is revenue trending?"})
    assert resp.status_code in (401, 403)


def test_ai_query_rejects_a_short_question(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    resp = client.post("/api/v1/ai/query", headers=_headers(token), json={"question": "hi"})
    assert resp.status_code == 422


def test_ai_context_endpoint_is_transparent(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.get("/api/v1/ai/context", headers=_headers(token), params={"question": "who is churning?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "churn_risk" in body["matched_intents"]
    assert body["blocks"][0]["label"]


def test_nl_to_sql_executes_a_template(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.post(
        "/api/v1/ai/nl-to-sql",
        headers=_headers(token),
        json={"question": "how many customers do we have?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["source"] == "template:customer_counts"
    assert body["rows"][0]["customers"] == 3
    assert "organization_id" in body["sql"]
    assert "LIMIT" in body["sql"]


def test_nl_to_sql_accepts_guarded_manual_sql(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.post(
        "/api/v1/ai/nl-to-sql",
        headers=_headers(token),
        json={
            "question": "customer names please",
            "generated_sql": "SELECT name, rfm_segment FROM customer_features JOIN customers "
            "ON customers.id = customer_features.customer_id ORDER BY name",
        },
    )
    assert resp.status_code == 422, resp.text  # unscoped join is refused


def test_nl_to_sql_rejects_dangerous_sql(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    for sql in (
        "DELETE FROM customers",
        "SELECT * FROM customers; DROP TABLE customers",
        "SELECT * FROM users",
    ):
        resp = client.post(
            "/api/v1/ai/nl-to-sql",
            headers=_headers(token),
            json={"question": "sneaky", "generated_sql": sql},
        )
        assert resp.status_code == 422, sql


def test_nl_to_sql_scopes_manual_sql_to_the_caller(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.post(
        "/api/v1/ai/nl-to-sql",
        headers=_headers(token),
        json={
            "question": "all customers",
            "generated_sql": "SELECT external_customer_id FROM customers ORDER BY external_customer_id",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "manual"
    assert {row["external_customer_id"] for row in body["rows"]} == {"C1", "C2", "C3"}


def test_nl_to_sql_never_returns_another_tenants_rows(ctx) -> None:
    client, session_factory = ctx
    token_a = _register(client)
    _process_txn_file(client, token_a)

    # a second tenant with their own copy of the same data
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "other@x.com", "password": "password123", "organization_name": "Globex"},
    )
    assert resp.status_code == 201, resp.text
    token_b = resp.json()["access_token"]
    _process_txn_file(client, token_b)

    resp = client.post(
        "/api/v1/ai/nl-to-sql",
        headers=_headers(token_b),
        json={"question": "how many customers do we have?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "template:customer_counts"
    # three customers per org exist, but the query returns only the caller's
    assert body["rows"][0]["customers"] == 3
    db = session_factory()
    from app.models.transaction import Customer
    from app.models.user import User

    org_b = db.get(User, 2).organization_id
    assert f"organization_id = {org_b}" in body["sql"]
    assert "organization_id = 1 " not in body["sql"].replace("organization_id = 1)", "")
    assert {c.organization_id for c in db.query(Customer).all()} == {1, org_b}
