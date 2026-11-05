"""Segmentation API tests: train, list ML segments, cluster analytics, rename."""
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
O0001,C0_0,2024-01-10,Widget,Tools,1,700.00,0.00,web
O0002,C0_0,2024-03-10,Widget,Tools,1,714.00,0.00,web
O0003,C0_0,2024-05-10,Widget,Tools,1,728.00,0.00,web
O0004,C0_0,2024-07-10,Widget,Tools,1,742.00,0.00,web
O0005,C0_1,2024-02-10,Widget,Tools,1,707.00,0.00,web
O0006,C0_1,2024-04-10,Widget,Tools,1,721.00,0.00,web
O0007,C0_1,2024-09-10,Widget,Tools,1,749.00,0.00,web
O0008,C1_0,2024-01-11,Gadget,Tools,1,450.00,5.00,app
O0009,C1_0,2024-04-11,Gadget,Tools,1,450.00,5.00,app
O0010,C1_0,2024-07-11,Gadget,Tools,1,450.00,5.00,app
O0011,C1_1,2024-02-11,Gadget,Tools,1,457.00,0.00,web
O0012,C1_1,2024-06-11,Gadget,Tools,1,457.00,0.00,web
O0013,C2_0,2024-01-12,Gadget,Tools,1,200.00,5.00,app
O0014,C2_0,2024-06-12,Gadget,Tools,1,200.00,5.00,app
O0015,C2_1,2024-03-12,Gadget,Tools,1,207.00,0.00,web
O0016,C2_1,2024-03-25,Gadget,Tools,1,207.00,0.00,web
O0017,C2_2,2024-04-12,Gadget,Tools,1,214.00,0.00,web
O0018,C2_2,2024-06-12,Gadget,Tools,1,214.00,0.00,web
O0019,C3_0,2024-05-14,Widget,Tools,1,320.00,0.00,web
O0020,C3_0,2024-07-14,Widget,Tools,1,327.00,5.00,web
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
        json={"email": "seg@x.com", "password": "password123", "organization_name": "Acme"},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _process_txns(client: TestClient, token: str) -> int:
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
    assert summary["feature_summary"]["buyers"] >= 8
    return ds_id


def test_train_list_clusters_flow(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txns(client, token)

    resp = client.post(
        "/api/v1/segments/train",
        headers=_headers(token),
        json={"n_clusters": 3},
    )
    assert resp.status_code == 200
    trained = resp.json()
    assert trained["status"] == "TRAINED"
    assert trained["n_clusters"] == 3

    listing = client.get("/api/v1/segments", params={"segment_type": "ML"}, headers=_headers(token)).json()
    assert listing["total"] == 3
    assert [s["id"] for s in listing["items"]]
    assert sum(s["member_count"] for s in listing["items"]) >= 8
    segment_id = listing["items"][0]["id"]

    clusters = client.get(f"/api/v1/segments/{segment_id}/clusters", headers=_headers(token)).json()
    assert clusters["member_count"] > 0
    assert clusters["stats"]
    assert clusters["channels"]
    assert clusters["top_members"]

    models = client.get("/api/v1/models", headers=_headers(token)).json()
    assert models["total"] >= 1
    seg_model = next(m for m in models["items"] if m["model_type"] == "segmentation")
    assert seg_model["versions"]
    assert seg_model["versions"][0]["metrics"]["silhouette_score"] is not None


def test_rename_segment(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    _process_txns(client, token)
    client.post("/api/v1/segments/train", headers=_headers(token), json={"n_clusters": 3})

    listing = client.get("/api/v1/segments", headers=_headers(token)).json()
    segment = listing["items"][0]

    resp = client.patch(
        f"/api/v1/segments/{segment['id']}",
        headers=_headers(token),
        json={"name": "VIP Power Buyers", "description": "Top spenders", "color": "#111827"},
    )
    assert resp.status_code == 200
    renamed = resp.json()
    assert renamed["name"] == "VIP Power Buyers"
    assert renamed["description"] == "Top spenders"

    refreshed = client.get("/api/v1/segments", headers=_headers(token)).json()["items"]
    match = next(s for s in refreshed if s["id"] == segment["id"])
    assert match["name"] == "VIP Power Buyers"


def test_viewer_gets_forbidden(ctx) -> None:
    from app.core.exceptions import ForbiddenError
    from app.core.security_deps import require_roles

    dependency = require_roles("ADMIN", "ANALYST")

    class _Viewer:
        role = "VIEWER"

    try:
        dependency(_Viewer())
        raised = False
    except ForbiddenError:
        raised = True
    assert raised