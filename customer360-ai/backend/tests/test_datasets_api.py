"""Datasets API integration tests (in-memory SQLite via dependency override)."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.security_deps import get_db
from app.db.base import Base
from app.main import app
from app.models.transaction import Customer, Transaction

USERS_CSV = b"""customer_id,name,email,city,age,signup_date,acquisition_channel
C1,Ann,ann@x.com,Delhi,34,2024-01-05,web
C2,Ben,ben@x.com,Mumbai,29,2024-02-10,app
"""

TXNS_CSV = b"""order_id,customer_id,order_date,product_name,quantity,amount,discount,channel
O1,C1,2024-06-01,Widget,2,100.00,0.00,web
O2,C2,2024-06-02,Gadget,1,250.00,10.00,app
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
        json={"email": "admin@x.com", "password": "password123", "organization_name": "Acme"},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload(client: TestClient, token: str, content: bytes, name: str) -> dict:
    resp = client.post(
        "/api/v1/datasets/upload",
        headers=_headers(token),
        files={"file": (name, io.BytesIO(content), "text/csv")},
    )
    assert resp.status_code == 201
    return resp.json()


def test_upload_returns_suggestion_and_preview(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    uploaded = _upload(client, token, USERS_CSV, "customers.csv")

    assert uploaded["dataset_id"] > 0
    assert uploaded["total_rows"] == 2
    suggestion = uploaded["mapping_suggestion"]
    assert suggestion["customers"]["external_customer_id"] == "customer_id"
    assert len(uploaded["columns"]) == 7
    assert uploaded["preview"][0]["customer_id"] == "C1"


def test_upload_to_existing_name_creates_new_version(ctx) -> None:
    client, session_factory = ctx
    token = _register(client)
    first = _upload(client, token, USERS_CSV, "customers.csv")
    second = _upload(client, token, TXNS_CSV, "customers.csv")  # same stem -> new version

    assert second["dataset_id"] == first["dataset_id"]

    db = session_factory()
    try:
        from app.models.dataset import DatasetVersion

        versions = db.execute(
            select(DatasetVersion).where(DatasetVersion.dataset_id == first["dataset_id"])
        ).scalars().all()
        assert [v.version_no for v in versions] == [1, 2]
    finally:
        db.close()


def test_full_mapping_validate_process_roundtrip(ctx) -> None:
    client, session_factory = ctx
    token = _register(client)
    uploaded = _upload(client, token, USERS_CSV, "customers.csv")
    ds_id = uploaded["dataset_id"]

    # List
    listing = client.get("/api/v1/datasets", headers=_headers(token)).json()
    assert listing["total"] == 1

    # Map columns
    mapping = {
        "customers": {
            "external_customer_id": "customer_id",
            "name": "name",
            "email": "email",
            "location": "city",
            "age": "age",
            "signup_date": "signup_date",
            "acquisition_channel": "acquisition_channel",
        }
    }
    resp = client.post(
        f"/api/v1/datasets/{ds_id}/map-columns",
        headers=_headers(token),
        json={"mapping": mapping},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "MAPPED"
    # Only the mapped entity is required; products/transactions are not in this file.
    assert body["required_missing"].get("customers") is None

    # Validate -> DQ
    resp = client.post(f"/api/v1/datasets/{ds_id}/validate", headers=_headers(token))
    assert resp.status_code == 200
    validation = resp.json()
    assert validation["blocked"] is False
    assert validation["score"] == 100.0

    # Process -> persist
    resp = client.post(f"/api/v1/datasets/{ds_id}/process", headers=_headers(token))
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["status"] == "PROCESSED"
    assert summary["inserted"]["customers"] == 2

    db = session_factory()
    try:
        count = db.execute(
            select(func.count()).select_from(Customer).where(Customer.organization_id == 1)
        ).scalar_one()
        assert count == 2
    finally:
        db.close()

    # Quality
    quality = client.get(f"/api/v1/datasets/{ds_id}/quality", headers=_headers(token)).json()
    assert quality["score"] == 100.0
    assert quality["status"] == "PROCESSED"


def test_process_transactions_with_products(ctx) -> None:
    client, session_factory = ctx
    token = _register(client)
    uploaded = _upload(client, token, TXNS_CSV, "transactions.csv")
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
                "products": {"product_name": "product_name"},
            }
        },
    )

    summary = client.post(f"/api/v1/datasets/{ds_id}/process", headers=_headers(token)).json()
    assert summary["inserted"]["transactions"] == 2
    assert summary["inserted"]["products"] == 2

    db = session_factory()
    try:
        txn_count = db.execute(
            select(func.count()).select_from(Transaction).where(Transaction.organization_id == 1)
        ).scalar_one()
        assert txn_count == 2
        # transactions resolved to inferred products
        linked = db.execute(
            select(Transaction).where(Transaction.product_id.isnot(None))
        ).scalars().all()
        assert len(linked) == 2
    finally:
        db.close()


def test_map_columns_rejects_unknown_field(ctx) -> None:
    client, _session_factory = ctx
    token = _register(client)
    uploaded = _upload(client, token, USERS_CSV, "customers.csv")

    resp = client.post(
        f"/api/v1/datasets/{uploaded['dataset_id']}/map-columns",
        headers=_headers(token),
        json={"mapping": {"customers": {"not_real": "customer_id"}}},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_viewer_cannot_access_mutable_endpoints(ctx) -> None:
    client, _session_factory = ctx
    _register(client)

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