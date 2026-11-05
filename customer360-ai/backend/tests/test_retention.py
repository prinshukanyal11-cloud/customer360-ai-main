"""Retention recommendation engine + what-if analysis tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.constants import PriorityLevel, RecommendationType
from app.core.exceptions import InsufficientDataError, NotFoundError, ValidationError
from app.ml import churn as churn_service
from app.models.customer_features_model import Model, ModelVersion, RetentionRecommendation
from app.models.dataset import Dataset, DatasetVersion
from app.models.transaction import Customer
from app.services import retention_service, whatif_service
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


def buyer_dataframe() -> pd.DataFrame:
    rows: list[dict] = []
    order_id = 0
    for group in range(3):
        for i in range(12):
            customer = f"C{group}_{i}"
            base_amount = 700.0 - group * 250.0
            high = group == 0
            for month in range(1, 10 if high else 7, 2):
                order_id += 1
                rows.append(
                    {
                        "order_id": f"O{order_id:04d}",
                        "customer_id": customer,
                        "order_date": f"2024-{month:02d}-{10 + i % 8:02d}",
                        "product_name": "Widget" if order_id % 2 else "Gadget",
                        "category": "Tools",
                        "quantity": 1,
                        "amount": f"{base_amount + (i * 7 % 150):.2f}",
                        "discount": "0.00" if group == 0 else f"{5.0 if order_id % 2 else 0.0:.2f}",
                        "channel": "web" if order_id % 3 else "app",
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture()
def buyers_org(db_session, upload_root) -> int:
    buyers = _process_df(db_session, 801, buyer_dataframe(), upload_root, "orders")
    assert buyers >= 16
    return 801


def _active_version(db_session, org_id: int) -> ModelVersion:
    model_id = db_session.execute(
        select(Model.id).where(Model.model_type == "churn", Model.organization_id == org_id)
    ).scalar_one()
    return db_session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model_id,
            ModelVersion.organization_id == org_id,
            ModelVersion.active.is_(True),
        )
    ).scalar_one()


def test_generate_requires_active_model(db_session, buyers_org) -> None:
    with pytest.raises(InsufficientDataError):
        retention_service.generate_recommendations(db_session, buyers_org)


def test_generate_recommendations(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=7, created_by=1)
    churn_service.score_customers(db_session, buyers_org)

    result = retention_service.generate_recommendations(db_session, buyers_org)
    assert result["n_generated"] > 0
    assert result["model_version_id"] is not None
    for item in result["items"]:
        assert item["type"] == RecommendationType.RETENTION
        assert item["action"]
        assert item["reason"]
        assert item["priority"] in PriorityLevel.ALL
        assert item["confidence"] > 0
        assert item["rules"]
        assert item["name"]

    stored = db_session.execute(
        select(RetentionRecommendation).where(RetentionRecommendation.organization_id == buyers_org)
    ).scalars().all()
    assert len(stored) == result["n_generated"]
    assert all(r.type == RecommendationType.RETENTION for r in stored)
    assert all(r.action for r in stored)
    assert all(r.reason for r in stored)


def test_generate_is_idempotent_and_rescoring_replaces(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=7, created_by=1)
    churn_service.score_customers(db_session, buyers_org)
    first = retention_service.generate_recommendations(db_session, buyers_org)
    second = retention_service.generate_recommendations(db_session, buyers_org)
    assert second["n_generated"] == first["n_generated"]

    stored = db_session.execute(
        select(RetentionRecommendation).where(RetentionRecommendation.organization_id == buyers_org)
    ).scalars().all()
    assert len(stored) == first["n_generated"]


def test_list_recommendations_and_filter(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=7, created_by=1)
    churn_service.score_customers(db_session, buyers_org)
    retention_service.generate_recommendations(db_session, buyers_org)

    listed = retention_service.list_recommendations(db_session, buyers_org)
    assert listed["total"] > 0
    priorities = {item["priority"] for item in listed["items"]}
    assert priorities & {PriorityLevel.HIGH, PriorityLevel.MEDIUM, PriorityLevel.LOW}

    high_only = retention_service.list_recommendations(
        db_session, buyers_org, min_priority=PriorityLevel.HIGH
    )
    assert all(item["priority"] == PriorityLevel.HIGH for item in high_only["items"])

    customer_id = listed["items"][0]["customer_id"]
    scoped = retention_service.list_recommendations(db_session, buyers_org, customer_id=customer_id)
    assert scoped["total"] >= 1
    assert all(item["customer_id"] == customer_id for item in scoped["items"])


def test_rules_are_self_consistent() -> None:
    assert len({rule.id for rule in retention_service.RULES}) == len(retention_service.RULES)
    for rule in retention_service.RULES:
        assert 0 < rule.confidence <= 1
        assert 0 < rule.priority_weight <= 1
        assert callable(rule.applies) and callable(rule.reason)


def test_whatif_requires_active_model(db_session, buyers_org) -> None:
    customer_id = db_session.execute(
        select(Customer.id).where(Customer.organization_id == buyers_org)
    ).scalars().first()
    with pytest.raises(InsufficientDataError):
        whatif_service.simulate_whatif(db_session, buyers_org, customer_id, changes={"recency_days": 10})


def test_whatif_flow(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=7, created_by=1)
    churn_service.score_customers(db_session, buyers_org)

    customers = db_session.execute(
        select(Customer.id).where(Customer.organization_id == buyers_org)
    ).scalars().all()
    results = []
    for customer_id in customers[:5]:
        out = whatif_service.simulate_whatif(
            db_session, buyers_org, customer_id, changes={"recency_days": 15}
        )
        results.append(out)
        assert out["baseline"]["probability"] is not None
        assert out["scenario_output"]["probability"] is not None
        assert 0.0 <= out["scenario_output"]["probability"] <= 1.0
        assert isinstance(out["delta"]["probability"], float)
        assert out["changes"]["recency_days"] == 15
        assert out["note"]
    assert len(results) == 5


def test_whatif_scenario_preset(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["random_forest"], random_seed=7, created_by=1)
    churn_service.score_customers(db_session, buyers_org)
    customer_id = db_session.execute(
        select(Customer.id).where(Customer.organization_id == buyers_org)
    ).scalars().first()
    out = whatif_service.simulate_whatif(db_session, buyers_org, customer_id, scenario="re_engage")
    assert out["scenario"] == "re_engage"
    assert out["changes"]["recency_days"] == 15


def test_whatif_validation(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=7, created_by=1)
    churn_service.score_customers(db_session, buyers_org)
    customer_id = db_session.execute(
        select(Customer.id).where(Customer.organization_id == buyers_org)
    ).scalars().first()
    with pytest.raises(ValidationError):
        whatif_service.simulate_whatif(db_session, buyers_org, customer_id, changes={"bogus_feature": 1})
    with pytest.raises(ValidationError):
        whatif_service.simulate_whatif(db_session, buyers_org, customer_id, changes={"recency_days": -5})
    with pytest.raises(ValidationError):
        whatif_service.simulate_whatif(db_session, buyers_org, customer_id, scenario="not_a_scenario")


def test_whatif_unknown_customer(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=7, created_by=1)
    with pytest.raises(NotFoundError):
        whatif_service.simulate_whatif(db_session, buyers_org, 999999, changes={"recency_days": 1})