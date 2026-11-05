"""Monitoring service tests: drift baselines, verdicts, persistence, and isolation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.constants import DriftStatus
from app.core.exceptions import NotFoundError
from app.db.base import Base
from app.ml.churn import CHURN_FEATURES
from app.models.customer_features_model import CustomerFeature, Model, ModelVersion, Prediction
from app.models.dataset import Dataset, DatasetVersion
from app.models.monitoring import MonitoringRun
from app.models.transaction import Customer
from app.models.user import Organization
from app.services import monitoring_service as ms


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _org(db, org_id: int, name: str) -> None:
    db.add(Organization(id=org_id, name=name, slug=name.lower()))


def _seed(
    db,
    org_id: int = 1,
    *,
    customers: int = 120,
    feature_shift: dict[str, float] | None = None,
    with_predictions: bool = True,
    active: bool = True,
    trained_at: datetime | None = None,
) -> ModelVersion:
    """Org with two dataset versions, a churn model, and feature rows in both.

    Each customer's features are identical across both dataset versions unless
    `feature_shift` names the feature, so "no shift" means literally no change
    rather than a second independent draw (which would only measure sampling
    noise). `feature_shift` offsets the current (newest) version, which is what
    drives drift relative to the training baseline.
    """
    import numpy as np

    rng = np.random.default_rng(42)
    _org(db, org_id, f"Org{org_id}")
    db.add(Dataset(id=org_id, organization_id=org_id, name="orders", status="PROCESSED"))
    db.add(
        DatasetVersion(
            id=org_id,
            organization_id=org_id,
            dataset_id=org_id,
            version_no=1,
            file_path="v1.csv",
        )
    )
    db.add(
        DatasetVersion(
            id=100 + org_id,
            organization_id=org_id,
            dataset_id=org_id,
            version_no=2,
            file_path="v2.csv",
        )
    )
    db.add(Model(id=org_id, organization_id=org_id, name="Churn Prediction", model_type="churn", status="TRAINED"))
    version = ModelVersion(
        id=org_id,
        organization_id=org_id,
        model_id=org_id,
        version_no=1,
        artifact_path="artifacts/v1/model.joblib",
        preprocessing_path="artifacts/v1",
        features_list=list(CHURN_FEATURES),
        params_json={"family": "logistic"},
        metrics_json={"auc": 0.81},
        train_dataset_version_id=org_id,
        random_seed=42,
        training_duration_s=1.25,
        trained_at=trained_at or datetime.now(timezone.utc),
        active=active,
    )
    db.add(version)

    shift = feature_shift or {}
    for index in range(1, customers + 1):
        db.add(
            Customer(
                id=1000 * org_id + index,
                organization_id=org_id,
                external_customer_id=f"ext-{index}",
                email=f"c{index}@x.com",
            )
        )
        base = {
            "recency_days": float(rng.normal(30, 10)),
            "frequency": float(rng.normal(5, 1)),
            "monetary": float(abs(rng.normal(500, 90))),
            "aov": float(abs(rng.normal(100, 15))),
            "tenure_days": float(rng.normal(220, 40)),
            "discount_usage": round(float(rng.uniform(0, 0.4)), 4),
            "engagement_score": float(rng.normal(55, 12)),
            "clv_est": float(abs(rng.normal(300, 60))),
        }
        for version_id, is_current in ((org_id, False), (100 + org_id, True)):
            values = {
                name: base[name] + shift.get(name, 0.0) if is_current else base[name]
                for name in base
            }
            db.add(
                CustomerFeature(
                    organization_id=org_id,
                    customer_id=1000 * org_id + index,
                    dataset_version_id=version_id,
                    recency_days=values["recency_days"],
                    frequency=values["frequency"],
                    monetary=values["monetary"],
                    rfm_score="R3",
                    rfm_segment="LOYAL",
                    aov=values["aov"],
                    avg_days_between=14.0,
                    tenure_days=values["tenure_days"],
                    category_diversity=2,
                    discount_usage=values["discount_usage"],
                    engagement_score=values["engagement_score"],
                    clv_est=values["clv_est"],
                )
            )
            if is_current and with_predictions and version.active:
                db.add(
                    Prediction(
                        organization_id=org_id,
                        model_version_id=version.id,
                        customer_id=1000 * org_id + index,
                        prediction_proba=round(float(rng.uniform(0, 1)), 6),
                        prediction_label=False,
                        risk_level="HIGH" if index % 3 == 0 else "LOW",
                        created_at=datetime.now(timezone.utc),
                    )
                )
    db.commit()
    return version


# ---------------------------------------------------------------- drift report


def test_drift_report_stable_when_features_match(db_session):
    _seed(db_session)
    report = ms.drift_report(db_session, 1)

    assert report["model"]["model_type"] == "churn"
    assert report["model_version"]["features"] == list(CHURN_FEATURES)
    assert report["summary"]["drift_status"] == DriftStatus.STABLE
    assert report["summary"]["n_features_drifted"] == 0
    assert report["summary"]["psi_overall"] < 0.25
    assert report["baseline"]["source"] == "training_dataset_version"
    assert report["baseline"]["dataset_version_id"] == 1


def test_drift_report_flags_significant_feature_shift(db_session):
    _seed(db_session, feature_shift={"recency_days": 40.0})
    report = ms.drift_report(db_session, 1)

    assert report["summary"]["drift_status"] == DriftStatus.SIGNIFICANT
    assert "recency_days" in report["summary"]["drifted_features"]
    recency = next(f for f in report["features"] if f["feature"] == "recency_days")
    assert recency["psi"] is not None and recency["psi"] >= 0.25
    assert recency["ks"] > 0.3
    assert recency["mean_delta"] > 0


def test_drift_report_profiles_every_model_feature(db_session):
    _seed(db_session)
    report = ms.drift_report(db_session, 1)
    assert [f["feature"] for f in report["features"]] == list(CHURN_FEATURES)
    for metric in report["features"]:
        assert set(metric) >= {"feature", "status", "psi", "ks", "reference", "current", "reason"}
        assert metric["reference"]["n_total"] > 0
        assert metric["current"]["n_total"] > 0


def test_drift_report_baseline_absent_when_version_untrained_without_dataset(db_session):
    _seed(db_session)
    version = db_session.get(ModelVersion, 1)
    version.train_dataset_version_id = None
    db_session.commit()

    report = ms.drift_report(db_session, 1)
    assert report["baseline"]["source"] == "none"
    assert "without a recorded dataset version" in report["baseline"]["note"]
    # With no baseline every feature is unmeasurable rather than silently clean.
    assert all(f["status"] == DriftStatus.NO_DATA for f in report["features"])
    assert report["summary"]["psi_overall"] is None


def test_drift_report_small_sample_withholds_verdict(db_session, monkeypatch):
    _seed(db_session)
    monkeypatch.setattr(ms.settings, "monitoring_min_samples", 10_000)
    report = ms.drift_report(db_session, 1)

    assert all(f["status"] == DriftStatus.UNKNOWN for f in report["features"])
    assert all("verdict withheld" in (f["reason"] or "") for f in report["features"])
    assert report["summary"]["drift_status"] == DriftStatus.UNKNOWN


def test_drift_report_tenant_isolation_hides_other_org_versions(db_session):
    _seed(db_session, org_id=1)
    _seed(db_session, org_id=2)
    report = ms.drift_report(db_session, 2)
    assert report["model_version"]["id"] == 2
    assert report["model"]["id"] == 2


def test_drift_report_unknown_version_raises(db_session):
    _seed(db_session)
    with pytest.raises(NotFoundError):
        ms.drift_report(db_session, 1, model_version_id=999)


def test_drift_report_without_any_model_raises(db_session):
    _org(db_session, 1, "Empty")
    db_session.commit()
    with pytest.raises(NotFoundError):
        ms.drift_report(db_session, 1)


def test_drift_report_falls_back_to_newest_inactive_version(db_session):
    version = _seed(db_session, active=False)
    assert version.active is False
    report = ms.drift_report(db_session, 1)
    assert report["model_version"]["id"] == version.id
    assert report["health"]["active"] is False


def test_drift_report_explicit_version_from_another_org_is_rejected(db_session):
    _seed(db_session, org_id=1)
    _seed(db_session, org_id=2)
    with pytest.raises(NotFoundError):
        ms.drift_report(db_session, 1, model_version_id=2)


def test_drift_report_explicit_version_beats_active(db_session):
    _seed(db_session, org_id=1, active=True)
    db_session.add(
        ModelVersion(
            id=7,
            organization_id=1,
            model_id=1,
            version_no=2,
            artifact_path="artifacts/v2/model.joblib",
            preprocessing_path="artifacts/v2",
            features_list=list(CHURN_FEATURES),
            train_dataset_version_id=1,
            trained_at=datetime.now(timezone.utc),
            active=False,
        )
    )
    db_session.commit()

    report = ms.drift_report(db_session, 1, model_version_id=7)
    assert report["model_version"]["id"] == 7
    assert report["model_version"]["version_no"] == 2
    assert report["health"]["active"] is False


def test_drift_report_ignores_unusable_recorded_features(db_session):
    _seed(db_session)
    version = db_session.get(ModelVersion, 1)
    version.features_list = ["not_a_column", "monetary"]
    db_session.commit()
    report = ms.drift_report(db_session, 1)
    # Only `monetary` is a real column; the model-type default does not pad it.
    assert report["model_version"]["features"] == ["monetary"]


def test_drift_report_health_reports_coverage_and_staleness(db_session):
    stale = datetime.now(timezone.utc) - timedelta(days=200)
    _seed(db_session, trained_at=stale)
    report = ms.drift_report(db_session, 1)

    health = report["health"]
    assert health["n_population"] == 120
    assert health["n_predictions"] == 120
    assert health["prediction_coverage"] == 1.0
    assert health["stale"] is True
    assert health["days_since_trained"] >= 200
    assert health["training_metrics"] == {"auc": 0.81}


# ------------------------------------------------------------ prediction drift


def test_prediction_drift_first_run_establishes_baseline(db_session):
    _seed(db_session)
    report = ms.drift_report(db_session, 1)
    prediction = report["prediction"]

    assert prediction["baseline_source"] == "none"
    assert prediction["status"] == DriftStatus.UNKNOWN
    assert prediction["psi"] is None
    assert "becomes the baseline" in prediction["reason"]
    assert prediction["summary"]["n"] == 120
    assert prediction["summary"]["risk_mix"] == {"HIGH": 40, "LOW": 80}
    assert prediction["summary"]["histogram"]["edges"]


def test_prediction_drift_compares_against_first_recorded_run(db_session):
    _seed(db_session)
    first = ms.record_run(db_session, 1)
    assert first["prediction_drift_status"] == DriftStatus.UNKNOWN
    assert first["baseline_run_id"] is None

    second = ms.record_run(db_session, 1)
    assert second["baseline_run_id"] == first["id"]
    assert second["prediction_psi"] is not None
    # Same underlying predictions, so no meaningful shift between scans.
    assert second["prediction_drift_status"] == DriftStatus.STABLE


def test_prediction_drift_detects_shift(db_session):
    _seed(db_session)
    ms.record_run(db_session, 1)

    for row in db_session.query(Prediction).filter(Prediction.organization_id == 1).all():
        row.prediction_proba = min(1.0, row.prediction_proba + 0.45)
    db_session.commit()

    shifted = ms.drift_report(db_session, 1)
    assert shifted["prediction"]["status"] == DriftStatus.SIGNIFICANT
    assert shifted["prediction"]["psi"] >= 0.25


def test_prediction_profile_without_predictions_is_no_data(db_session):
    _seed(db_session, with_predictions=False)
    prediction = ms.drift_report(db_session, 1)["prediction"]
    assert prediction["status"] == DriftStatus.NO_DATA
    assert prediction["summary"]["n"] == 0
    assert "no scored customers" in prediction["reason"]


# ------------------------------------------------------------------- recording


def test_record_run_persists_snapshot(db_session):
    _seed(db_session)
    run = ms.record_run(db_session, 1, triggered_by=None)

    stored = db_session.get(MonitoringRun, run["id"])
    assert stored is not None
    assert stored.status == "COMPLETED"
    assert stored.model_id == 1
    assert stored.model_version_id == 1
    assert stored.reference_dataset_version_id == 1
    assert stored.n_current_rows == 120
    assert stored.n_reference_rows == 120
    assert stored.duration_s is not None
    assert stored.feature_metrics_json
    assert stored.health_json["n_predictions"] == 120
    assert stored.drift_status == DriftStatus.STABLE


def test_record_run_records_failure_without_losing_the_error(db_session):
    _seed(db_session)
    original = ms.drift_report
    try:
        ms.drift_report = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            ms.record_run(db_session, 1)
    finally:
        ms.drift_report = original

    run = db_session.query(MonitoringRun).one()
    assert run.status == "FAILED"
    assert "boom" in run.error


def test_list_runs_paginates_newest_first(db_session):
    _seed(db_session)
    ids = [ms.record_run(db_session, 1)["id"] for _ in range(3)]

    page = ms.list_runs(db_session, 1, limit=2)
    assert page["total"] == 3
    assert [item["id"] for item in page["items"]] == [ids[2], ids[1]]

    tail = ms.list_runs(db_session, 1, limit=2, offset=2)
    assert [item["id"] for item in tail["items"]] == [ids[0]]


def test_list_runs_filters_by_version_and_org(db_session):
    _seed(db_session, org_id=1)
    _seed(db_session, org_id=2)
    ms.record_run(db_session, 1)
    ms.record_run(db_session, 2)

    assert ms.list_runs(db_session, 1)["total"] == 1
    assert ms.list_runs(db_session, 1, model_version_id=2)["total"] == 0
    assert ms.list_runs(db_session, 2, model_version_id=2)["total"] == 1


def test_get_run_returns_full_detail(db_session):
    _seed(db_session)
    run = ms.record_run(db_session, 1)
    fetched = ms.get_run(db_session, 1, run["id"])

    assert fetched["feature_metrics"]
    assert fetched["prediction_summary"]["n"] == 120
    assert fetched["health"]["n_population"] == 120


def test_get_run_tenant_isolated(db_session):
    _seed(db_session, org_id=1)
    _seed(db_session, org_id=2)
    run = ms.record_run(db_session, 1)
    with pytest.raises(NotFoundError):
        ms.get_run(db_session, 2, run["id"])


def test_model_health_rolls_up_versions(db_session):
    _seed(db_session, org_id=1)
    ms.record_run(db_session, 1)

    rollup = ms.model_health(db_session, 1)
    assert rollup["total"] == 1
    item = rollup["items"][0]
    assert item["model_id"] == 1
    assert item["active"] is True
    assert item["n_predictions"] == 120
    assert item["last_run"]["status"] == "COMPLETED"
    assert item["last_run"]["drift_status"] == DriftStatus.STABLE
    assert rollup["thresholds"] == {"MODERATE": 0.1, "SIGNIFICANT": 0.25}


def test_model_health_before_any_scan_has_no_last_run(db_session):
    _seed(db_session)
    item = ms.model_health(db_session, 1)["items"][0]
    assert item["last_run"] is None
    assert item["n_predictions"] == 120


def test_scan_all_active_sweeps_every_active_version(db_session):
    _seed(db_session)
    result = ms.scan_all_active(db_session, 1)
    assert result["scanned"] == 1
    assert result["results"][0]["status"] == "COMPLETED"
    assert ms.list_runs(db_session, 1)["total"] == 1


def test_scan_all_active_skips_inactive_versions(db_session):
    _seed(db_session, active=False)
    assert ms.scan_all_active(db_session, 1)["scanned"] == 0
    assert ms.list_runs(db_session, 1)["total"] == 0


def test_scan_all_active_reports_failure_without_aborting(db_session):
    _seed(db_session)
    original = ms.record_run
    try:
        def flaky(db, org_id, model_version_id=None, **kwargs):
            raise RuntimeError("scoring exploded")

        ms.record_run = flaky
        result = ms.scan_all_active(db_session, 1)
    finally:
        ms.record_run = original

    assert result["scanned"] == 1
    assert result["results"][0]["status"] == "FAILED"
    assert "scoring exploded" in result["results"][0]["error"]
