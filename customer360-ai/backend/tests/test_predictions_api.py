"""Churn predictions API tests: train via API, score, at-risk list, RBAC."""
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


def _register(client: TestClient, email: str = "churn@x.com") -> str:
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
        json={"families": ["logistic", "random_forest"], "random_seed": 7},
    )
    assert resp.status_code == 200
    trained = resp.json()
    assert trained["status"] == "TRAINED"
    assert len(trained["families"]) == 2
    return trained


@pytest.fixture()
def token(ctx) -> str:
    client, _ = ctx
    return _register(client)


def test_train_score_at_risk_flow(ctx, token, trained) -> None:
    client, _ = ctx
    scored = client.post("/api/v1/predictions/churn", headers=_headers(token), json={}).json()
    assert scored["n_scored"] == 24
    assert all(item["risk_level"] for item in scored["items"])

    at_risk = client.get("/api/v1/predictions/at-risk", headers=_headers(token)).json()
    assert at_risk["total"] == 24
    assert len(at_risk["items"]) > 0
    assert all(item["risk_level"] in ("HIGH", "CRITICAL") for item in at_risk["items"])
    assert all(item["name"] for item in at_risk["items"])
    assert "summary" in at_risk and "total_value_at_risk" in at_risk["summary"]
    for item in at_risk["items"]:
        assert "risk_category" in item
        assert item["risk_category"] in (
            "PRIORITY",
            "WIN_BACK",
        )  # at-risk quadrant is high risk, low or high value
        assert item["value_at_risk"] is None or item["value_at_risk"] >= 0.0


def test_explain_flow(ctx, token, trained) -> None:
    client, _ = ctx
    client.post("/api/v1/predictions/churn", headers=_headers(token), json={})
    explained = client.post(
        "/api/v1/predictions/churn/explain", headers=_headers(token), json={}
    ).json()
    assert explained["n_explained"] == 24
    assert len(explained["items"]) == 24
    total_share = sum(g["share"] for g in explained["global_importance"])
    if total_share:
        assert total_share == pytest.approx(100.0, abs=1.0)
    first = explained["items"][0]
    detail = client.get(
        f"/api/v1/customers/{first['customer_id']}/explanation",
        headers=_headers(token),
    )
    assert detail.status_code == 200
    assert detail.json()["prediction_id"] == first["prediction_id"]
    assert detail.json()["contributions"]


def test_score_specific_customers(ctx, token, trained) -> None:
    client, _ = ctx
    scored = client.post(
        "/api/v1/predictions/churn",
        headers=_headers(token),
        json={"customer_ids": [1, 2]},
    ).json()
    assert scored["n_scored"] == 2


def test_models_list_activates_viewer_forbidden(ctx, token, trained) -> None:
    client, _session_factory = ctx
    listing = client.get("/api/v1/models", headers=_headers(token)).json()
    assert listing["total"] == 1
    model = listing["items"][0]
    assert model["model_type"] == "churn"
    inactive = [v for v in model["versions"] if not v["active"]]
    active = [v for v in model["versions"] if v["active"]]
    assert len(active) == 1
    assert len(inactive) == 1

    activated = client.post(
        f"/api/v1/models/{model['id']}/versions/{inactive[0]['id']}/activate",
        headers=_headers(token),
    )
    assert activated.status_code == 200
    assert activated.json()["active"] is True

    _session_factory: sessionmaker = ctx[1]
    db = _session_factory()
    user = db.execute(select(User).where(User.email == "churn@x.com")).scalar_one()
    user.role = "VIEWER"
    db.commit()
    db.close()

    resp = client.post(
        "/api/v1/models/churn/train",
        headers=_headers(token),
        json={"families": ["logistic"]},
    )
    assert resp.status_code == 403

    listed = client.get("/api/v1/predictions/at-risk", headers=_headers(token))
    assert listed.status_code == 200