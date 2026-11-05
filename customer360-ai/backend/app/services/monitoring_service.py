"""Model monitoring: feature drift, prediction drift, and model health.

Design notes
------------
* Feature drift compares the **latest feature rows** against the feature rows of
  `ModelVersion.train_dataset_version_id` - the genuine train-time baseline. The
  churn/segmentation trainers both read "latest features", so the recorded
  dataset version is what the model was actually fitted on.
* Prediction drift cannot use a train-time baseline, because `score_customers`
  deletes and recreates `predictions` rows and keeps no history. Instead the
  earliest recorded `MonitoringRun` for the version supplies a compact baseline
  histogram, so the first scan establishes the reference and later scans trend
  against it.
* Nothing is fabricated: an absent baseline, a degenerate reference, or a sample
  below `settings.monitoring_min_samples` is reported as `NO_DATA` / `UNKNOWN`
  with a human-readable reason, never as a clean bill of health.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import DriftMetric, DriftStatus, MonitoredModelType
from app.core.exceptions import NotFoundError, ValidationError
from app.ml.churn import CHURN_FEATURES
from app.ml.segmentation import FEATURE_METRICS
from app.models.customer_features_model import CustomerFeature, Model, ModelVersion, Prediction
from app.models.monitoring import MonitoringRun
from app.services import drift_metrics

_DEFAULT_FEATURES = {
    MonitoredModelType.CHURN: CHURN_FEATURES,
    MonitoredModelType.SEGMENTATION: FEATURE_METRICS,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _model_feature_names(model: Model, version: ModelVersion) -> list[str]:
    """Resolve the feature contract, preferring what the version recorded."""
    model_type = (model.model_type or "").lower()
    default = _DEFAULT_FEATURES.get(model_type, CHURN_FEATURES)
    columns = {column.key for column in CustomerFeature.__table__.columns}
    recorded = version.features_list if isinstance(version.features_list, list) else []
    usable = [str(name) for name in recorded if str(name) in columns]
    # Fall back to the model-type default when nothing usable was recorded.
    return usable or [name for name in default if name in columns]


def _latest_rows(
    db: Session, organization_id: int, *, dataset_version_id: int | None = None
) -> list[CustomerFeature]:
    """Most recent feature row per customer (optionally pinned to one dataset version)."""
    stmt = select(CustomerFeature.customer_id, func.max(CustomerFeature.id)).where(
        CustomerFeature.organization_id == organization_id
    )
    if dataset_version_id is not None:
        stmt = stmt.where(CustomerFeature.dataset_version_id == dataset_version_id)
    latest = stmt.group_by(CustomerFeature.customer_id).subquery()
    rows = db.execute(
        select(CustomerFeature).where(CustomerFeature.id.in_(select(latest.c[1])))
    ).scalars()
    return list(rows)


def _values(rows: list[CustomerFeature], feature: str) -> list[float | None]:
    return [getattr(row, feature, None) for row in rows]


def _buyers(rows: list[CustomerFeature]) -> list[CustomerFeature]:
    """Both trainers score buyers only (`frequency > 0`); keep the same population."""
    return [row for row in rows if (row.frequency or 0) > 0]


def _feature_metric(
    feature: str, reference: list[float | None], current: list[float | None]
) -> dict:
    min_samples = settings.monitoring_min_samples
    ref_summary = drift_metrics.summarize(reference)
    cur_summary = drift_metrics.summarize(current)
    psi_value: float | None = None
    ks_value: float | None = None
    reason: str | None = None

    if ref_summary["n"] == 0 or cur_summary["n"] == 0:
        status = DriftStatus.NO_DATA
        reason = "no observed values on one side of the comparison"
    elif min(ref_summary["n"], cur_summary["n"]) < min_samples:
        status = DriftStatus.UNKNOWN
        reason = f"fewer than {min_samples} observations; verdict withheld"
    else:
        bins = drift_metrics.effective_bins(
            ref_summary["n"],
            max_bins=settings.drift_bins,
            min_per_bin=settings.monitoring_min_per_bin,
        )
        psi_value = drift_metrics.psi(reference, current, bins=bins)
        ks_value = drift_metrics.ks_statistic(reference, current)
        status = drift_metrics.classify(psi_value, settings.drift_psi_thresholds)
        if psi_value is None:
            reason = "reference distribution is constant; PSI is undefined"

    metric: dict = {
        "feature": feature,
        "status": status,
        "psi": psi_value,
        "ks": ks_value,
        "reference": ref_summary,
        "current": cur_summary,
        "mean_delta": None,
        "missing_rate_delta": None,
        "reason": reason,
    }
    if ref_summary["mean"] is not None and cur_summary["mean"] is not None:
        metric["mean_delta"] = round(cur_summary["mean"] - ref_summary["mean"], 6)
    if ref_summary["missing_rate"] is not None and cur_summary["missing_rate"] is not None:
        metric["missing_rate_delta"] = round(cur_summary["missing_rate"] - ref_summary["missing_rate"], 6)
    return metric


def _resolve_version(db: Session, organization_id: int, model_version_id: int | None) -> tuple[Model, ModelVersion]:
    """Resolve an explicit version, else the org's active version, else its newest."""
    if model_version_id is not None:
        version = db.execute(
            select(ModelVersion).where(
                ModelVersion.id == model_version_id,
                ModelVersion.organization_id == organization_id,
            )
        ).scalar_one_or_none()
        if version is None:
            raise NotFoundError("Model version not found.")
        model = db.get(Model, version.model_id)
        if model is None:
            raise NotFoundError("Model not found.")
        return model, version

    version = db.execute(
        select(ModelVersion)
        .join(Model, Model.id == ModelVersion.model_id)
        .where(
            ModelVersion.organization_id == organization_id,
            ModelVersion.active.is_(True),
        )
        .order_by(ModelVersion.trained_at.desc().nullslast(), ModelVersion.id.desc())
    ).scalars().first()
    if version is None:
        version = db.execute(
            select(ModelVersion)
            .where(ModelVersion.organization_id == organization_id)
            .order_by(ModelVersion.trained_at.desc().nullslast(), ModelVersion.id.desc())
        ).scalars().first()
    if version is None:
        raise NotFoundError("No trained model version is available to monitor.")
    model = db.get(Model, version.model_id)
    if model is None:
        raise NotFoundError("Model not found.")
    return model, version


