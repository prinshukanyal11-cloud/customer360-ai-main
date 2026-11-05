"""Customer 360 feature engineering: RFM, behavioral features, CLV estimate.

Given an organization's ledger (customers + transactions + products), this
module computes one feature row per buyer and persists it to `customer_features`
keyed on (organization, customer, dataset_version). RFM quintiles and segment
labels are derived from the buyers' distributions; segments are also persisted
to `segments` / `customer_segments` so downstream analytics and the dashboard
share one source of truth.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.core.constants import RfmSegment
from app.models.customer_features_model import CustomerFeature, CustomerSegment, Segment
from app.models.dataset import DatasetVersion
from app.models.transaction import Customer, Product, Transaction

BATCH_SIZE = 1000


# ---------------------------------------------------------------------------
# RFM scoring
# ---------------------------------------------------------------------------
def rfm_score_from_ranks(r: int, f: int, m_bin: int) -> str:
    return f"{r}{f}{m_bin}"


def rfm_segment(r: int, f: int, m_bin: int) -> str:
    """Map RFM quintiles (1 = worst … 5 = best) to a named segment.

    Rules are mutually exclusive and evaluated in priority order.
    """
    if r >= 4 and f >= 4 and m_bin >= 4:
        return RfmSegment.CHAMPIONS
    if r >= 4 and f >= 3 and m_bin >= 3:
        return RfmSegment.LOYAL
    if r >= 4 and (f <= 2 or m_bin <= 2):
        return RfmSegment.NEW_CUSTOMERS
    if r >= 3 and f >= 3 and m_bin >= 3:
        return RfmSegment.POTENTIAL_LOYALISTS
    if r == 3:
        return RfmSegment.PROMISING
    if r == 2:
        return RfmSegment.AT_RISK
    if r == 1 and f >= 4 and m_bin >= 4:
        return RfmSegment.CANT_LOSE
    if r == 1 and f >= 2 and m_bin >= 2:
        return RfmSegment.HIBERNATING
    return RfmSegment.LOST


rfm_segment.__doc__ = __doc__


def _score_quintiles(series: pd.Series, ascending: bool) -> pd.Series:
    """Turn a metric into 1..5 quintile bins; 5 = best.

    Uses fractional ranking: the best value maps to 5, the worst to 1, with
    the remaining values spread evenly. Deterministic for any sample size and
    duplicate-heavy data (no qcut bin-collapse edge cases).
    """
    valid = series.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=series.index)
    n = len(valid)
    ranks = valid.rank(method="first", ascending=ascending)
    frac = 1.0 - (ranks - 1) / n  # 1.0 = best
    bins = np.ceil(frac * 5).clip(1, 5).astype(int)
    out = pd.Series(np.nan, index=series.index, dtype=float)
    out[valid.index] = bins.astype(float)
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _build_buyer_frame(
    transactions: list[Transaction],
    products_by_id: dict[int, Product],
) -> tuple[pd.DataFrame, dict[int, float]]:
    records = []
    for t in transactions:
        records.append(
            {
                "customer_id": t.customer_id,
                "order_date": t.order_date,
                "amount": float(t.amount),
                "discount": float(t.discount) if t.discount is not None else None,
                "category": products_by_id.get(t.product_id).category if t.product_id else None,
            }
        )
    df = pd.DataFrame(records)
    if df.empty:
        return df, {}

    df["order_date"] = pd.to_datetime(df["order_date"])
    grouped = df.groupby("customer_id")

    monetary = grouped["amount"].sum()
    frequency = grouped["amount"].count()
    aov = grouped["amount"].mean()
    first = grouped["order_date"].min()
    last = grouped["order_date"].max()
    reference: pd.Timestamp = df["order_date"].max()
    recency = (reference - last).dt.days

    def _avg_days_between(rows: pd.DataFrame) -> float | None:
        if len(rows) < 2:
            return None
        diffs = rows["order_date"].diff().dropna().dt.days
        return float(diffs.mean()) if not diffs.empty else None

    avg_gap = pd.Series(
        {cid: _avg_days_between(sub) for cid, sub in grouped}, dtype=object
    )

    category_diversity = (
        df.dropna(subset=["category"]).groupby("customer_id")["category"].nunique()
        if df["category"].notna().any()
        else pd.Series(dtype=int)
    )

    def _discount_usage(rows: pd.DataFrame) -> float | None:
        with_amount = rows[rows["amount"] > 0]
        if with_amount.empty:
            return None
        ratios = (
            pd.to_numeric(with_amount["discount"], errors="coerce").fillna(0.0)
            / with_amount["amount"]
        ).clip(0.0, 1.0)
        return float(ratios.mean())

    discount_usage = pd.Series(
        {cid: _discount_usage(sub) for cid, sub in grouped}, dtype=object
    )

    tenure_days = (last - first).dt.days.clip(lower=0)
    clv = (monetary / (tenure_days / 365.0).replace(0.0, np.nan)).fillna(monetary)

    frame = pd.DataFrame(
        {
            "recency_days": recency,
            "frequency": frequency,
            "monetary": monetary,
            "aov": aov,
            "avg_days_between": avg_gap,
            "tenure_days": tenure_days,
            "category_diversity": category_diversity.reindex(tenure_days.index).fillna(0).astype(int),
            "discount_usage": discount_usage,
            "clv_est": clv,
            "last_order": last,
        }
    ).fillna({"frequency": 0, "monetary": 0.0})

    # Recency: smaller = better -> ascend. Frequency/monetary: bigger = better.
    frame["r"] = _score_quintiles(frame["recency_days"], ascending=True)
    frame["f"] = _score_quintiles(frame["frequency"], ascending=False)
    frame["m"] = _score_quintiles(frame["monetary"], ascending=False)

    def _engagement(row: pd.Series) -> float | None:
        parts = [row.get("r"), row.get("f"), row.get("m")]
        if any(p is None or pd.isna(p) for p in parts):
            return None
        return round(100.0 * ((row["r"] - 1) * 0.33 + (row["f"] - 1) * 0.34 + (row["m"] - 1) * 0.33) / 4.0, 1)

    frame["engagement_score"] = frame.apply(_engagement, axis=1)
    frame["rfm_score"] = [
        rfm_score_from_ranks(int(r), int(f), int(m_bin))
        if all(pd.notna(v) for v in (r, f, m_bin))
        else None
        for r, f, m_bin in zip(frame["r"], frame["f"], frame["m"], strict=True)
    ]
    frame["rfm_segment"] = [
        rfm_segment(int(r), int(f), int(m_bin))
        if all(pd.notna(v) for v in (r, f, m_bin))
        else None
        for r, f, m_bin in zip(frame["r"], frame["f"], frame["m"], strict=True)
    ]
    return frame


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _dialect_insert(db: Session, model):
    if db.get_bind().dialect.name == "postgresql":
        return postgresql.insert(model)
    return sqlite.insert(model)


def _exec_upserts(db: Session, model, rows: list[dict], *, conflict: list[str], update: list[str]) -> None:
    if not rows:
        return
    for start in range(0, len(rows), BATCH_SIZE):
        chunk = rows[start : start + BATCH_SIZE]
        statement = _dialect_insert(db, model).values(chunk)
        if update:
            statement = statement.on_conflict_do_update(
                index_elements=conflict,
                set_={field: getattr(statement.excluded, field) for field in update},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=conflict)
        db.execute(statement)
    db.flush()


def _latest_processed_version(db: Session, organization_id: int) -> DatasetVersion | None:
    return db.execute(
        select(DatasetVersion)
        .where(
            DatasetVersion.organization_id == organization_id,
            DatasetVersion.processed_at.isnot(None),
        )
        .order_by(DatasetVersion.id.desc())
    ).scalars().first()


def _persist_rfm_segments(
    db: Session,
    organization_id: int,
    customer_segment_lookup: dict[int, str | None],
) -> None:
    segment_ids: dict[str, int] = {}
    names = {csegs for csegs in customer_segment_lookup.values() if csegs}
    for name in sorted(names):
        row = db.execute(
            select(Segment).where(
                Segment.organization_id == organization_id,
                Segment.type == "RFM",
                Segment.name == name,
            )
        ).scalar_one_or_none()
        if row is None:
            row = Segment(organization_id=organization_id, type="RFM", name=name, active=True)
            db.add(row)
            db.flush()
        segment_ids[name] = row.id

    if not segment_ids:
        return

    rfm_segment_rows = db.execute(
        select(Segment).where(Segment.organization_id == organization_id, Segment.type == "RFM")
    ).scalars().all()
    seg_org_ids = [s.id for s in rfm_segment_rows]
    assignments = db.execute(
        select(CustomerSegment).where(
            CustomerSegment.organization_id == organization_id,
            CustomerSegment.segment_id.in_(seg_org_ids),
        )
    ).scalars().all()
    for assignment in assignments:
        db.delete(assignment)
    db.flush()

    rows = [
        {
            "organization_id": organization_id,
            "customer_id": customer_id,
            "segment_id": segment_ids[name],
        }
        for customer_id, name in customer_segment_lookup.items()
        if name and name in segment_ids
    ]
    if rows:
        for start in range(0, len(rows), BATCH_SIZE):
            db.execute(insert(CustomerSegment).values(rows[start : start + BATCH_SIZE]))
            db.flush()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def compute_features(
    db: Session,
    organization_id: int,
    *,
    dataset_version_id: int | None = None,
    reference_date: date | None = None,
) -> dict:
    """Compute and persist Customer 360 features for all buyers in an org.

    The RFM reference date defaults to the latest order date in the
    organization so recency scores are meaningful and deterministic.
    Returns a small summary dict (no PII).
    """
    customers = db.execute(
        select(Customer.id).where(Customer.organization_id == organization_id)
    ).scalars().all()
    transactions = db.execute(
        select(Transaction).where(Transaction.organization_id == organization_id)
    ).scalars().all()

    total_customers = len(customers)
    if not transactions:
        return {
            "status": "OK",
            "customers_with_features": 0,
            "buyers": 0,
            "dataset_version_id": None,
            "segments": 0,
            "as_of": None,
        }

    product_ids = {t.product_id for t in transactions if t.product_id is not None}
    products = {}
    if product_ids:
        products = {
            p.id: p
            for p in db.execute(
                select(Product).where(Product.id.in_(product_ids))
            ).scalars()
        }

    frame = _build_buyer_frame(transactions, products)

    version = _latest_processed_version(db, organization_id)
    version_id = version.id if version else dataset_version_id
    as_of = frame["last_order"].max().date() if reference_date is None else reference_date

    feature_rows = []
    lookups: dict[int, str | None] = {}
    for row in frame.itertuples():
        cid = int(row.Index) if isinstance(row.Index, (int, np.integer)) else int(row.customer_id)
        lookups[cid] = row.rfm_segment
        feature_rows.append(
            {
                "organization_id": organization_id,
                "customer_id": cid,
                "dataset_version_id": version_id,
                "recency_days": _n(row.recency_days),
                "frequency": _n(row.frequency),
                "monetary": _n(row.monetary),
                "rfm_score": row.rfm_score,
                "rfm_segment": row.rfm_segment,
                "aov": _n(row.aov),
                "avg_days_between": _n(row.avg_days_between),
                "tenure_days": _n(row.tenure_days),
                "category_diversity": _n(row.category_diversity),
                "discount_usage": _n(row.discount_usage),
                "engagement_score": _n(row.engagement_score),
                "clv_est": round(_n(row.clv_est), 2),
            }
        )

    if version_id is not None:
        _exec_upserts(
            db,
            CustomerFeature,
            feature_rows,
            conflict=["organization_id", "customer_id", "dataset_version_id"],
            update=[
                "recency_days",
                "frequency",
                "monetary",
                "rfm_score",
                "rfm_segment",
                "aov",
                "avg_days_between",
                "tenure_days",
                "category_diversity",
                "discount_usage",
                "engagement_score",
                "clv_est",
            ],
        )
        _persist_rfm_segments(db, organization_id, lookups)
        db.commit()

    return {
        "status": "OK",
        "customers_with_features": len(feature_rows),
        "buyers": len(feature_rows),
        "total_customers": total_customers,
        "dataset_version_id": version_id,
        "segments": len({v for v in lookups.values() if v}) if lookups else 0,
        "as_of": as_of.isoformat(),
    }


def _n(value) -> float | None:
    """Clean a numpy scalar -> python float or None (also handles Decimal/NaN)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (np.generic,)):
        value = value.item()
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f