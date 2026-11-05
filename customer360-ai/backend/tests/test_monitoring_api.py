"""Monitoring + reports API tests: RBAC, tenant isolation, and audit trail."""
from __future__ import annotations

import csv
import io
import json

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
from app.models.customer_features_model import CustomerFeature, Model, ModelVersion, Prediction
from app.models.user import User


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


def _register(client, email: str, org: str = "Acme") -> str:
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "organization_name": org},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _as_viewer(session_factory, email: str) -> None:
    db = session_factory()
    try:
        db.execute(select(User).where(User.email == email)).scalar_one().role = "VIEWER"
        db.commit()
    finally:
        db.close()


def _seed_model(session_factory, organization_id: int, *, shift: float = 0.0) -> int:
    """Give the org one active churn version, two dataset versions, and 60 buyers."""
    from datetime import datetime, timezone

    import numpy as np

    rng = np.random.default_rng(11)
    db = session_factory()
    try:
        db.add(
            Model(id=organization_id, organization_id=organization_id, name="Churn Prediction", model_type="churn")
        )
        db.add(
            ModelVersion(
                id=organization_id,
                organization_id=organization_id,
                model_id=organization_id,
                version_no=1,
                artifact_path="artifacts/v1/model.joblib",
                preprocessing_path="artifacts/v1",
                train_dataset_version_id=organization_id,
                trained_at=datetime.now(timezone.utc),
                active=True,
            )
        )
        from app.models.dataset import DatasetVersion

        db.add(
            DatasetVersion(
                id=100 + organization_id,
                organization_id=organization_id,
                dataset_id=organization_id,
                version_no=2,
                file_path="v2.csv",
            )
        )
        for index in range(1, 61):
            customer_id = 1000 * organization_id + index
            for version_id, is_current in ((organization_id, False), (100 + organization_id, True)):
                db.add(
                    CustomerFeature(
                        organization_id=organization_id,
                        customer_id=customer_id,
                        dataset_version_id=version_id,
                        recency_days=float(rng.normal(30 + (shift if is_current else 0.0), 10)),
                        frequency=float(rng.normal(5, 1)),
                        monetary=float(abs(rng.normal(500, 90))),
                        rfm_segment="LOYAL",
                        aov=100.0,
                        avg_days_between=14.0,
                        tenure_days=200.0,
                        category_diversity=2,
                        discount_usage=0.1,
                        engagement_score=55.0,
                        clv_est=300.0,
                    )
                )
            db.add(
                Prediction(
                    organization_id=organization_id,
                    model_version_id=organization_id,
                    customer_id=customer_id,
                    prediction_proba=round(float(rng.uniform(0, 1)), 6),
                    risk_level="HIGH" if index % 2 else "LOW",
                    created_at=datetime.now(timezone.utc),
                )
            )
        db.commit()
        return organization_id
    finally:
        db.close()


# --------------------------------------------------------------------- RBAC


def test_monitoring_endpoints_require_auth(ctx) -> None:
    client, _ = ctx
    assert client.get("/api/v1/monitoring/drift").status_code in (401, 403)
    assert client.get("/api/v1/monitoring/models").status_code in (401, 403)
    assert client.get("/api/v1/monitoring/runs").status_code in (401, 403)
    assert client.post("/api/v1/monitoring/runs").status_code in (401, 403)


def test_viewers_can_read_monitoring_but_not_record_runs(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "viewer-mon@x.com")
    _seed_model(session_factory, 1)
    _as_viewer(session_factory, "viewer-mon@x.com")

    assert client.get("/api/v1/monitoring/drift", headers=_headers(token)).status_code == 200
    assert client.get("/api/v1/monitoring/models", headers=_headers(token)).status_code == 200
    assert client.get("/api/v1/monitoring/runs", headers=_headers(token)).status_code == 200
    assert client.post("/api/v1/monitoring/runs", headers=_headers(token)).status_code == 403


def test_reports_export_is_restricted_to_analysts(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "viewer-rep@x.com")
    _as_viewer(session_factory, "viewer-rep@x.com")

    assert client.get("/api/v1/reports/catalog", headers=_headers(token)).status_code == 200
    denied = client.post(
        "/api/v1/reports/export",
        headers=_headers(token),
        json={"report": "customers", "format": "csv"},
    )
    assert denied.status_code == 403
    assert client.post("/api/v1/reports/export", json={"report": "customers"}).status_code in (401, 403)


# ------------------------------------------------------------------- drift