def _baseline_run(
    db: Session, organization_id: int, model_version_id: int, *, exclude_id: int | None = None
) -> MonitoringRun | None:
    """Earliest completed run for a version - the prediction-drift baseline."""
    stmt = (
        select(MonitoringRun)
        .where(
            MonitoringRun.organization_id == organization_id,
            MonitoringRun.model_version_id == model_version_id,
            MonitoringRun.status == "COMPLETED",
            MonitoringRun.prediction_summary_json.is_not(None),
        )
        .order_by(MonitoringRun.id.asc())
    )
    if exclude_id is not None:
        stmt = stmt.where(MonitoringRun.id != exclude_id)
    return db.execute(stmt).scalars().first()


def _prediction_profile(
    db: Session,
    organization_id: int,
    model_version_id: int,
    *,
    exclude_run_id: int | None = None,
) -> dict:
    """Distribution shift of `prediction_proba` against the first recorded scan."""
    predictions = list(
        db.execute(
            select(Prediction).where(
                Prediction.organization_id == organization_id,
                Prediction.model_version_id == model_version_id,
            )
        ).scalars()
    )
    probas = [row.prediction_proba for row in predictions if row.prediction_proba is not None]
    risk_mix: dict[str, int] = {}
    for row in predictions:
        if row.risk_level:
            risk_mix[row.risk_level] = risk_mix.get(row.risk_level, 0) + 1

    summary = drift_metrics.summarize(probas)
    summary["risk_mix"] = dict(sorted(risk_mix.items()))
    bins = drift_metrics.effective_bins(
        summary["n"],
        max_bins=settings.drift_bins,
        min_per_bin=settings.monitoring_min_per_bin,
    )
    histogram = drift_metrics.serialize_histogram(probas, bins=bins)

    baseline = _baseline_run(db, organization_id, model_version_id, exclude_id=exclude_run_id)
    baseline_summary = (baseline.prediction_summary_json or {}) if baseline is not None else {}
    baseline_histogram = (baseline_summary.get("histogram") if isinstance(baseline_summary, dict) else None) or None

    psi_value = None
    status = DriftStatus.NO_DATA
    reason: str | None = None
    if summary["n"] == 0:
        reason = "this version has no scored customers"
    elif summary["n"] < settings.monitoring_min_samples:
        status = DriftStatus.UNKNOWN
        reason = f"fewer than {settings.monitoring_min_samples} scored rows; verdict withheld"
    elif not baseline_histogram:
        status = DriftStatus.UNKNOWN
        reason = "no earlier scan to compare against; this run becomes the baseline"
    else:
        psi_value = drift_metrics.psi_against_histogram(probas, baseline_histogram, bins=bins)
        status = drift_metrics.classify(psi_value, settings.drift_psi_thresholds)
        if psi_value is None:
            reason = "baseline histogram is unusable; PSI is undefined"

    return {
        "metric": DriftMetric.PREDICTION,
        "status": status,
        "psi": psi_value,
        "baseline_run_id": baseline.id if baseline is not None else None,
        "baseline_source": "first_monitoring_run" if baseline_histogram else "none",
        "baseline_n": baseline_summary.get("n") if isinstance(baseline_summary, dict) else None,
        "summary": {**summary, "histogram": histogram},
        "reason": reason,
    }


