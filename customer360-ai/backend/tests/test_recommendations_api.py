"""Retention recommendations + what-if API endpoints: RBAC and flows."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.security_deps import get_db
from app.db.base import Base
from app.main import app
from app.models.user import User


def _churn_csv() -> bytes:
    lines = ["order_id,customer_id,order_date,product_name,category,quantity,amount,discount,channel"]
    order = 0
    for i in range(12):  # active: recent Aug orders
        order += 1
        lines.append(
            f"O{order:04d},A{i},2024-08-10,Widget,Tools,1,500.00,0.00,web"
        )
    for i in range(12):  # dormant: only Jan orders -> recency > 90d
        order += 1
        lines.append(
            f"O{order:04d},D{i},2024-01-15,Gadget,Tools,1,250.00,0.00,app"
        )
    return "\n".join(lines).encode()


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c, session_factory
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str = "rec@x.com") -> str:
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "organization_name": "Acme"},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def trained(ctx, token) -> dict:
    client, _ = ctx
    uploaded = client.post(
        "/api/v1/datasets/upload",
        headers=_headers(token),
        files={"file": ("transactions.csv", io.BytesIO(_churn_csv()), "text/csv")},
    ).json()
    ds_id = uploaded["dataset_id"]
    client.post(
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
    summary = client.post(f"/api/v1/datasets/{ds_id}/process", headers=_headers(token)).json()
    assert summary["status"] == "PROCESSED"
    assert summary["feature_summary"]["buyers"] >= 20

    resp = client.post(
        "/api/v1/models/churn/train",
        headers=_headers(token),
        json={"families": ["logistic"], "random_seed": 7},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture()
def token(ctx) -> str:
    client, _ = ctx
    return _register(client)


def test_generate_and_list_recommendations(ctx, token, trained) -> None:
    client, _ = ctx
    client.post("/api/v1/predictions/churn", headers=_headers(token), json={})

    generated = client.post(
        "/api/v1/recommendations/generate", headers=_headers(token), json={}
    ).json()
    assert generated["n_generated"] > 0
    assert generated["items"]
    item = generated["items"][0]
    assert item["type"] == "RETENTION"
    assert item["action"]
    assert item["priority"] in ("HIGH", "MEDIUM", "LOW")
    assert item["reason"]
    assert item["customer_id"]
    assert item["name"]

    listed = client.get("/api/v1/recommendations", headers=_headers(token)).json()
    assert listed["total"] == generated["n_generated"]

    high = client.get(
        "/api/v1/recommendations", headers=_headers(token), params={"min_priority": "HIGH"}
    ).json()
    assert all(i["priority"] == "HIGH" for i in high["items"])


def test_whatif_endpoint(ctx, token, trained) -> None:
    client, _ = ctx
    scored = client.post("/api/v1/predictions/churn", headers=_headers(token), json={}).json()
    customer_id = scored["items"][0]["customer_id"]

    resp = client.post(
        f"/api/v1/customers/{customer_id}/what-if",
        headers=_headers(token),
        json={"changes": {"recency_days": 15}},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["customer_id"] == customer_id
    assert out["baseline"]["probability"] is not None
    assert out["scenario_output"]["probability"] is not None
    assert out["delta"]["probability"] is not None
    assert out["changes"]["recency_days"] == 15
    assert out["note"]

    preset = client.post(
        f"/api/v1/customers/{customer_id}/what-if",
        headers=_headers(token),
        json={"scenario": "re_engage"},
    )
    assert preset.status_code == 200
    assert preset.json()["scenario"] == "re_engage"


def test_whatif_validation_and_rbac(ctx, token, trained) -> None:
    client, session_factory = ctx
    scored = client.post("/api/v1/predictions/churn", headers=_headers(token), json={}).json()
    customer_id = scored["items"][0]["customer_id"]

    bad = client.post(
        f"/api/v1/customers/{customer_id}/what-if",
        headers=_headers(token),
        json={"changes": {"nope": 5}},
    )
    assert bad.status_code == 422

    client.post("/api/v1/recommendations/generate", headers=_headers(token), json={})

    db = session_factory()
    user = db.execute(select(User).where(User.email == "rec@x.com")).scalar_one()
    user.role = "VIEWER"
    db.commit()
    db.close()

    gen = client.post("/api/v1/recommendations/generate", headers=_headers(token), json={})
    assert gen.status_code == 403

    whatif = client.post(
        f"/api/v1/customers/{customer_id}/what-if",
        headers=_headers(token),
        json={"changes": {"recency_days": 15}},
    )
    assert whatif.status_code == 403

    listed = client.get("/api/v1/recommendations", headers=_headers(token))
    assert listed.status_code == 200


def test_recommendations_generate_requires_model(ctx, token) -> None:
    client, _ = ctx
    generated = client.post(
        "/api/v1/recommendations/generate", headers=_headers(token), json={}
    )
    assert generated.status_code == 422
    assert "error" in generated.json()