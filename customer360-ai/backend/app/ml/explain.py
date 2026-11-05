"""SHAP-based explanations for the active churn model.

Computes local per-customer feature contributions and a global feature
importance summary for the predictions of the active churn model version,
persisting one Explanation row per prediction. Explanations are recomputed
whenever predictions are rescored (old rows cascade away with their
predictions).

SHAP output is normalized across model families: for binary estimators that
return per-class output (RandomForest), the "class 1" (churn) slice is used;
for scalars (logistic, xgboost, lightgbm) the native logit output is kept.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientDataError, NotFoundError
from app.ml.churn import CHURN_FEATURES, _latest_features, _matrix, active_churn_version
from app.models.customer_features_model import Explanation, Prediction

FEATURE_LABELS: dict[str, str] = {
    "recency_days": "Recency (days since last order)",
    "frequency": "Order frequency",
    "monetary": "Lifetime spend",
    "aov": "Average order value",
    "avg_days_between": "Avg days between orders",
    "tenure_days": "Tenure (days)",
    "category_diversity": "Categories bought",
    "discount_usage": "Discount usage",
    "engagement_score": "Engagement score",
    "clv_est": "Est. CLV",
}

BACKGROUND_ROWS = 100


def _native_contribution_shape(values, expected_value):
    values = np.asarray(values)
    if values.ndim == 3:  # per-class output (sklearn RandomForest)
        return values[..., 1], float(np.asarray(expected_value).reshape(-1)[1])
    if isinstance(expected_value, (list, tuple, np.ndarray)) and np.asarray(expected_value).size > 1:
        return values, float(np.asarray(expected_value).reshape(-1)[1])
    return values, float(expected_value)


def _explainer_for(model, family: str, background: np.ndarray):
    import shap  # noqa: PLC0415 — heavy import, load lazily

    if family == "logistic":
        return shap.LinearExplainer(model, background)
    return shap.TreeExplainer(model)


def _build_prepared_x(bundle: dict, features: list) -> np.ndarray:
    x, _ = _matrix(features)
    if len(x) == 0:
        return np.zeros((0, len(CHURN_FEATURES)), dtype=float)
    return bundle["scaler"].transform(bundle["imputer"].transform(x))


def _features_for(db: Session, org_id: int, customer_ids: list[int]) -> dict[int, object]:
    if not customer_ids:
        return {}
    return {f.customer_id: f for f in _latest_features(db, org_id) if f.customer_id in set(customer_ids)}


def explain_predictions(db: Session, org_id: int, *, customer_ids: list[int] | None = None) -> dict:
    """Compute and persist SHAP explanations for the active model's predictions."""

    version = active_churn_version(db, org_id)
    if version is None:
        raise InsufficientDataError(
            "No active churn model for this organization. Train one first (POST /models/churn/train), then score."
        )

    pred_stmt = select(Prediction).where(
        Prediction.organization_id == org_id,
        Prediction.model_version_id == version.id,
    )
    if customer_ids is not None:
        wanted = set(customer_ids)
        pred_stmt = pred_stmt.where(Prediction.customer_id.in_(wanted))
    predictions = db.execute(pred_stmt.order_by(Prediction.customer_id)).scalars().all()
    if not predictions:
        raise InsufficientDataError(
            "No predictions to explain. Score customers first (POST /predictions/churn)."
        )

    bundle = joblib.load(Path(version.artifact_path) / "model.joblib")
    features = _features_for(db, org_id, [p.customer_id for p in predictions])
    ordered = [features[p.customer_id] for p in predictions if p.customer_id in features]
    if not ordered:
        raise InsufficientDataError("Explanations unavailable: feature rows are missing for these customers.")

    x_prepared = _build_prepared_x(bundle, ordered)
    background = x_prepared[: min(BACKGROUND_ROWS, len(x_prepared))]
    explainer = _explainer_for(bundle["model"], bundle["family"], background)
    raw = explainer.shap_values(x_prepared)
    values, base_value = _native_contribution_shape(raw, explainer.expected_value)
    values = np.asarray(values, dtype=float)
    if values.shape == () or values.shape[0] == 0:
        raise InsufficientDataError("Explanations unavailable: model produced no SHAP values.")

    feature_values = {f.customer_id: f for f in ordered}
    importance_abs = np.mean(np.abs(values), axis=0)
    importance_sum = float(importance_abs.sum()) or 1.0

    existing = db.execute(
        select(Explanation.id).where(
            Explanation.organization_id == org_id,
            Explanation.prediction_id.in_([p.id for p in predictions]),
        )
    ).scalars().all()
    for row_id in existing:
        row = db.get(Explanation, row_id)
        if row is not None:
            db.delete(row)
    db.flush()

    items = []
    for idx, prediction in enumerate(predictions):
        feature_row = feature_values.get(prediction.customer_id)
        contributions = []
        for col, name in enumerate(CHURN_FEATURES):
            contribution = round(float(values[idx, col]), 6)
            contributions.append(
                {
                    "feature": name,
                    "label": FEATURE_LABELS.get(name, name),
                    "value": (
                        None if feature_row is None else getattr(feature_row, name, None)
                    ),
                    "contribution": contribution,
                    "share": round(float(abs(contribution)) / (np.abs(values[idx]).sum() or 1.0) * 100, 2),
                }
            )
        contributions.sort(key=lambda c: abs(c["contribution"]), reverse=True)
        items.append(
            {
                "prediction_id": prediction.id,
                "customer_id": prediction.customer_id,
                "risk_level": prediction.risk_level,
                "probability": round(float(prediction.prediction_proba), 4),
                "base_value": round(float(base_value), 6),
                "contributions": contributions,
            }
        )
        db.add(
            Explanation(
                organization_id=org_id,
                prediction_id=prediction.id,
                method="SHAP",
                base_value=round(float(base_value), 6),
                global_importance_json={
                    "features": {
                        name: round(float(importance_abs[col]), 6)
                        for col, name in enumerate(CHURN_FEATURES)
                    },
                    "total": round(importance_sum, 6),
                },
                local_contrib_json={
                    "base_value": round(float(base_value), 6),
                    "family": bundle["family"],
                    "features": {
                        c["feature"]: {"contribution": c["contribution"], "share": c["share"]}
                        for c in contributions
                    },
                },
                created_at=datetime.now(UTC),
            )
        )
    db.commit()

    global_importance = [
        {
            "feature": name,
            "label": FEATURE_LABELS.get(name, name),
            "importance": round(float(importance_abs[col]), 6),
            "share": round(float(importance_abs[col]) / importance_sum * 100, 2),
        }
        for col, name in enumerate(CHURN_FEATURES)
    ]
    global_importance.sort(key=lambda g: g["importance"], reverse=True)

    return {
        "model_id": version.model_id,
        "version_no": version.version_no,
        "family": bundle["family"],
        "n_explained": len(items),
        "base_value": round(float(base_value), 6),
        "global_importance": global_importance,
        "items": items,
    }


def explanation_for_customer(db: Session, org_id: int, customer_id: int) -> dict:
    """Return the stored explanation for a customer's latest churn prediction."""
    prediction = db.execute(
        select(Prediction)
        .where(
            Prediction.customer_id == customer_id,
            Prediction.organization_id == org_id,
        )
        .order_by(Prediction.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if prediction is None:
        raise NotFoundError("No churn prediction scored for this customer.")

    row = db.execute(
        select(Explanation).where(
            Explanation.prediction_id == prediction.id,
            Explanation.organization_id == org_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return explain_predictions(db, org_id, customer_ids=[customer_id])

    contributions = (row.local_contrib_json or {}).get("features", {})
    return {
        "prediction_id": prediction.id,
        "customer_id": customer_id,
        "risk_level": prediction.risk_level,
        "probability": float(prediction.prediction_proba),
        "base_value": row.base_value,
        "global_importance": [],
        "contributions": [
            {
                "feature": name,
                "label": FEATURE_LABELS.get(name, name),
                "value": None,
                "contribution": info.get("contribution"),
                "share": info.get("share"),
            }
            for name, info in contributions.items()
        ],
    }