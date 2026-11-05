"""Cohort retention, customer journey, and product affinity unit tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.constants import JourneyStage
from app.core.exceptions import NotFoundError
from app.models.dataset import Dataset, DatasetVersion
from app.models.transaction import Customer
from app.services.engagement_service import (
    _shift_month,
    cohort_retention,
    customer_journey,
    product_recommendations,
)
from app.services.processing_service import process_dataset_core


@pytest.fixture(autouse=True)
def _autouse(model_artifacts) -> None:
    settings.model_artifact_dir = str(model_artifacts)


@pytest.fixture()
def model_artifacts(tmp_path):
    return tmp_path / "model_artifacts"


@pytest.fixture()
def upload_root(tmp_path):
    return tmp_path / "uploads"


def _process_df(db_session, org_id: int, df: pd.DataFrame, upload_root: Path, name: str) -> int:
    upload_root.mkdir(parents=True, exist_ok=True)
    path = upload_root / f"{name}.csv"
    df.to_csv(path, index=False)
    dataset = Dataset(
        organization_id=org_id,
        name=name,
        status="UPLOADED",
        total_rows=len(df),
        columns_json={"columns": df.columns.tolist()},
        created_by=1,
    )
    db_session.add(dataset)
    db_session.flush()
    version = DatasetVersion(
        organization_id=org_id,
        dataset_id=dataset.id,
        version_no=1,
        file_path=str(path),
        checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        row_count=len(df),
        created_by=1,
    )
    db_session.add(version)
    version.column_mapping_json = {
        "entities": {
            "transactions": {
                "external_order_id": "order_id",
                "customer_id": "customer_id",
                "order_date": "order_date",
                "quantity": "quantity",
                "amount": "amount",
                "discount": "discount",
                "channel": "channel",
            },
            "products": {"product_name": "product_name", "category": "category"},
        }
    }
    db_session.commit()
    summary = process_dataset_core(db_session, dataset, org_id)
    return summary["feature_summary"]["buyers"]


def engagement_frame() -> pd.DataFrame:
    """Buyers with known cohorts + co-purchase signatures.

    The ledger stores one product per order (external_order_id is unique), so
    co-purchase affinity is measured across customers who bought both products.

    C1: Widget (03-01), Gadget (04-01), Widget (05-01)  -> first 2024-03
    C2: Widget (03-10), Gadget (03-24), Sprocket (03-30) -> first 2024-03
    C3: Gadget (06-01)                                  -> first 2024-06
    C4: Widget (01-05), Sprocket (02-20), Widget (02-22) -> first 2024-01

    Customer product sets:
      C1 {Widget, Gadget}    C2 {Widget, Gadget, Sprocket}
      C3 {Gadget}            C4 {Widget, Sprocket}
    Widget-Gadget: 2 customers (C1, C2).  Widget-Sprocket: 2 (C2, C4).
    """
    orders = [
        ("O1", "C1", "2024-03-01", "Widget"),
        ("O2", "C1", "2024-04-01", "Gadget"),
        ("O3", "C1", "2024-05-01", "Widget"),
        ("O4", "C2", "2024-03-10", "Widget"),
        ("O5", "C2", "2024-03-24", "Gadget"),
        ("O6", "C2", "2024-03-30", "Sprocket"),
        ("O7", "C3", "2024-06-01", "Gadget"),
        ("O8", "C4", "2024-01-05", "Widget"),
        ("O9", "C4", "2024-02-20", "Sprocket"),
        ("O10", "C4", "2024-02-22", "Widget"),
    ]
    rows = []
    for oid, customer, date, product in orders:
        rows.append(
            {
                "order_id": oid,
                "customer_id": customer,
                "order_date": date,
                "product_name": product,
                "category": "Tools",
                "quantity": 1,
                "amount": "100.00",
                "discount": "0.00",
                "channel": "web",
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture()
def org(db_session, upload_root) -> int:
    buyers = _process_df(db_session, 901, engagement_frame(), upload_root, "orders")
    assert buyers == 4
    return 901


def _products(db_session, org_id: int) -> dict[str, int]:
    from app.models.transaction import Product

    rows = db_session.execute(
        select(Product).where(Product.organization_id == org_id)
    ).scalars().all()
    return {p.product_name: p.id for p in rows}


def _customers(db_session, org_id: int) -> dict[str, int]:
    rows = db_session.execute(
        select(Customer).where(Customer.organization_id == org_id)
    ).scalars().all()
    return {c.external_customer_id: c.id for c in rows}


def test_cohort_matrix(db_session, org) -> None:
    result = cohort_retention(db_session, org, months=12)
    assert result["summary"]["total_customers"] == 4
    assert result["summary"]["repeat_share"] == 0.75  # C1, C2, C4 all have >=2 orders
    cohort_months = [c["cohort"] for c in result["cohorts"]]
    assert cohort_months == ["2024-01", "2024-03", "2024-06"]
    by_cohort = {c["cohort"]: c for c in result["cohorts"]}
    assert by_cohort["2024-01"]["customers"] == 1
    assert by_cohort["2024-01"]["retention"][0] == 1.0
    assert by_cohort["2024-06"]["retention"] == [1.0]  # newest cohort: no complete lags
    assert all(r["retention"][0] == 1.0 for r in result["cohorts"])


def test_cohort_empty(db_session, upload_root) -> None:
    result = cohort_retention(db_session, 999, months=12)
    assert result["cohorts"] == []
    assert result["summary"]["total_customers"] == 0


def test_shift_month() -> None:
    assert _shift_month("2024-01", 1) == "2024-02"
    assert _shift_month("2024-12", 1) == "2025-01"
    assert _shift_month("2024-10", 5) == "2025-03"


def test_journey_stages(db_session, org) -> None:
    result = customer_journey(db_session, org)
    assert result["as_of"] == "2024-06-01"
    stages = {s["stage"]: s for s in result["stages"]}
    # C3 single recent order -> NEW
    assert stages[JourneyStage.NEW]["customers"] >= 1
    # C4 had an early course -> AT_RISK-ish or DORMANT depending on window
    assert sum(s["customers"] for s in result["stages"]) == 4
    milestones = {m["label"]: m for m in result["milestones"]}
    assert milestones["1+ purchases"]["customers"] == 4
    assert milestones["2+ purchases"]["customers"] == 3
    assert result["avg_orders_per_customer"] == 2.5  # 10 orders / 4 customers
    assert result["time_to_repeat"]  # 3 time-to-repeat values present


def test_product_affinity_item_to_item(db_session, org) -> None:
    products = _products(db_session, org)
    widget_id = products["Widget"]

    result = product_recommendations(db_session, org, product_id=widget_id, limit=5)
    assert result["basis"]["type"] == "product"
    assert result["total_baskets"] == 4  # distinct customers with products
    recs = result["recommendations"]
    assert len(recs) == 2
    assert all(r["product_id"] != widget_id for r in recs)
    assert {r["name"] for r in recs} == {"Gadget", "Sprocket"}
    assert all(r["co_purchases"] >= 1 for r in recs)
    assert all(r["confidence"] > 0 and r["lift"] > 0 and r["score"] > 0 for r in recs)
    by_name = {r["name"]: r for r in recs}
    assert by_name["Sprocket"]["co_purchases"] == 2  # C2 + C4 bought Widget & Sprocket
    assert by_name["Gadget"]["co_purchases"] == 2  # C1 + C2 bought Widget & Gadget


def test_product_affinity_customer(db_session, org) -> None:
    products = _products(db_session, org)
    customers = _customers(db_session, org)
    gad = products["Gadget"]
    c1 = customers["C1"]
    c4 = customers["C4"]

    result = product_recommendations(db_session, org, customer_id=c1, limit=5)
    assert result["basis"]["type"] == "customer"
    # C1 already owns Widget & Gadget -> only the Sprocket co-purchase remains
    assert [r["name"] for r in result["recommendations"]] == ["Sprocket"]
    assert len(result["customer_purchased_products"]) == 2

    result = product_recommendations(db_session, org, customer_id=c4, limit=5)
    recs = result["recommendations"]
    assert recs
    assert recs[0]["product_id"] == gad  # recommended via Gadget co-purchase
    owned = {products["Widget"], products["Sprocket"]}
    assert all(r["product_id"] not in owned for r in recs)


def test_product_affinity_global(db_session, org) -> None:
    result = product_recommendations(db_session, org, limit=5)
    assert result["basis"]["type"] == "global"
    assert result["recommendations"]
    top = result["recommendations"][0]
    assert top["product_a"]["name"]
    assert top["product_b"]["name"]
    assert top["co_purchases"] >= 2
    assert top["confidence"] > 0


def test_product_affinity_not_found(db_session, org) -> None:
    with pytest.raises(NotFoundError):
        product_recommendations(db_session, org, product_id=9999)


def test_product_affinity_unknown_customer(db_session, org) -> None:
    with pytest.raises(NotFoundError):
        product_recommendations(db_session, org, customer_id=9999)