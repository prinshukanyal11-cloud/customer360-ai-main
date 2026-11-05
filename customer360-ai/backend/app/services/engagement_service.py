"""Cohort retention, lifecycle journey, and product co-purchase analytics.

All functions are read-only over the raw ledger (transactions + products) and
scoped to one organization. They are deliberately stateless (no persistence),
so the analytics UI can recompute them on every load.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import JourneyStage
from app.core.exceptions import NotFoundError
from app.models.transaction import Product, Transaction

MONTH_FMT = "%Y-%m"


def cohort_retention(db: Session, organization_id: int, *, months: int = 12) -> dict:
    """Retention-cohort matrix.

    Buyers are grouped by the month of their first purchase; each cohort lists
    the share of customers who purchased again in each later month (lag 0 is
    the cohort month itself). `months` caps how many lags are reported.
    """
    rows = db.execute(
        select(Transaction.customer_id, Transaction.order_date).where(
            Transaction.organization_id == organization_id
        )
    ).all()

    if not rows:
        return {
            "cohorts": [],
            "months": months,
            "as_of": None,
            "summary": {
                "repeat_share": 0.0,
                "avg_orders_per_customer": 0.0,
                "median_orders_per_customer": 0.0,
                "cohorts_analyzed": 0,
                "total_customers": 0,
            },
        }

    # customer -> set of month strings with activity + transaction row count
    activity: dict[int, set[str]] = defaultdict(set)
    order_counts: Counter[int] = Counter()
    for customer_id, order_date in rows:
        activity[customer_id].add(order_date.strftime(MONTH_FMT))
        order_counts[customer_id] += 1

    first_month: dict[int, str] = {}
    for customer_id, months_active in activity.items():
        first_month[customer_id] = min(months_active)

    cohort_months = sorted(set(first_month.values()))
    lag_months = min(months, len(cohort_months) - 1)
    cohorts = []
    for idx, cohort in enumerate(cohort_months):
        members = [c for c, fm in first_month.items() if fm == cohort]
        size = len(members)
        # Report only completed lags: a cohort at index `idx` can only have
        # observed `len(cohort_months) - 1 - idx` later months so far.
        available_lags = min(lag_months, len(cohort_months) - 1 - idx)
        retention = [1.0]
        if size:
            for lag in range(1, available_lags + 1):
                target = _shift_month(cohort, lag)
                active = sum(1 for c in members if target in activity[c])
                retention.append(round(active / size, 4))
        cohorts.append(
            {
                "cohort": cohort,
                "customers": size,
                "retention": retention,
            }
        )

    as_of = max(d for _, d in rows).isoformat() if rows else None
    return {
        "cohorts": cohorts,
        "months": lag_months,
        "as_of": as_of,
        "summary": {
            "repeat_share": round(sum(1 for c in order_counts.values() if c >= 2) / len(order_counts), 4)
            if order_counts
            else 0.0,
            "avg_orders_per_customer": round(sum(order_counts.values()) / len(order_counts), 2)
            if order_counts
            else 0.0,
            "median_orders_per_customer": float(_median(list(order_counts.values()))) if order_counts else 0.0,
            "cohorts_analyzed": len(cohort_months),
            "total_customers": len(order_counts),
        },
    }


def customer_journey(db: Session, organization_id: int) -> dict:
    """Lifecycle journey: current stage distribution + purchase milestone funnel +
    time-to-second-purchase buckets. Recency is measured against the most recent
    order date in the org (as-of reference), keeping results deterministic."""
    rows = db.execute(
        select(Transaction.customer_id, Transaction.order_date).where(
            Transaction.organization_id == organization_id
        )
    ).all()
    if not rows:
        return {
            "stages": [],
            "milestones": [{"label": "1+ purchases", "customers": 0}],
            "time_to_repeat": [],
            "avg_time_to_repeat_days": None,
            "avg_orders_per_customer": 0.0,
            "as_of": None,
        }

    dates_by_customer: dict[int, set] = defaultdict(set)
    for customer_id, order_date in rows:
        dates_by_customer[customer_id].add(order_date)
    as_of = max(d for _, d in rows)

    stages: Counter[str] = Counter()
    milestones = Counter()
    time_to_repeat_days: list[float] = []
    for _customer_id, dates in dates_by_customer.items():
        ordered = sorted(dates)
        count = len(ordered)
        milestones[count] += 1
        if count >= 2:
            time_to_repeat_days.append((ordered[1] - ordered[0]).days)
        recency = (as_of - ordered[-1]).days
        stages[_stage_for(count, recency)] += 1

    n_customers = len(dates_by_customer)
    milestones_out = []
    for threshold in (1, 2, 3, 5, 10):
        cnt = sum(v for k, v in milestones.items() if k >= threshold)
        milestones_out.append(
            {
                "threshold": threshold,
                "label": f"{threshold}+ purchases",
                "customers": cnt,
                "share": round(cnt / n_customers, 4) if n_customers else 0.0,
            }
        )

    stage_out = []
    for stage in JourneyStage.ALL:
        cnt = stages.get(stage, 0)
        stage_out.append(
            {
                "stage": stage,
                "customers": cnt,
                "share": round(cnt / n_customers, 4) if n_customers else 0.0,
            }
        )

    return {
        "stages": stage_out,
        "milestones": milestones_out,
        "time_to_repeat": _time_to_repeat_buckets(time_to_repeat_days),
        "avg_time_to_repeat_days": round(sum(time_to_repeat_days) / len(time_to_repeat_days), 1)
        if time_to_repeat_days
        else None,
        "median_time_to_repeat_days": _median(time_to_repeat_days) if time_to_repeat_days else None,
        "avg_orders_per_customer": round(sum(k * v for k, v in milestones.items()) / n_customers, 2),
        "as_of": as_of.isoformat(),
    }


def product_recommendations(
    db: Session,
    organization_id: int,
    *,
    product_id: int | None = None,
    customer_id: int | None = None,
    limit: int = 5,
) -> dict:
    """Co-purchase ("customers who bought this also bought") recommendations.

    The ledger stores one product per order (external_order_id is unique), so
    affinity is measured across customers who bought both products: a customer
    who owns {A, B} counts as one co-purchase for the pair (A, B).

    Either `product_id` (item-to-item), `customer_id` (personalized from the
    customer's own products), or neither (global top pairs). Ranked by an
    affinity score blending co-customer count, confidence and lift.
    """
    rows = db.execute(
        select(Transaction.customer_id, Transaction.product_id).where(
            Transaction.organization_id == organization_id,
            Transaction.product_id.isnot(None),
        )
    ).all()
    if not rows:
        return {"basis": _basis(product_id, customer_id), "total_baskets": 0, "recommendations": []}

    # customer-basket: distinct products per customer
    customer_baskets: dict[int, set[int]] = defaultdict(set)
    for cust_id, prod_id in rows:
        if prod_id is not None:
            customer_baskets[cust_id].add(prod_id)

    if not customer_baskets:
        return {"basis": _basis(product_id, customer_id), "total_baskets": 0, "recommendations": []}

    basket_list = list(customer_baskets.values())
    total_baskets = len(basket_list)  # customers with >=1 product
    all_product_ids = {pid for items in basket_list for pid in items}
    product_rows = db.execute(
        select(Product.id, Product.external_product_id, Product.product_name, Product.category).where(
            Product.organization_id == organization_id,
            Product.id.in_(sorted(all_product_ids)),
        )
    ).all()
    meta = {
        row[0]: {
            "id": row[0],
            "external_product_id": row[1],
            "name": row[2],
            "category": row[3],
        }
        for row in product_rows
    }

    item_count: Counter[int] = Counter()
    pair_count: Counter[tuple[int, int]] = Counter()
    item_baskets: dict[int, list[int]] = defaultdict(list)  # product -> basket indexes
    for idx, items in enumerate(basket_list):
        for pid in items:
            item_count[pid] += 1
            item_baskets[pid].append(idx)
        for a, b in _ordered_pairs(items):
            pair_count[(a, b)] += 1

    if product_id is not None:
        if product_id not in item_baskets:
            raise NotFoundError("Product not found in this organization's basket history.")
        candidates = _from_product(product_id, pair_count, item_baskets, total_baskets)
        basis = _basis(product_id, None)
        recommendations = [
            _recommendation_entry(pid, score, co_purchases, confidence, lift, meta)
            for pid, score, co_purchases, confidence, lift in candidates[:limit]
        ]
        return {"basis": basis, "total_baskets": total_baskets, "recommendations": recommendations}

    if customer_id is not None:
        owned = _customer_products(db, organization_id, customer_id)
        if not owned:
            return {
                "basis": _basis(None, customer_id),
                "customer_purchased_products": [],
                "total_baskets": total_baskets,
                "recommendations": [],
            }
        candidates = _personalized(owned, pair_count, item_baskets, total_baskets)
        payload = {
            "basis": _basis(None, customer_id),
            "customer_purchased_products": _products_meta(meta, owned),
            "total_baskets": total_baskets,
            "recommendations": [
                _recommendation_entry(pid, score, co_purchases, confidence, lift, meta)
                for pid, score, co_purchases, confidence, lift in candidates[:limit]
            ],
        }
        return payload

    pairs = _global_pairs(pair_count, item_baskets, total_baskets)
    global_recs = [_global_entry(a, b, co, conf, lift, meta) for a, b, co, conf, lift in pairs[:limit]]
    return {
        "basis": _basis(None, None),
        "total_baskets": total_baskets,
        "recommendations": global_recs,
    }


def _from_product(pid, pair_count, item_baskets, total_baskets) -> list:
    focus_size = len(item_baskets[pid])
    out = []
    for (a, b), count in pair_count.items():
        if a != pid and b != pid:
            continue
        other = b if a == pid else a
        other_size = len(item_baskets.get(other, []))
        confidence = count / focus_size if focus_size else 0.0
        lift = confidence / (other_size / total_baskets) if other_size else 0.0
        score = round(count * 0.6 + confidence * 40.0 + lift, 4)
        out.append((other, score, count, confidence, lift))
    out.sort(key=lambda row: (row[1], row[2]), reverse=True)
    return out


def _personalized(owned, pair_count, item_baskets, total_baskets) -> list:
    owned_set = set(owned)
    detail: dict[int, tuple] = {}
    for (a, b), count in pair_count.items():
        if a in owned_set and b not in owned_set:
            neighbor, source = b, a
        elif b in owned_set and a not in owned_set:
            neighbor, source = a, b
        else:
            continue
        source_size = len(item_baskets.get(source, []))
        neighbor_size = len(item_baskets.get(neighbor, []))
        if not source_size or not neighbor_size:
            continue
        confidence = count / source_size
        lift = confidence / (neighbor_size / total_baskets)
        score = round(count * 0.5 + confidence * 30.0 + lift, 4)
        prior = detail.get(neighbor)
        if prior is None or score > prior[1]:
            detail[neighbor] = (count, score, confidence, lift)
    ranked = sorted(
        ((pid, d[1], d[0], d[2], d[3]) for pid, d in detail.items()),
        key=lambda row: (row[1], row[2]),
        reverse=True,
    )
    return ranked


def _global_pairs(pair_count, item_baskets, total_baskets) -> list:
    """Top undirected pairs, each reported in its highest-affinity orientation."""
    out = []
    for (a, b), count in pair_count.items():
        a_size = len(item_baskets.get(a, []))
        b_size = len(item_baskets.get(b, []))
        if a_size < 2 or b_size < 2:
            continue
        # pick the better orientation as the "anchor -> neighbor" story
        conf_ab = count / a_size
        conf_ba = count / b_size
        lift_ab = conf_ab / (b_size / total_baskets)
        lift_ba = conf_ba / (a_size / total_baskets)
        score_ab = count * 0.6 + conf_ab * 40.0 + lift_ab
        score_ba = count * 0.6 + conf_ba * 40.0 + lift_ba
        if score_ba > score_ab:
            out.append((b, a, count, conf_ba, lift_ba, score_ba))
        else:
            out.append((a, b, count, conf_ab, lift_ab, score_ab))
    out.sort(key=lambda row: (row[3] * 2 + row[5], row[2]), reverse=True)
    return [(a, b, count, confidence, lift) for a, b, count, confidence, lift, _ in out]


def _recommendation_entry(pid, score, co_purchases, confidence, lift, meta) -> dict:
    info = meta.get(pid, {})
    return {
        "product_id": pid,
        "external_product_id": info.get("external_product_id"),
        "name": info.get("name"),
        "category": info.get("category"),
        "co_purchases": co_purchases,
        "confidence": round(confidence, 4),
        "lift": round(float(lift), 4),
        "score": round(float(score), 4),
    }


def _global_entry(a, b, co_purchases, confidence, lift, meta) -> dict:
    return {
        "product_a": _product_meta(a, meta),
        "product_b": _product_meta(b, meta),
        "co_purchases": co_purchases,
        "confidence": round(confidence, 4),
        "lift": round(float(lift), 4),
    }


def _product_meta(pid, meta) -> dict:
    info = meta.get(pid, {})
    return {
        "id": pid,
        "external_product_id": info.get("external_product_id"),
        "name": info.get("name"),
        "category": info.get("category"),
    }


def _products_meta(meta: dict, product_ids) -> list[dict]:
    return [_product_meta(pid, meta) for pid in product_ids if pid in meta]


def _customer_products(db: Session, organization_id: int, customer_id: int) -> set[int]:
    from app.models.transaction import Customer

    customer = db.get(Customer, customer_id)
    if customer is None or customer.organization_id != organization_id:
        raise NotFoundError("Customer not found in this organization.")
    rows = db.execute(
        select(Transaction.product_id).where(
            Transaction.organization_id == organization_id,
            Transaction.customer_id == customer_id,
            Transaction.product_id.isnot(None),
        )
    ).all()
    return {row[0] for row in rows}


def _basis(product_id: int | None, customer_id: int | None) -> dict:
    if product_id is not None:
        return {"type": "product", "product_id": product_id, "customer_id": None}
    if customer_id is not None:
        return {"type": "customer", "product_id": None, "customer_id": customer_id}
    return {"type": "global", "product_id": None, "customer_id": None}


def _ordered_pairs(items: set[int]) -> list[tuple[int, int]]:
    ordered = sorted(items)
    return [(ordered[i], ordered[j]) for i in range(len(ordered)) for j in range(i + 1, len(ordered))]


def _stage_for(count: int, recency_days: int) -> str:
    if count >= 4 and recency_days <= 45:
        return JourneyStage.REGULAR
    if count >= 2 and recency_days <= 45:
        return JourneyStage.EARLY
    if count == 1 and recency_days <= 45:
        return JourneyStage.NEW
    if recency_days <= 120:
        return JourneyStage.AT_RISK
    if recency_days <= 180:
        return JourneyStage.DORMANT
    return JourneyStage.LOST


def _time_to_repeat_buckets(days: list[float]) -> list[dict]:
    edges = [(0, 15), (16, 30), (31, 60), (61, 90), (91, 180), (181, None)]
    counts = Counter()
    for d in days:
        for low, high in edges:
            if (high is None and d >= low) or (d >= low and d <= high):
                counts[(low, high)] += 1
                break
    total = len(days)
    return [
        {
            "range": f"{low}-{high}d" if high else f"{low}d+",
            "days": low,
            "customers": counts.get((low, high), 0),
            "share": round(counts.get((low, high), 0) / total, 4) if total else 0.0,
        }
        for low, high in edges
    ]


def _median(values: list) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2)


def _shift_month(month: str, lag: int) -> str:
    """Shift a YYYY-MM string by `lag` months (positive going forward)."""
    year, m = int(month[:4]), int(month[5:7])
    total = year * 12 + (m - 1) + lag
    new_year, new_month = divmod(total, 12)
    return f"{new_year:04d}-{new_month + 1:02d}"