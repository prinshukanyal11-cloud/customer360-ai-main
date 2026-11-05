"""Analytics API tests: feature summary, RFM mix, CLV buckets, revenue trends,
cohorts, journey, product recommendations."""
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
O5,C3,2024-06-20,Widget,Tools,1,60.00,5.00,web
O6,C3,2024-08-05,Gadget,Tools,3,300.00,0.00,web
"""


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


def _register(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "analyst@x.com", "password": "password123", "organization_name": "Acme"},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _process_txn_file(client: TestClient, token: str) -> int:
    uploaded = client.post(
        "/api/v1/datasets/upload",
        headers=_headers(token),
        files={"file": ("transactions.csv", io.BytesIO(TXNS_CSV), "text/csv")},
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
    assert summary["feature_summary"]["buyers"] == 3
    return ds_id


def test_features_summary_after_process(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.get("/api/v1/analytics/features-summary", headers=_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["buyers"] == 3
    assert body["total_customers"] == 3
    assert body["coverage"] == 1.0


def test_rfm_distribution(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.get("/api/v1/analytics/rfm", headers=_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_customers"] == 3
    assert sum(s["customer_count"] for s in body["segments"]) == 3
    for segment in body["segments"]:
        assert 0 <= segment["share"] <= 1


def test_clv_distribution(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.get("/api/v1/analytics/clv", headers=_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["customers"] == 3
    assert body["avg_clv"] > 0
    assert body["median_clv"] > 0
    assert len(body["buckets"]) == 10
    assert sum(b["count"] for b in body["buckets"]) == 3


def test_revenue_trends(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.get("/api/v1/analytics/trends", headers=_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"]
    assert all(i["orders"] > 0 for i in body["items"])
    total_revenue = sum(i["revenue"] for i in body["items"])
    assert round(total_revenue, 2) == 990.00  # 100+250+80+200+60+300


def test_features_build_endpoint(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.post("/api/v1/features/build", headers=_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "OK"
    assert body["buyers"] == 3

    summary = client.get("/api/v1/features/summary", headers=_headers(token)).json()
    assert summary["buyers"] == 3


def test_cohorts_endpoint(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.get("/api/v1/analytics/cohorts", headers=_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_customers"] == 3
    cohorts = body["cohorts"]
    assert len(cohorts) == 3  # first purchase months 2024-03, 2024-05, 2024-06
    assert all(c["customers"] == 1 for c in cohorts)
    assert all(c["retention"][0] == 1.0 for c in cohorts)
    # C1 (2 orders) and C3 (3 orders) repeat; C2 has a single order
    assert body["summary"]["repeat_share"] == round(2 / 3, 4)


def test_journey_endpoint(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txn_file(client, token)

    resp = client.get("/api/v1/analytics/journey", headers=_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert sum(s["customers"] for s in body["stages"]) == 3
    assert body["milestones"][0]["customers"] == 3  # 1+ purchases
    assert body["milestones"][1]["customers"] == 2  # 2+ purchases: C1 + C3
    assert body["time_to_repeat"]
    assert body["as_of"] == "2024-08-05"


def test_products_endpoint(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txn_file(client, token)

    global_resp = client.get("/api/v1/analytics/products", headers=_headers(token))
    assert global_resp.status_code == 200
    global_body = global_resp.json()
    assert global_body["basis"]["type"] == "global"
    assert global_body["total_baskets"] == 3
    assert global_body["recommendations"]
    top = global_body["recommendations"][0]
    assert top["product_a"]["name"] and top["product_b"]["name"]
    assert top["co_purchases"] >= 1

    # all three customers bought both Widget and Gadget -> item-to-item rec
    products = client.get("/api/v1/customers", headers=_headers(token)).json()
    assert products["total"] == 3
    # find the internal Product id from the transactions; Widget is product "Widget"
    resp = client.get("/api/v1/analytics/products", headers=_headers(token), params={"product_id": 1, "limit": 3})
    assert resp.status_code == 200
    recs = resp.json()
    assert recs["basis"]["type"] == "product"
    assert recs["recommendations"]

    bad = client.get("/api/v1/analytics/products", headers=_headers(token), params={"product_id": 9999})
    assert bad.status_code == 404