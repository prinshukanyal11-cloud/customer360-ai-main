"""Read-only analytics over the persisted Customer 360 feature store.

All metrics derive from `customer_features` (plus the raw ledger for revenue
trends) so they stay consistent with the feature store used by the dashboard
and ML pipelines.
"""
from __future__ import annotations

from collections import Counter

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer_features_model import CustomerFeature
from app.models.transaction import Customer, Transaction


def _latest_features(db: Session, organization_id: int):
    """Yield the most-recent CustomerFeature row per customer for an org."""
    version_subq = (
        select(CustomerFeature.customer_id, func.max(CustomerFeature.id).label("latest_id"))
        .where(CustomerFeature.organization_id == organization_id)
        .group_by(CustomerFeature.customer_id)
        .subquery()
    )
    return (
        db.execute(
            select(CustomerFeature)
            .join(version_subq, version_subq.c.latest_id == CustomerFeature.id)
        )
        .scalars()
        .all()
    )


def features_summary(db: Session, organization_id: int) -> dict:
    total_customers = db.execute(
        select(func.count(Customer.id)).where(Customer.organization_id == organization_id)
    ).scalar_one()
    features = _latest_features(db, organization_id)
    buyers = [f for f in features if (f.frequency or 0) > 0]
    versions = {f.dataset_version_id for f in features}
    as_ofs = {f.updated_at for f in features if f.updated_at}
    as_of = max(as_ofs).date().isoformat() if as_ofs else None
    return {
        "customers_with_features": len(features),
        "buyers": len(buyers),
        "total_customers": total_customers,
        "coverage": round(len(features) / total_customers, 4) if total_customers else 0.0,
        "dataset_version_ids": sorted(versions, reverse=True)[:5],
        "as_of": as_of,
    }


def rfm_distribution(db: Session, organization_id: int) -> dict:
    features = [f for f in _latest_features(db, organization_id) if f.rfm_segment]
    counts = Counter(f.rfm_segment for f in features)
    total = sum(counts.values()) or 0
    items = sorted(
        (
            {
                "segment": segment,
                "customer_count": count,
                "share": round(count / total, 4) if total else 0.0,
            }
            for segment, count in counts.items()
        ),
        key=lambda row: row["customer_count"],
        reverse=True,
    )
    return {"total_customers": total, "segments": items}


def clv_distribution(db: Session, organization_id: int) -> dict:
    values = [
        float(f.clv_est)
        for f in _latest_features(db, organization_id)
        if f.clv_est is not None and f.clv_est > 0
    ]
    if not values:
        return {
            "avg_clv": 0.0,
            "median_clv": 0.0,
            "total_clv": 0.0,
            "customers": 0,
            "percentiles": {"p10": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p90": 0.0},
            "buckets": [],
        }
    series = pd.Series(values)
    percentiles = {f"p{int(q * 100)}": round(float(series.quantile(q)), 2) for q in (0.10, 0.25, 0.50, 0.75, 0.90)}
    step = max(float(series.max()), 1.0) / 10
    edges = [round(float(step * i), 2) for i in range(11)]
    buckets = []
    for i in range(10):
        low = edges[i]
        high = edges[i + 1]
        mask = ((series >= low) & (series <= high)) if i == 9 else ((series >= low) & (series < high))
        count = int(mask.sum())
        buckets.append({"label": f"{_fmt_money(low)} - {_fmt_money(high)}", "count": count})
    return {
        "avg_clv": round(float(series.mean()), 2),
        "median_clv": round(float(series.median()), 2),
        "total_clv": round(float(series.sum()), 2),
        "customers": len(values),
        "percentiles": percentiles,
        "buckets": buckets,
    }


def revenue_trends(db: Session, organization_id: int, *, months: int = 12) -> dict:
    rows = db.execute(
        select(Transaction.order_date, Transaction.amount, Transaction.customer_id).where(
            Transaction.organization_id == organization_id, Transaction.order_date.isnot(None)
        )
    ).all()
    if not rows:
        return {"months": 12, "items": []}
    df = pd.DataFrame(
        {
            "month": pd.Series(pd.to_datetime([r[0] for r in rows])).dt.to_period("M").astype(str),
            "amount": pd.to_numeric([r[1] for r in rows]),
            "customer_id": [r[2] for r in rows],
        }
    )
    grouped = df.groupby("month")
    items = []
    for month, group in grouped:
        items.append(
            {
                "month": str(month),
                "orders": int(len(group)),
                "revenue": round(float(group["amount"].sum()), 2),
                "active_customers": int(group["customer_id"].nunique()),
            }
        )
    items.sort(key=lambda row: row["month"])
    return {"months": months, "items": items[-months:]}


def _fmt_money(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"