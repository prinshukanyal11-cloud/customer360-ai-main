"""Campaign API tests: lifecycle, RBAC, tenant isolation, and audit trail."""
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
from app.models.campaign import AuditLog

TXNS_CSV = b"""order_id,customer_id,order_date,product_name,category,quantity,amount,discount,channel
O1,C1,2024-06-01,Widget,Tools,2,100.00,0.00,web
O2,C1,2024-07-15,Gadget,Tools,1,250.00,10.00,app
O3,C2,2024-05-20,Widget,Tools,1,80.00,0.00,web
O4,C3,2024-03-10,Gadget,Tools,2,200.00,0.00,app
O5,C3,2024-08-05,Gadget,Tools,3,300.00,0.00,web
"""


@pytest.fixture()
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


def _register(client, email: str = "campaign@x.com", org: str = "Acme") -> str:
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "organization_name": org},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _process(client, token: str) -> None:
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


def _create(client, token: str, **overrides) -> dict:
    payload = {
        "name": "Lapsed win-back",
        "objective": "Bring back dormant buyers",
        "offer": "20% off",
        "channel": "email",
        "audience_filter": {"max_recency_days": 400},
        "assumptions": {"response_rate": 0.2, "cost_per_contact": 0.5},
    }
    payload.update(overrides)
    resp = client.post("/api/v1/campaigns", headers=_headers(token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_campaign_lifecycle_end_to_end(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process(client, token)

    created = _create(client, token)
    assert created["status"] == "DRAFT"
    campaign_id = created["id"]

    listed = client.get("/api/v1/campaigns", headers=_headers(token))
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    simulated = client.post(f"/api/v1/campaigns/{campaign_id}/simulate", headers=_headers(token), json={})
    assert simulated.status_code == 200, simulated.text
    forecast = simulated.json()["forecast"]
    assert forecast["contacts"] == 3
    assert forecast["response_range"][0] <= forecast["expected_responses"] <= forecast["response_range"][1]

    started = client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=_headers(token))
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "ACTIVE"
    assert started.json()["member_count"] == 3

    members = client.get(f"/api/v1/campaigns/{campaign_id}/members", headers=_headers(token))
    assert members.status_code == 200
    assert members.json()["total"] == 3
    assert {item["variant"] for item in members.json()["items"]} <= {"A", "B"}

    results = client.post(
        f"/api/v1/campaigns/{campaign_id}/results",
        headers=_headers(token),
        json={
            "response_rate": 0.2,
            "revenue": 900.0,
            "cost": 300.0,
            "variant_metrics": {
                "A": {"delivered": 200, "responses": 30, "revenue": 700.0},
                "B": {"delivered": 200, "responses": 52, "revenue": 1200.0},
            },
            "complete": True,
        },
    )
    assert results.status_code == 200, results.text
    body = results.json()
    assert body["roi"] == 2.0
    assert body["status"] == "COMPLETED"
    assert body["ab_analysis"]["test"]["significant"] is True

    analysis = client.get(f"/api/v1/campaigns/{campaign_id}/ab-analysis", headers=_headers(token))
    assert analysis.status_code == 200
    assert analysis.json()["metric_compared"] == "responses"
    assert analysis.json()["test"]["lift_relative"] > 0


def test_simulate_accepts_assumption_overrides(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process(client, token)
    campaign_id = _create(client, token)["id"]

    resp = client.post(
        f"/api/v1/campaigns/{campaign_id}/simulate",
        headers=_headers(token),
        json={"assumptions": {"cost_per_contact": 25.0}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["forecast"]["estimated_cost"] == 75.0
    assert resp.json()["assumptions"]["cost_per_contact"] == 25.0


def test_create_rejects_an_unknown_audience_key(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    resp = client.post(
        "/api/v1/campaigns",
        headers=_headers(token),
        json={"name": "typo", "audience_filter": {"min_ltv": 100}},
    )
    assert resp.status_code == 422, resp.text
    assert "min_ltv" in resp.text  # the caller is told which key was wrong


def test_create_rejects_nonsensical_assumptions(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    resp = client.post(
        "/api/v1/campaigns",
        headers=_headers(token),
        json={"name": "no test", "assumptions": {"response_rate": 5}},
    )
    assert resp.status_code == 422, resp.text


def test_create_requires_a_name(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    assert client.post("/api/v1/campaigns", headers=_headers(token), json={"name": "  "}).status_code == 422


def test_update_is_rejected_once_launched(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process(client, token)
    campaign_id = _create(client, token)["id"]

    patched = client.patch(
        f"/api/v1/campaigns/{campaign_id}", headers=_headers(token), json={"name": "Renamed"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed"

    assert client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=_headers(token)).status_code == 200
    late = client.patch(f"/api/v1/campaigns/{campaign_id}", headers=_headers(token), json={"name": "Nope"})
    assert late.status_code == 422, late.text
    assert "draft" in late.json()["error"]["message"].lower()


def test_results_before_launch_are_refused(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process(client, token)
    campaign_id = _create(client, token)["id"]
    resp = client.post(
        f"/api/v1/campaigns/{campaign_id}/results", headers=_headers(token), json={"revenue": 10.0}
    )
    assert resp.status_code == 422, resp.text


def test_unknown_campaign_returns_404(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    assert client.get("/api/v1/campaigns/9999", headers=_headers(token)).status_code == 404
    assert client.post("/api/v1/campaigns/9999/start", headers=_headers(token)).status_code == 404


def test_campaigns_require_authentication(ctx) -> None:
    client, _sf = ctx
    assert client.get("/api/v1/campaigns").status_code in (401, 403)
    assert client.post("/api/v1/campaigns", json={"name": "x"}).status_code in (401, 403)


def test_viewers_can_read_but_not_write(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "viewer-campaign@x.com")
    _process(client, token)

    from app.models.user import User

    db = session_factory()
    db.execute(select(User).where(User.email == "viewer-campaign@x.com")).scalar_one().role = "VIEWER"
    db.commit()
    db.close()

    assert client.post("/api/v1/campaigns", headers=_headers(token), json={"name": "nope"}).status_code == 403
    assert client.get("/api/v1/campaigns", headers=_headers(token)).status_code == 200
    assert client.post("/api/v1/campaigns/1/start", headers=_headers(token)).status_code == 403


def test_campaigns_are_isolated_between_tenants(ctx) -> None:
    client, _sf = ctx
    token_a = _register(client, "a@x.com", "Acme")
    _process(client, token_a)
    campaign_id = _create(client, token_a)["id"]

    token_b = _register(client, "b@x.com", "Globex")
    _process(client, token_b)

    assert client.get(f"/api/v1/campaigns/{campaign_id}", headers=_headers(token_b)).status_code == 404
    assert client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=_headers(token_b)).status_code == 404
    assert client.get("/api/v1/campaigns", headers=_headers(token_b)).json()["total"] == 0


def test_audience_filter_survives_the_round_trip(ctx) -> None:
    client, _sf = ctx
    token = _register(client)
    _process(client, token)
    campaign_id = _create(client, token, audience_filter={"risk_levels": ["high"], "variant_split": 0.6})["id"]

    detail = client.get(f"/api/v1/campaigns/{campaign_id}", headers=_headers(token)).json()
    assert detail["audience_filter"]["risk_levels"] == ["HIGH"]
    assert detail["audience_filter"]["variant_split"] == 0.6


def test_every_campaign_action_is_audited(ctx) -> None:
    client, session_factory = ctx
    token = _register(client)
    _process(client, token)
    campaign_id = _create(client, token)["id"]
    client.post(f"/api/v1/campaigns/{campaign_id}/simulate", headers=_headers(token), json={})
    client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=_headers(token))
    client.post(f"/api/v1/campaigns/{campaign_id}/results", headers=_headers(token), json={"revenue": 50.0})

    db = session_factory()
    actions = {
        entry.action
        for entry in db.execute(
            select(AuditLog).where(AuditLog.entity_id == campaign_id, AuditLog.entity_type == "campaign")
        ).scalars().all()
    }
    db.close()
    assert {"campaign.create", "campaign.simulate", "campaign.start", "campaign.results"} <= actions
