"""Rule-based retention recommendation engine.

Translates each scored customer's latest features and churn prediction into
explainable, actionable retention actions. Rules are deterministic, documented,
and reference the concrete feature values that triggered them — the same inputs
SHAP uses — so a recommendation is as auditable as an explanation.

Rules only apply when an active churn model exists (predictions drive the risk
assessment). Generated recommendations are persisted to `recommendations`
(retention type) and replaced wholesale per (org, model_version, customer).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import joblib
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AuditAction,
    PriorityLevel,
    RecommendationAction,
    RecommendationType,
    RiskLevel,
)
from app.core.exceptions import InsufficientDataError
from app.ml.churn import _latest_features, _median_clv, active_churn_version
from app.ml.explain import FEATURE_LABELS
from app.models.customer_features_model import (
    CustomerFeature,
    Explanation,
    Prediction,
    RetentionRecommendation,
)
from app.models.transaction import Customer
from app.services import audit_service

DISPLAY_LABEL = FEATURE_LABELS


@dataclass(frozen=True)
class RetentionRule:
    id: str
    action: str
    objective: str
    priority: str
    priority_weight: float  # 0..1 multiplier for the priority score
    confidence: float  # 0..1
    applies: staticmethod  # (features, prediction, ctx) -> bool
    reason: staticmethod  # (features, prediction, ctx) -> str


def _high_risk(prediction: Prediction) -> bool:
    return prediction.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def _medium_plus(prediction: Prediction) -> bool:
    return prediction.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)


def _num(features: CustomerFeature | None, name: str) -> float | None:
    return getattr(features, name, None)


# --- rule definitions -----------------------------------------------------
def _rule_win_back(features, prediction, ctx) -> bool:
    if not _high_risk(prediction):
        return False
    return (features.recency_days or 0) > ctx.window_days


def _rule_win_back_reason(features, prediction, ctx) -> str:
    return (
        f"Recency ({features.recency_days} days) exceeds the {ctx.window_days}-day churn window; "
        f"probability {prediction.prediction_proba:.0%}. Send a win-back offer with a time-bound incentive."
    )


def _rule_loyalty(features, prediction, ctx) -> bool:
    if not _medium_plus(prediction):
        return False
    return (features.discount_usage or 0) >= 0.5


def _rule_loyalty_reason(features, prediction, ctx) -> str:
    return (
        f"Discount usage is {features.discount_usage:.0%} and churn risk is {prediction.risk_level}. "
        "Shift the relationship to a loyalty program instead of discount-led purchases."
    )


def _rule_reengagement(features, prediction, ctx) -> bool:
    if not _medium_plus(prediction):
        return False
    return (features.engagement_score or 101) <= 40


def _rule_reengagement_reason(features, prediction, ctx) -> str:
    return (
        f"Engagement score {features.engagement_score} is low for a {prediction.risk_level}-risk "
        "customer. Launch a personalized re-engagement email with a call-to-action."
    )


def _rule_cross_sell(features, prediction, ctx) -> bool:
    if not _medium_plus(prediction):
        return False
    return (features.category_diversity or 99) <= 1


def _rule_cross_sell_reason(features, prediction, ctx) -> str:
    return (
        f"Category diversity is {features.category_diversity} (average {ctx.avg_category_diversity:.1f}) "
        "with elevated churn risk. Cross-sell a complementary category to deepen stickiness."
    )


def _rule_subscription(features, prediction, ctx) -> bool:
    if not _medium_plus(prediction):
        return False
    return (features.frequency or 0) <= 3 and (features.avg_days_between or 0) >= 60


def _rule_subscription_reason(features, prediction, ctx) -> str:
    return (
        f"Frequency ({int(features.frequency or 0)}) and gap ({features.avg_days_between:.0f} days) "
        "signal irregular buying. Offer a subscription/replenishment plan to restore cadence."
    )


def _rule_concierge(features, prediction, ctx) -> bool:
    return _high_risk(prediction) and (features.clv_est or 0) >= ctx.median_clv


def _rule_concierge_reason(features, prediction, ctx) -> str:
    return (
        f"High-value account (CLV {features.clv_est:.2f}) at {prediction.risk_level} churn risk. "
        "Assign a concierge save: personal outreach by account owner with a targeted retention offer."
    )


def _rule_cycle(features, prediction, ctx) -> bool:
    if not _medium_plus(prediction):
        return False
    return (features.avg_days_between or 0) >= ctx.window_days


def _rule_cycle_reason(features, prediction, ctx) -> str:
    return (
        f"Avg order gap {features.avg_days_between:.0f} days approaches/exceeds the {ctx.window_days}-day "
        "window. Reduce the cycle: replenishment reminders and a next-purchase incentive."
    )


def _rule_nourish(features, prediction, ctx) -> bool:
    return prediction.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM) and (features.clv_est or 0) >= ctx.median_clv


def _rule_nourish_reason(features, prediction, ctx) -> str:
    return (
        f"Healthy relationship (risk {prediction.risk_level}, CLV {features.clv_est:.2f}). "
        "Nourish with early access and rewards; no discount required."
    )


RULES: list[RetentionRule] = [
    RetentionRule(
        id="win_back",
        action=RecommendationAction.WIN_BACK_OFFER,
        objective="Reactivate lapsed high-risk buyers",
        priority=PriorityLevel.HIGH,
        priority_weight=1.0,
        confidence=0.85,
        applies=_rule_win_back,
        reason=_rule_win_back_reason,
    ),
    RetentionRule(
        id="concierge_save",
        action=RecommendationAction.CONCIERGE_SAVE,
        objective="Protect high-value revenue",
        priority=PriorityLevel.HIGH,
        priority_weight=1.0,
        confidence=0.9,
        applies=_rule_concierge,
        reason=_rule_concierge_reason,
    ),
    RetentionRule(
        id="cycle_reduce",
        action=RecommendationAction.CYCLE_REDUCE,
        objective="Shorten the repurchase cycle",
        priority=PriorityLevel.MEDIUM,
        priority_weight=0.8,
        confidence=0.7,
        applies=_rule_cycle,
        reason=_rule_cycle_reason,
    ),
    RetentionRule(
        id="loyalty_program",
        action=RecommendationAction.LOYALTY_PROGRAM,
        objective="Move from discount-led to loyalty-led demand",
        priority=PriorityLevel.MEDIUM,
        priority_weight=0.8,
        confidence=0.75,
        applies=_rule_loyalty,
        reason=_rule_loyalty_reason,
    ),
    RetentionRule(
        id="reengagement",
        action=RecommendationAction.REENGAGEMENT,
        objective="Rebuild engagement with personalized touch",
        priority=PriorityLevel.MEDIUM,
        priority_weight=0.7,
        confidence=0.7,
        applies=_rule_reengagement,
        reason=_rule_reengagement_reason,
    ),
    RetentionRule(
        id="cross_sell",
        action=RecommendationAction.CROSS_SELL,
        objective="Increase category breadth",
        priority=PriorityLevel.LOW,
        priority_weight=0.5,
        confidence=0.6,
        applies=_rule_cross_sell,
        reason=_rule_cross_sell_reason,
    ),
    RetentionRule(
        id="subscription_push",
        action=RecommendationAction.SUBSCRIPTION_PUSH,
        objective="Restore purchase cadence",
        priority=PriorityLevel.LOW,
        priority_weight=0.5,
        confidence=0.6,
        applies=_rule_subscription,
        reason=_rule_subscription_reason,
    ),
    RetentionRule(
        id="nourish",
        action=RecommendationAction.NOURISH,
        objective="Deepen loyalty of valuable healthy customers",
        priority=PriorityLevel.LOW,
        priority_weight=0.4,
        confidence=0.8,
        applies=_rule_nourish,
        reason=_rule_nourish_reason,
    ),
]


class RuleContext:
    def __init__(self, window_days: int, median_clv: float, avg_category_diversity: float) -> None:
        self.window_days = window_days
        self.median_clv = median_clv
        self.avg_category_diversity = avg_category_diversity


RULES_BY_ID = {rule.id: rule for rule in RULES}


def generate_recommendations(
    db: Session,
    organization_id: int,
    *,
    customer_ids: list[int] | None = None,
    min_risk_level: str = RiskLevel.MEDIUM,
    limit: int = 200,
) -> dict:
    version = active_churn_version(db, organization_id)
    if version is None:
        raise InsufficientDataError(
            "No active churn model for this organization. Train and score customers first "
            "(POST /models/churn/train, POST /predictions/churn)."
        )

    bundle = joblib.load(Path(version.artifact_path) / "model.joblib")
    window_days = int(bundle.get("churn_window_days", settings.churn_window_days))

    feature_rows = {f.customer_id: f for f in _latest_features(db, organization_id) if f.frequency and f.frequency > 0}
    if customer_ids is not None:
        wanted = set(customer_ids)
        feature_rows = {cid: f for cid, f in feature_rows.items() if cid in wanted}
    if not feature_rows:
        return {"model_version_id": version.id, "n_generated": 0, "items": []}

    pred_stmt = select(Prediction).where(
        Prediction.organization_id == organization_id,
        Prediction.model_version_id == version.id,
        Prediction.customer_id.in_(feature_rows.keys()),
    )
    predictions = {p.customer_id: p for p in db.execute(pred_stmt).scalars().all()}

    explanation_map: dict[int, dict] = {}
    for row in db.execute(
        select(Explanation).join(Prediction, Prediction.id == Explanation.prediction_id).where(
            Prediction.organization_id == organization_id,
            Prediction.model_version_id == version.id,
            Prediction.customer_id.in_(feature_rows.keys()),
        )
    ).scalars():
        customer_id = db.execute(
            select(Prediction.customer_id).where(Prediction.id == row.prediction_id)
        ).scalar_one()
        explanation_map[customer_id] = row.local_contrib_json or {}

    customer_names = {
        row.id: row.name or row.external_customer_id or row.email or f"#{row.id}"
        for row in db.execute(
            select(Customer).where(
                Customer.organization_id == organization_id,
                Customer.id.in_(feature_rows.keys()),
            )
        ).scalars()
    }

    risk_order = {level: i for i, level in enumerate(RiskLevel.ALL)}
    min_order = risk_order.get(min_risk_level, risk_order[RiskLevel.MEDIUM])
    avg_category_diversity = _avg(feature_rows, "category_diversity")
    ctx = RuleContext(window_days, _median_clv(db, organization_id), avg_category_diversity)

    candidates = []
    for customer_id, features in feature_rows.items():
        prediction = predictions.get(customer_id)
        if prediction is None or risk_order.get(prediction.risk_level, 0) < min_order:
            continue
        top_driver = _top_driver(explanation_map.get(customer_id))
        for rule in RULES:
            if not rule.applies(features, prediction, ctx):
                continue
            reason = rule.reason(features, prediction, ctx)
            if top_driver:
                reason += f" SHAP top driver: {top_driver}."
            candidates.append(
                {
                    "customer_id": customer_id,
                    "name": customer_names.get(customer_id, f"#{customer_id}"),
                    "rule": rule,
                    "probability": prediction.prediction_proba,
                    "clv_est": features.clv_est or 0.0,
                    "priority_score": round(
                        prediction.prediction_proba * 100 * rule.priority_weight
                        + min((features.clv_est or 0.0) / max(ctx.median_clv, 1.0), 1.0) * rule.priority_weight * 10,
                        2,
                    ),
                    "reason": reason,
                }
            )

    candidates.sort(key=lambda c: (-c["priority_score"], -c["probability"]))

    db.execute(
        delete(RetentionRecommendation).where(
            RetentionRecommendation.organization_id == organization_id,
            RetentionRecommendation.type == RecommendationType.RETENTION,
        )
    )
    db.flush()

    items = []
    for cand in candidates[:limit]:
        rule = cand["rule"]
        row = RetentionRecommendation(
            organization_id=organization_id,
            customer_id=cand["customer_id"],
            type=RecommendationType.RETENTION,
            action=rule.action,
            reason=cand["reason"],
            priority_score=cand["priority_score"],
            confidence=rule.confidence,
            expected_objective=rule.objective,
            rules_triggered_json={"rules": [rule.id], "priority": rule.priority, "label": DISPLAY_LABEL},
            model_version_id=version.id,
            created_at=datetime.now(UTC),
        )
        db.add(row)
        db.flush()
        items.append(
            {
                "id": row.id,
                "customer_id": cand["customer_id"],
                "name": cand["name"],
                "type": RecommendationType.RETENTION,
                "action": rule.action,
                "priority": rule.priority,
                "priority_score": cand["priority_score"],
                "confidence": rule.confidence,
                "expected_objective": rule.objective,
                "rules": [rule.id],
                "reason": cand["reason"],
            }
        )

    db.commit()
    if items:
        audit_service.audit(
            db,
            organization_id,
            None,
            AuditAction.RECOMMENDATION_GENERATE,
            entity_type="model_version",
            entity_id=version.id,
            metadata_json={"n": len(items)},
        )

    return {
        "model_version_id": version.id,
        "model_family": bundle.get("family"),
        "window_days": window_days,
        "n_generated": len(items),
        "items": items,
    }


def list_recommendations(
    db: Session,
    organization_id: int,
    *,
    customer_id: int | None = None,
    min_priority: str = PriorityLevel.LOW,
) -> dict:
    stmt = select(RetentionRecommendation).where(
        RetentionRecommendation.organization_id == organization_id
    )
    if customer_id is not None:
        stmt = stmt.where(RetentionRecommendation.customer_id == customer_id)
    rows = db.execute(
        stmt.order_by(RetentionRecommendation.priority_score.desc(), RetentionRecommendation.id.asc())
    ).scalars().all()

    items = []
    for row in rows:
        priority = (row.rules_triggered_json or {}).get("priority") or item_priority(row)
        if not _priority_passing(priority, min_priority):
            continue
        customer = db.get(Customer, row.customer_id)
        items.append(
            {
                "id": row.id,
                "customer_id": row.customer_id,
                "name": (
                    (customer.name or customer.external_customer_id or customer.email or f"#{customer.id}")
                    if customer
                    else f"#{row.customer_id}"
                ),
                "type": row.type,
                "action": row.action,
                "priority": priority,
                "priority_score": row.priority_score,
                "confidence": row.confidence,
                "expected_objective": row.expected_objective,
                "rules": (row.rules_triggered_json or {}).get("rules", []),
                "reason": row.reason,
            }
        )
    return {"total": len(items), "items": items}


def recommendation_actions_for_customer(db: Session, organization_id: int, customer_id: int) -> dict:
    """Read-only shortcut used by the Customer 360 page."""
    return list_recommendations(db, organization_id, customer_id=customer_id)


def item_priority(row: RetentionRecommendation) -> str:
    score = row.priority_score or 0.0
    if score >= 80:
        return PriorityLevel.HIGH
    if score >= 50:
        return PriorityLevel.MEDIUM
    return PriorityLevel.LOW


def _priority_passing(item_priority: str, min_priority: str) -> bool:
    order = {PriorityLevel.HIGH: 3, PriorityLevel.MEDIUM: 2, PriorityLevel.LOW: 1}
    return order.get(item_priority, 1) >= order.get(min_priority, 1)


def _avg(feature_rows: dict[int, CustomerFeature], name: str) -> float:
    values = [float(getattr(f, name) or 0.0) for f in feature_rows.values()]
    return (sum(values) / len(values)) if values else 0.0


def _top_driver(local_json: dict | None) -> str | None:
    if not local_json:
        return None
    features = local_json.get("features") or {}
    if not features:
        return None
    ranked = sorted(features.items(), key=lambda kv: abs(kv[1].get("contribution") or 0.0), reverse=True)
    return ranked[0][0] if ranked else None