def _health(
    db: Session,
    organization_id: int,
    model: Model,
    version: ModelVersion,
    *,
    n_population: int,
    n_predictions: int,
) -> dict:
    max_age = settings.monitoring_max_staleness_days
    days_since = None
    if isinstance(version.trained_at, datetime):
        reference = version.trained_at
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        days_since = max((_utcnow() - reference).days, 0)
    return {
        "days_since_trained": days_since,
        "max_staleness_days": max_age,
        "stale": bool(days_since is not None and days_since > max_age),
        "training_duration_s": version.training_duration_s,
        "training_metrics": version.metrics_json,
        "n_population": n_population,
        "n_predictions": n_predictions,
        "prediction_coverage": round(n_predictions / n_population, 6) if n_population else None,
        "active": bool(version.active),
    }


def drift_report(db: Session, organization_id: int, *, model_version_id: int | None = None) -> dict:
    """Compute a drift/health snapshot without persisting it."""
    model, version = _resolve_version(db, organization_id, model_version_id)
    features = _model_feature_names(model, version)

    current_rows = _buyers(_latest_rows(db, organization_id))
    reference_rows: list[CustomerFeature] = []
    baseline_source = "none"
    baseline_note = "this version was trained without a recorded dataset version"
    if version.train_dataset_version_id is not None:
        reference_rows = _buyers(
            _latest_rows(db, organization_id, dataset_version_id=version.train_dataset_version_id)
        )
        if reference_rows:
            baseline_source = "training_dataset_version"
            baseline_note = "features at the dataset version this model was trained on"
        else:
            baseline_note = (
                f"dataset version {version.train_dataset_version_id} has no surviving feature rows"
            )

    metrics = [_feature_metric(name, _values(reference_rows, name), _values(current_rows, name)) for name in features]
    measurable = [metric["psi"] for metric in metrics if metric["psi"] is not None]
    statuses = [metric["status"] for metric in metrics]
    drifted = [metric["feature"] for metric in metrics if drift_metrics.is_drifted(metric["status"])]

    prediction = _prediction_profile(db, organization_id, version.id)
    health = _health(
        db,
        organization_id,
        model,
        version,
        n_population=len(current_rows),
        n_predictions=prediction["summary"]["n"],
    )
    last_run = db.execute(
        select(MonitoringRun)
        .where(
            MonitoringRun.organization_id == organization_id,
            MonitoringRun.model_version_id == version.id,
        )
        .order_by(MonitoringRun.id.desc())
    ).scalars().first()

    return {
        "organization_id": organization_id,
        "model": {
            "id": model.id,
            "name": model.name,
            "model_type": model.model_type,
            "family": model.family,
            "status": model.status,
        },
        "model_version": {
            "id": version.id,
            "version_no": version.version_no,
            "trained_at": _iso(version.trained_at),
            "active": bool(version.active),
            "train_dataset_version_id": version.train_dataset_version_id,
            "features": features,
        },
        "baseline": {
            "source": baseline_source,
            "dataset_version_id": version.train_dataset_version_id,
            "note": baseline_note,
        },
        "summary": {
            "drift_status": drift_metrics.worst(statuses),
            "psi_overall": round(sum(measurable) / len(measurable), 6) if measurable else None,
            "n_features_profiled": len(features),
            "n_features_drifted": len(drifted),
            "drifted_features": drifted,
        },
        "features": metrics,
        "prediction": prediction,
        "health": health,
        "thresholds": dict(settings.drift_psi_thresholds),
        "min_samples": settings.monitoring_min_samples,
        "last_run_id": last_run.id if last_run is not None else None,
        "generated_at": _iso(_utcnow()),
    }


