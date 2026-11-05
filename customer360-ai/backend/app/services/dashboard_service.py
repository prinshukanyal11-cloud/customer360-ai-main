"""Dashboard KPIs computed from live database data (facts, never hard-coded).

Returns zero/None when no data exists so the UI can show honest empty states.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer_features_model import CustomerFeature, ModelVersion, Prediction
from app.models.transaction import Customer, Transaction


def _latest_active_model_version(db: Session, org_id: int, model_type: str) -> ModelVersion | None:
    return db.execute(
        select(ModelVersion)
        .where(ModelVersion.organization_id == org_id, ModelVersion.active.is_(True))
        .order_by(ModelVersion.version_no.desc())
        .limit(1)
    ).scalar_one_or_none()


def dashboard_summary(db: Session, organization_id: int) -> dict:
    total_customers = db.execute(
        select(func.count(Customer.id)).where(Customer.organization_id == organization_id)
    ).scalar() or 0

    revenue = float(
        db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.organization_id == organization_id
            )
        ).scalar()
    ) or 0.0

    total_orders = db.execute(
        select(func.count(Transaction.id)).where(Transaction.organization_id == organization_id)
    ).scalar() or 0

    avg_order_value = round(revenue / total_orders, 2) if total_orders else None

    # Repeat purchase rate: customers with > 1 order / customers with >= 1 order
    buyers = db.execute(
        select(Customer.id)
        .join(Transaction, Transaction.customer_id == Customer.id)
        .where(Customer.organization_id == organization_id)
        .distinct()
    ).scalars().all()
    repeaters = sum(
        1
        for cid in buyers
        if (
            db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.customer_id == cid, Transaction.organization_id == organization_id
                )
            ).scalar()
            or 0
        )
        > 1
    )
    repeat_purchase_rate = round(repeaters / len(buyers), 4) if buyers else None

    # Churn rate (fact-based, approximate): active buyers with no order in last 90 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    active_or_churned = db.execute(
        select(func.count(Transaction.id))
        .where(Transaction.organization_id == organization_id)
        .group_by(Transaction.customer_id)
    ).scalars().all()
    recently_active = db.execute(
        select(func.count(Transaction.id))
        .where(
            Transaction.organization_id == organization_id,
            func.date(Transaction.order_date) >= cutoff.date(),
        )
        .group_by(Transaction.customer_id)
    ).scalars().all()
    churn_rate = (
        round(max(len(active_or_churned) - len(recently_active), 0) / len(active_or_churned), 4)
        if active_or_churned
        else None
    )

    # At-risk customers come from the active churn model if present; otherwise None.
    model = _latest_active_model_version(db, organization_id, "churn")
    at_risk = None
    revenue_at_risk = None
    if model is not None:
        high_risk = db.execute(
            select(Prediction)
            .where(
                Prediction.organization_id == organization_id,
                Prediction.model_version_id == model.id,
                Prediction.risk_level.in_(["HIGH", "CRITICAL"]),
            )
            .limit(500)
        ).scalars().all()
        at_risk = len(high_risk)
        risked_feature_values = (
            db.execute(
                select(CustomerFeature.clv_est)
                .where(
                    CustomerFeature.organization_id == organization_id,
                    CustomerFeature.customer_id.in_([p.customer_id for p in high_risk]),
                )
            ).scalars().all()
            if high_risk
            else []
        )
        revenue_at_risk = round(sum(v for v in risked_feature_values if v), 2) if risked_feature_values else None

    # avg_clv = round(
    #     db.execute(
    #         select(func.avg(CustomerFeature.clv_est)).where(CustomerFeature.organization_id == organization_id)
    #     ).scalar(),
    #     2,
    # )
    avg_clv_result = db.execute(
        select(func.avg(CustomerFeature.clv_est)).where(
            CustomerFeature.organization_id == organization_id
        )
    ).scalar()

    avg_clv = round(avg_clv_result, 2) if avg_clv_result is not None else 0.0

    return {
        "total_customers": total_customers,
        "revenue": revenue,
        "total_orders": total_orders,
        "avg_order_value": avg_order_value,
        "repeat_purchase_rate": repeat_purchase_rate,
        "churn_rate": churn_rate,
        "avg_clv": avg_clv,
        "at_risk_customers": at_risk,
        "revenue_at_risk": revenue_at_risk,
        "currency": "INR",
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }