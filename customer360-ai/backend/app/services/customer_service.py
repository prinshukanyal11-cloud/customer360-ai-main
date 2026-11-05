"""Customer list (server-side pagination/filtering) and Customer 360 detail."""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ml.churn import _median_clv, categorize_risk
from app.models.customer_features_model import CustomerFeature, Prediction
from app.models.transaction import Customer, Transaction


def _latest_features(db: Session, organization_id: int, customer_ids: list[int]) -> dict[int, CustomerFeature]:
    if not customer_ids:
        return {}
    latest = (
        select(CustomerFeature.customer_id, func.max(CustomerFeature.id).label("latest_id"))
        .where(
            CustomerFeature.organization_id == organization_id,
            CustomerFeature.customer_id.in_(customer_ids),
        )
        .group_by(CustomerFeature.customer_id)
        .subquery()
    )
    rows = db.execute(
        select(CustomerFeature).join(latest, latest.c.latest_id == CustomerFeature.id)
    ).scalars().all()
    return {row.customer_id: row for row in rows}


def list_customers(
    db: Session,
    organization_id: int,
    *,
    page: int,
    page_size: int,
    search: str | None,
) -> dict:
    stmt = select(Customer).where(Customer.organization_id == organization_id)
    count_stmt = select(func.count(Customer.id)).where(Customer.organization_id == organization_id)

    if search:
        like = f"%{search}%"
        condition = or_(
            Customer.external_customer_id.ilike(like),
            Customer.name.ilike(like),
            Customer.email.ilike(like),
            Customer.location.ilike(like),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = db.execute(count_stmt).scalar() or 0
    rows = db.execute(
        stmt.order_by(Customer.id).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    customer_ids = [c.id for c in rows]
    aggregates = {
        customer_id: (orders, revenue, last_order)
        for customer_id, orders, revenue, last_order in db.execute(
            select(
                Transaction.customer_id,
                func.count(Transaction.id),
                func.coalesce(func.sum(Transaction.amount), 0),
                func.max(Transaction.order_date),
            )
            .where(
                Transaction.organization_id == organization_id,
                Transaction.customer_id.in_(customer_ids),
            )
            .group_by(Transaction.customer_id)
        ).all()
    }
    features = _latest_features(db, organization_id, customer_ids)

    items = []
    for c in rows:
        orders, revenue, last_order = aggregates.get(c.id, (0, 0.0, None))
        feature = features.get(c.id)
        items.append(
            {
                "id": c.id,
                "external_customer_id": c.external_customer_id,
                "name": c.name,
                "email": c.email,
                "location": c.location,
                "signup_date": c.signup_date.isoformat() if c.signup_date else None,
                "total_orders": orders,
                "total_revenue": round(float(revenue), 2),
                "last_purchase": last_order.isoformat() if last_order else None,
                "rfm_score": feature.rfm_score if feature else None,
                "rfm_segment": feature.rfm_segment if feature else None,
                "clv_est": feature.clv_est if feature else None,
            }
        )

    pages = max((total + page_size - 1) // page_size, 1)
    return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": pages}


def get_customer_360(db: Session, organization_id: int, customer_id: int) -> dict | None:
    customer = db.execute(
        select(Customer).where(Customer.id == customer_id, Customer.organization_id == organization_id)
    ).scalar_one_or_none()
    if customer is None:
        return None

    transactions = db.execute(
        select(Transaction)
        .where(Transaction.customer_id == customer.id, Transaction.organization_id == organization_id)
        .order_by(Transaction.order_date.desc())
    ).scalars().all()
    features = _latest_features(db, organization_id, [customer.id]).get(customer.id)
    predictions = db.execute(
        select(Prediction)
        .where(Prediction.customer_id == customer.id, Prediction.organization_id == organization_id)
        .order_by(Prediction.created_at.desc())
    ).scalars().first()

    transactions_out = [
        {
            "id": t.id,
            "external_order_id": t.external_order_id,
            "order_date": t.order_date.isoformat() if t.order_date else None,
            "amount": float(t.amount),
            "discount": float(t.discount) if t.discount is not None else None,
            "channel": t.channel,
        }
        for t in transactions
    ]

    total_spending = sum(float(t.amount) for t in transactions)
    return {
        "customer": {
            "id": customer.id,
            "external_customer_id": customer.external_customer_id,
            "name": customer.name,
            "email": customer.email,
            "location": customer.location,
            "age": customer.age,
            "gender": customer.gender,
            "signup_date": customer.signup_date.isoformat() if customer.signup_date else None,
            "acquisition_channel": customer.acquisition_channel,
        },
        "purchase_metrics": {
            "total_orders": len(transactions),
            "total_spending": round(total_spending, 2),
            "avg_order_value": round(total_spending / len(transactions), 2) if transactions else None,
            "first_purchase": transactions[-1].order_date.isoformat() if transactions else None,
            "last_purchase": transactions[0].order_date.isoformat() if transactions else None,
        },
        "rfm": (
            {
                "recency_days": features.recency_days,
                "frequency": int(features.frequency) if features.frequency is not None else None,
                "monetary": features.monetary,
                "rfm_score": features.rfm_score,
                "rfm_segment": features.rfm_segment,
            }
            if features
            else None
        ),
        "behavioral": (
            {
                "aov": features.aov,
                "tenure_days": features.tenure_days,
                "avg_days_between": features.avg_days_between,
                "category_diversity": features.category_diversity,
                "discount_usage": features.discount_usage,
                "engagement_score": features.engagement_score,
            }
            if features
            else None
        ),
        "value": {
            "clv_est": features.clv_est if features else None,
            "value_at_risk": (
                round(predictions.prediction_proba * features.clv_est, 2)
                if predictions and features and features.clv_est is not None
                else None
            ),
        },
        "risk": (
            {
                "churn_probability": predictions.prediction_proba if predictions else None,
                "risk_level": predictions.risk_level if predictions else None,
                "risk_category": (
                    categorize_risk(
                        predictions.risk_level,
                        features.clv_est if features else None,
                        _median_clv(db, organization_id),
                    )
                    if predictions
                    else None
                ),
            }
            if predictions
            else None
        ),
        "transactions": transactions_out,
    }