def record_run(
    db: Session,
    organization_id: int,
    model_version_id: int | None = None,
    *,
    triggered_by: int | None = None,
) -> dict:
    """Run a scan and persist it so drift can be trended over time."""
    started = _utcnow()
    model, version = _resolve_version(db, organization_id, model_version_id)
    try:
        report = drift_report(db, organization_id, model_version_id=version.id)
    except (NotFoundError, ValidationError):
        raise
    except Exception as exc:  # noqa: BLE001 - recorded as a FAILED run rather than lost
        db.rollback()
        run = MonitoringRun(
            organization_id=organization_id,
            model_id=model.id,
            model_version_id=version.id,
            status="FAILED",
            error=str(exc)[:2000],
            triggered_by=triggered_by,
            finished_at=_utcnow(),
        )
        db.add(run)
        db.commit()
        raise

    summary = report["summary"]
    prediction = report["prediction"]
    run = MonitoringRun(
        organization_id=organization_id,
        model_id=model.id,
        model_version_id=version.id,
        status="COMPLETED",
        drift_status=summary["drift_status"],
        psi_overall=summary["psi_overall"],
        n_features_drifted=summary["n_features_drifted"],
        drifted_features_json=summary["drifted_features"],
        feature_metrics_json=report["features"],
        prediction_psi=prediction["psi"],
        prediction_drift_status=prediction["status"],
        prediction_summary_json=prediction["summary"],
        health_json=report["health"],
        n_reference_rows=_reference_row_count(db, organization_id, version),
        n_current_rows=report["health"]["n_population"],
        reference_dataset_version_id=version.train_dataset_version_id,
        baseline_run_id=prediction["baseline_run_id"],
        duration_s=round((_utcnow() - started).total_seconds(), 6),
        triggered_by=triggered_by,
        finished_at=_utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return get_run(db, organization_id, run.id)


def _reference_row_count(db: Session, organization_id: int, version: ModelVersion) -> int | None:
    if version.train_dataset_version_id is None:
        return None
    return int(
        db.execute(
            select(func.count(CustomerFeature.id)).where(
                CustomerFeature.organization_id == organization_id,
                CustomerFeature.dataset_version_id == version.train_dataset_version_id,
                CustomerFeature.frequency > 0,
            )
        ).scalar_one()
    )


def _run_payload(run: MonitoringRun) -> dict:
    return {
        "id": run.id,
        "model_id": run.model_id,
        "model_version_id": run.model_version_id,
        "reference_dataset_version_id": run.reference_dataset_version_id,
        "baseline_run_id": run.baseline_run_id,
        "triggered_by": run.triggered_by,
        "created_at": _iso(run.created_at),
        "finished_at": _iso(run.finished_at),
        **run.metrics,
    }


def list_runs(
    db: Session,
    organization_id: int,
    *,
    model_version_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    stmt = select(MonitoringRun).where(MonitoringRun.organization_id == organization_id)
    count_stmt = select(func.count(MonitoringRun.id)).where(
        MonitoringRun.organization_id == organization_id
    )
    if model_version_id is not None:
        stmt = stmt.where(MonitoringRun.model_version_id == model_version_id)
        count_stmt = count_stmt.where(MonitoringRun.model_version_id == model_version_id)
    total = int(db.execute(count_stmt).scalar_one())
    runs = db.execute(
        stmt.order_by(MonitoringRun.id.desc()).limit(max(int(limit), 1)).offset(max(int(offset), 0))
    ).scalars()
    return {
        "items": [_run_payload(run) for run in runs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_run(db: Session, organization_id: int, run_id: int) -> dict:
    run = db.execute(
        select(MonitoringRun).where(
            MonitoringRun.id == run_id,
            MonitoringRun.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("Monitoring run not found.")
    payload = _run_payload(run)
    payload["feature_metrics"] = run.feature_metrics_json or []
    payload["prediction_summary"] = run.prediction_summary_json or {}
    payload["health"] = run.health_json or {}
    return payload


def model_health(db: Session, organization_id: int) -> dict:
    """Per-model roll-up: active version, prediction volume, and latest verdict."""
    versions = db.execute(
        select(ModelVersion)
        .where(ModelVersion.organization_id == organization_id)
        .order_by(ModelVersion.model_id, ModelVersion.version_no.desc())
    ).scalars()
    models = {
        model.id: model
        for model in db.execute(
            select(Model).where(Model.organization_id == organization_id).order_by(Model.id)
        ).scalars()
    }

    items: list[dict] = []
    for version in versions:
        model = models.get(version.model_id)
        if model is None:
            continue
        n_predictions = int(
            db.execute(
                select(func.count(Prediction.id)).where(
                    Prediction.organization_id == organization_id,
                    Prediction.model_version_id == version.id,
                )
            ).scalar_one()
        )
        last_run = db.execute(
            select(MonitoringRun)
            .where(
                MonitoringRun.organization_id == organization_id,
                MonitoringRun.model_version_id == version.id,
            )
            .order_by(MonitoringRun.id.desc())
        ).scalars().first()
        health = (last_run.health_json if last_run is not None else None) or {}
        items.append(
            {
                "model_id": model.id,
                "model_name": model.name,
                "model_type": model.model_type,
                "family": model.family,
                "model_version_id": version.id,
                "version_no": version.version_no,
                "active": bool(version.active),
                "trained_at": _iso(version.trained_at),
                "n_predictions": n_predictions,
                "days_since_trained": health.get("days_since_trained"),
                "stale": health.get("stale", False),
                "prediction_coverage": health.get("prediction_coverage"),
                "training_metrics": version.metrics_json,
                "last_run": None
                if last_run is None
                else {
                    "id": last_run.id,
                    "drift_status": last_run.drift_status,
                    "psi_overall": last_run.psi_overall,
                    "prediction_drift_status": last_run.prediction_drift_status,
                    "status": last_run.status,
                    "created_at": _iso(last_run.created_at),
                },
            }
        )
    return {"items": items, "total": len(items), "thresholds": dict(settings.drift_psi_thresholds)}


def scan_all_active(db: Session, organization_id: int, *, triggered_by: int | None = None) -> dict:
    """Scan every active model version for an org (used by the Celery task)."""
    versions = db.execute(
        select(ModelVersion)
        .where(
            ModelVersion.organization_id == organization_id,
            ModelVersion.active.is_(True),
        )
        .order_by(ModelVersion.id)
    ).scalars()

    results = []
    for version in versions:
        try:
            run = record_run(db, organization_id, version.id, triggered_by=triggered_by)
            results.append(
                {"model_version_id": version.id, "status": "COMPLETED", "run_id": run["id"]}
            )
        except Exception as exc:  # noqa: BLE001 - one bad version must not abort the sweep
            results.append({"model_version_id": version.id, "status": "FAILED", "error": str(exc)[:500]})
    return {
        "organization_id": organization_id,
        "scanned": len(results),
        "results": results,
    }
