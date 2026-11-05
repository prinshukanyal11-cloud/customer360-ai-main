"""What-if churn simulation.

Uses the active churn model's bundle to re-score a single customer under a
hypothetical feature change (e.g. "what if recency dropped to 20 days?").
Inputs are perturbed in original feature units, then run through the exact
same imputer + scaler + model the production scorer uses, guaranteeing the
scenario output is comparable to the baseline prediction. Simulations are
labeled as hypothetical — never presented as guarantees — and are not persisted.
"""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from sqlalchemy.orm import Session

from app.core.constants import AuditAction
from app.core.exceptions import InsufficientDataError, NotFoundError, ValidationError
from app.ml.churn import (
    CHURN_FEATURES,
    _latest_features,
    _matrix,
    _risk_level,
    active_churn_version,
    categorize_risk,
)
from app.models.customer_features_model import CustomerFeature
from app.models.transaction import Customer
from app.services import audit_service

FEATURE_COMMENTS: dict[str, tuple[str, str]] = {
    "recency_days": ("Recency (days since last order)", "Lower = more recent activity"),
    "frequency": ("Order frequency", "Higher = more repeat purchases"),
    "monetary": ("Lifetime spend", "Higher = deeper wallet share"),
    "aov": ("Average order value", "Higher = bigger baskets"),
    "avg_days_between": ("Avg days between orders", "Lower = tighter cadence"),
    "tenure_days": ("Tenure (days)", "Longer = more established"),
    "category_diversity": ("Categories bought", "Higher = more breadth"),
    "discount_usage": ("Discount usage", "0..1 share of discounted orders"),
    "engagement_score": ("Engagement score", "Higher = more engaged"),
    "clv_est": ("Estimated CLV", "Higher = more valuable"),
}

SCENARIOS: dict[str, dict[str, float]] = {
    "re_engage": {"recency_days": 15.0},
    "increase_frequency": {"frequency": 6.0, "avg_days_between": 30.0},
    "raise_basket": {"aov": 400.0},
    "reduce_discount": {"discount_usage": 0.1},
    "boost_engagement": {"engagement_score": 70.0},
    "diversify_categories": {"category_diversity": 3.0},
}


def simulate_whatif(
    db: Session,
    organization_id: int,
    customer_id: int,
    *,
    changes: dict[str, float] | None = None,
    scenario: str | None = None,
) -> dict:
    customer = db.get(Customer, customer_id)
    if customer is None or customer.organization_id != organization_id:
        raise NotFoundError("Customer not found in this organization.")

    version = active_churn_version(db, organization_id)
    if version is None:
        raise InsufficientDataError(
            "No active churn model for this organization. Train one first (POST /models/churn/train)."
        )
    bundle = joblib.load(Path(version.artifact_path) / "model.joblib")

    if scenario and scenario not in SCENARIOS:
        raise ValidationError(
            f"Unknown scenario. Choose one of: {', '.join(sorted(SCENARIOS))}.",
            details={"available": sorted(SCENARIOS)},
        )
    scenario_changes = SCENARIOS.get(scenario, {}) if scenario else {}

    proposed: dict[str, float] = {**scenario_changes, **(changes or {})}
    unknown = set(proposed) - set(CHURN_FEATURES)
    if unknown:
        raise ValidationError(
            f"Unknown feature(s): {', '.join(sorted(unknown))}.",
            details={"allowed": list(CHURN_FEATURES)},
        )
    for value in proposed.values():
        if value < 0 or not np.isfinite(value):
            raise ValidationError("Simulation values must be finite and non-negative.")

    features = None
    for f in _latest_features(db, organization_id):
        if f.customer_id == customer_id:
            features = f
            break
    if features is None or not features.frequency or features.frequency <= 0:
        raise InsufficientDataError(
            "This customer has no scored features (non-buyer). Score and explain before simulating."
        )

    median = _median_clv_for_org(db, organization_id)
    baseline = _predict_one(bundle, features, median)
    scenario_row = _perturbed(features, proposed)
    scenario_out = _predict_one(bundle, scenario_row, median)

    delta_probability = round(scenario_out["probability"] - baseline["probability"], 4)
    value_at_risk_b = round(baseline["probability"] * (features.clv_est or 0.0), 2)
    value_at_risk_s = round(scenario_out["probability"] * (scenario_row.clv_est or 0.0), 2)

    audit_service.audit(
        db,
        organization_id,
        None,
        AuditAction.WHATIF_SIMULATE,
        entity_type="customer",
        entity_id=customer_id,
        metadata_json={"changes": proposed, "scenario": scenario},
    )

    return {
        "customer_id": customer_id,
        "model_version_id": version.id,
        "family": bundle.get("family"),
        "scenario": scenario,
        "changes": {k: round(v, 4) for k, v in proposed.items()},
        "baseline": {
            "probability": baseline["probability"],
            "risk_level": baseline["risk_level"],
            "risk_category": baseline["risk_category"],
            "value_at_risk": value_at_risk_b,
        },
        "scenario_output": {
            "probability": scenario_out["probability"],
            "risk_level": scenario_out["risk_level"],
            "risk_category": scenario_out["risk_category"],
            "risk_level_changed": baseline["risk_level"] != scenario_out["risk_level"],
            "value_at_risk": value_at_risk_s,
        },
        "delta": {
            "probability": delta_probability,
            "value_at_risk": round(value_at_risk_s - value_at_risk_b, 2),
        },
        "median_clv": round(median, 2),
        "note": "Simulation uses the active model; it is a hypothesis, not a guarantee.",
    }


def _predict_one(bundle: dict, features: CustomerFeature, median_clv: float) -> dict:
    x, _ = _matrix([features])
    prepared = bundle["scaler"].transform(bundle["imputer"].transform(x))
    probability = float(bundle["model"].predict_proba(prepared)[:, 1][0])
    probability = round(probability, 4)
    return {
        "probability": probability,
        "risk_level": _risk_level(probability),
        "risk_category": categorize_risk(_risk_level(probability), features.clv_est, median_clv),
    }


def _perturbed(features: CustomerFeature, changes: dict[str, float]) -> CustomerFeature:
    clone = CustomerFeature(
        organization_id=features.organization_id,
        customer_id=features.customer_id,
        dataset_version_id=features.dataset_version_id,
        recency_days=features.recency_days,
        frequency=features.frequency,
        monetary=features.monetary,
        aov=features.aov,
        avg_days_between=features.avg_days_between,
        tenure_days=features.tenure_days,
        category_diversity=features.category_diversity,
        discount_usage=features.discount_usage,
        engagement_score=features.engagement_score,
        clv_est=features.clv_est,
    )
    for name, value in changes.items():
        setattr(clone, name, value)
    return clone


def _median_clv_for_org(db: Session, organization_id: int) -> float:
    from app.ml.churn import _median_clv  # noqa: PLC0415

    return _median_clv(db, organization_id)