def test_drift_endpoint_returns_full_report(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "mon@x.com")
    _seed_model(session_factory, 1)

    resp = client.get("/api/v1/monitoring/drift", headers=_headers(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["model"]["model_type"] == "churn"
    assert body["baseline"]["source"] == "training_dataset_version"
    assert body["summary"]["n_features_profiled"] == 10
    assert len(body["features"]) == 10
    assert body["prediction"]["metric"] == "PREDICTION"
    assert body["thresholds"] == {"MODERATE": 0.1, "SIGNIFICANT": 0.25}
    assert body["health"]["n_population"] == 60


def test_drift_endpoint_reports_significant_shift(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "shift@x.com")
    _seed_model(session_factory, 1, shift=45.0)

    body = client.get("/api/v1/monitoring/drift", headers=_headers(token)).json()
    assert body["summary"]["drift_status"] == "SIGNIFICANT"
    assert "recency_days" in body["summary"]["drifted_features"]


def test_drift_endpoint_404s_without_a_model(ctx) -> None:
    client, _ = ctx
    token = _register(client, "nomodel@x.com")
    assert client.get("/api/v1/monitoring/drift", headers=_headers(token)).status_code == 404


def test_drift_endpoint_404s_for_unknown_version(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "unk@x.com")
    _seed_model(session_factory, 1)
    resp = client.get("/api/v1/monitoring/drift?model_version_id=999", headers=_headers(token))
    assert resp.status_code == 404


def test_drift_endpoint_is_tenant_scoped(ctx) -> None:
    client, session_factory = ctx
    first = _register(client, "t1@x.com", org="OrgOne")
    _seed_model(session_factory, 1)
    second = _register(client, "t2@x.com", org="OrgTwo")

    assert client.get("/api/v1/monitoring/drift", headers=_headers(first)).status_code == 200
    assert client.get("/api/v1/monitoring/drift", headers=_headers(second)).status_code == 404
    assert (
        client.get("/api/v1/monitoring/drift?model_version_id=1", headers=_headers(second)).status_code == 404
    )


# --------------------------------------------------------------------- runs


def test_record_run_then_list_and_fetch_it(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "runs@x.com")
    _seed_model(session_factory, 1)
    headers = _headers(token)

    created = client.post("/api/v1/monitoring/runs", headers=headers)
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["status"] == "COMPLETED"
    assert run["n_current_rows"] == 60
    assert run["drift_status"] in ("STABLE", "MODERATE", "SIGNIFICANT")

    listed = client.get("/api/v1/monitoring/runs", headers=headers).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == run["id"]

    fetched = client.get(f"/api/v1/monitoring/runs/{run['id']}", headers=headers).json()
    assert fetched["feature_metrics"]
    assert fetched["health"]["n_population"] == 60


def test_second_run_reuses_the_first_as_prediction_baseline(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "twice@x.com")
    _seed_model(session_factory, 1)
    headers = _headers(token)

    first = client.post("/api/v1/monitoring/runs", headers=headers).json()
    assert first["prediction_drift_status"] == "UNKNOWN"
    assert first["baseline_run_id"] is None

    second = client.post("/api/v1/monitoring/runs", headers=headers).json()
    assert second["baseline_run_id"] == first["id"]
    assert second["prediction_psi"] is not None


def test_run_recording_writes_an_audit_entry(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "audit-mon@x.com")
    _seed_model(session_factory, 1)
    client.post("/api/v1/monitoring/runs", headers=_headers(token))

    db = session_factory()
    try:
        entry = db.execute(
            select(AuditLog).where(AuditLog.action == "monitoring.scan")
        ).scalar_one()
        assert entry.entity_type == "monitoring_run"
        assert entry.metadata_json["drift_status"] is not None
    finally:
        db.close()


def test_run_history_is_tenant_scoped(ctx) -> None:
    client, session_factory = ctx
    first = _register(client, "rh1@x.com", org="OrgOne")
    _seed_model(session_factory, 1)
    second = _register(client, "rh2@x.com", org="OrgTwo")

    run = client.post("/api/v1/monitoring/runs", headers=_headers(first)).json()
    assert client.get("/api/v1/monitoring/runs", headers=_headers(second)).json()["total"] == 0
    assert client.get(f"/api/v1/monitoring/runs/{run['id']}", headers=_headers(second)).status_code == 404


def test_run_list_filters_by_version(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "filter@x.com")
    _seed_model(session_factory, 1)
    client.post("/api/v1/monitoring/runs", headers=_headers(token))

    headers = _headers(token)
    assert client.get("/api/v1/monitoring/runs?model_version_id=1", headers=headers).json()["total"] == 1
    assert client.get("/api/v1/monitoring/runs?model_version_id=2", headers=headers).json()["total"] == 0


def test_model_health_endpoint(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "health@x.com")
    _seed_model(session_factory, 1)
    headers = _headers(token)
    client.post("/api/v1/monitoring/runs", headers=headers)

    body = client.get("/api/v1/monitoring/models", headers=headers).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["model_type"] == "churn"
    assert item["n_predictions"] == 60
    assert item["last_run"]["status"] == "COMPLETED"


def test_run_pagination_bounds_are_validated(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "page@x.com")
    _seed_model(session_factory, 1)
    headers = _headers(token)
    assert client.get("/api/v1/monitoring/runs?limit=0", headers=headers).status_code == 422
    assert client.get("/api/v1/monitoring/runs?limit=999", headers=headers).status_code == 422
    assert client.get("/api/v1/monitoring/runs?offset=-1", headers=headers).status_code == 422


# ------------------------------------------------------------------ reports


def test_report_catalog_lists_reports_and_formats(ctx) -> None:
    client, _ = ctx
    token = _register(client, "cat@x.com")
    body = client.get("/api/v1/reports/catalog", headers=_headers(token)).json()

    keys = {item["key"] for item in body["items"]}
    assert "at_risk" in keys and "customers" in keys and "campaigns" in keys
    assert body["formats"] == ["csv", "xlsx", "json"]
    assert body["pdf_status"] == "not_available"


def test_export_csv_sets_attachment_headers(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "csv@x.com")
    _seed_model(session_factory, 1)
    resp = client.post(
        "/api/v1/reports/export",
        headers=_headers(token),
        json={"report": "predictions", "format": "csv"},
    )
    assert resp.status_code == 200, resp.text
    assert "attachment" in resp.headers["content-disposition"]
    assert ".csv" in resp.headers["content-disposition"]
    assert resp.headers["x-report-rows"] == "60"
    assert resp.headers["x-report-truncated"] == "false"
    assert resp.headers["x-report-max-rows"] == "50000"

    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert len(rows) == 60
    assert rows[0]["risk_level"] in ("HIGH", "LOW")


def test_export_json_includes_metadata(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "json@x.com")
    _seed_model(session_factory, 1)
    resp = client.post(
        "/api/v1/reports/export",
        headers=_headers(token),
        json={"report": "features", "format": "json"},
    )
    payload = json.loads(resp.content.decode("utf-8"))
    assert payload["report"] == "features"
    assert payload["row_count"] == 60
    assert payload["columns"][0] == "customer_id"


def test_export_xlsx_is_downloadable(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "xlsx@x.com")
    _seed_model(session_factory, 1)
    resp = client.post(
        "/api/v1/reports/export",
        headers=_headers(token),
        json={"report": "customers", "format": "xlsx"},
    )
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"
    assert resp.headers["content-type"].endswith("spreadsheetml.sheet")


def test_export_rejects_unknown_report_and_format(ctx) -> None:
    client, _ = ctx
    token = _register(client, "bad@x.com")
    headers = _headers(token)
    assert (
        client.post("/api/v1/reports/export", headers=headers, json={"report": "nope", "format": "csv"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/reports/export", headers=headers, json={"report": "customers", "format": "pdf"}
        ).status_code
        == 422
    )


def test_export_rejects_unknown_request_keys(ctx) -> None:
    client, _ = ctx
    token = _register(client, "strict@x.com")
    resp = client.post(
        "/api/v1/reports/export",
        headers=_headers(token),
        json={"report": "customers", "format": "csv", "surprise": True},
    )
    assert resp.status_code == 422


def test_export_rejects_unknown_filters(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "filt@x.com")
    _seed_model(session_factory, 1)
    resp = client.post(
        "/api/v1/reports/export",
        headers=_headers(token),
        json={"report": "customers", "format": "csv", "filters": {"all_columns": True}},
    )
    assert resp.status_code == 422


def test_export_is_tenant_scoped(ctx) -> None:
    client, session_factory = ctx
    first = _register(client, "e1@x.com", org="OrgOne")
    _seed_model(session_factory, 1)
    second = _register(client, "e2@x.com", org="OrgTwo")

    mine = client.post(
        "/api/v1/reports/export",
        headers=_headers(first),
        json={"report": "predictions", "format": "json"},
    )
    theirs = client.post(
        "/api/v1/reports/export",
        headers=_headers(second),
        json={"report": "predictions", "format": "json"},
    )
    assert json.loads(mine.content.decode("utf-8"))["row_count"] == 60
    # The second org owns no model, so it has nothing to leak.
    assert json.loads(theirs.content.decode("utf-8"))["row_count"] == 0


def test_export_writes_an_audit_entry(ctx) -> None:
    client, session_factory = ctx
    token = _register(client, "audit-rep@x.com")
    _seed_model(session_factory, 1)
    client.post(
        "/api/v1/reports/export",
        headers=_headers(token),
        json={"report": "customers", "format": "csv", "filters": {"search": "ext-1"}},
    )

    db = session_factory()
    try:
        entry = db.execute(select(AuditLog).where(AuditLog.action == "report.export")).scalar_one()
        assert entry.entity_type == "report"
        assert entry.entity_id == "customers"
        assert entry.metadata_json["format"] == "csv"
        assert entry.metadata_json["filters"] == {"search": "ext-1"}
    finally:
        db.close()
