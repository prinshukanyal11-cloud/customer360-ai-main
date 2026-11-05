"""Churn model tests: multi-family training, comparison, activation, scoring."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.constants import RiskLevel
from app.core.exceptions import InsufficientDataError
from app.ml import churn as churn_service
from app.models.customer_features_model import Model, ModelVersion, Prediction
from app.models.dataset import Dataset, DatasetVersion
from app.models.transaction import Customer
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
    """36 buyers: 12 recent (Sep), 24 dormant since May -> both churn classes."""
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


def tiny_dataframe() -> pd.DataFrame:
    rows: list[dict] = []
    for i in range(3):
        rows.append(
            {
                "order_id": f"T{i:03d}",
                "customer_id": f"T{i}",
                "order_date": f"2024-0{i + 2}-10",
                "product_name": "Widget",
                "category": "Tools",
                "quantity": 1,
                "amount": "100.00",
                "discount": "0.00",
                "channel": "web",
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture()
def buyers_org(db_session, upload_root) -> int:
    buyers = _process_df(db_session, 601, buyer_dataframe(), upload_root, "orders")
    assert buyers >= 16
    return 601


@pytest.fixture()
def tiny_org(db_session, upload_root) -> int:
    _process_df(db_session, 602, tiny_dataframe(), upload_root, "tiny")
    return 602


def _churn_versions(db_session, org_id: int) -> list[ModelVersion]:
    model_id = db_session.execute(
        select(Model.id).where(Model.model_type == "churn", Model.organization_id == org_id)
    ).scalar_one()
    return db_session.execute(
        select(ModelVersion)
        .where(ModelVersion.model_id == model_id, ModelVersion.organization_id == org_id)
        .order_by(ModelVersion.version_no)
    ).scalars().all()


def _active_versions(db_session, org_id: int) -> list[ModelVersion]:
    model_id = db_session.execute(
        select(Model.id).where(Model.model_type == "churn", Model.organization_id == org_id)
    ).scalar_one()
    return db_session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model_id,
            ModelVersion.organization_id == org_id,
            ModelVersion.active.is_(True),
        )
    ).scalars().all()


def test_train_registers_families_and_activates_champion(db_session, buyers_org) -> None:
    result = churn_service.train_churn(
        db_session, buyers_org, families=["logistic", "random_forest"], random_seed=7, created_by=1
    )

    assert result["status"] == "TRAINED"
    assert result["n_samples"] >= 16
    assert result["window_days"] == settings.churn_window_days
    assert len(result["families"]) == 2
    assert {f["family"] for f in result["families"]} == {"logistic", "random_forest"}

    champion = result["champion"]
    assert champion["family"] in {"logistic", "random_forest"}
    assert 0.0 < champion["metrics"]["roc_auc"] <= 1.0
    assert champion["metrics"]["confusion_matrix"]["tp"] >= 0

    versions = _churn_versions(db_session, buyers_org)
    assert len(versions) == 2
    assert [v.version_no for v in versions] == [1, 2]
    active = _active_versions(db_session, buyers_org)
    assert len(active) == 1
    assert active[0].id == champion["model_version_id"]
    assert active[0].metrics_json["roc_auc"] == champion["metrics"]["roc_auc"]

    artifact = (
        Path(settings.model_artifact_dir)
        / str(buyers_org)
        / str(result["model_id"])
        / f"v{champion['version_no']}"
        / "model.joblib"
    )
    assert artifact.exists()


def test_default_families_and_retrain_increments_versions(db_session, buyers_org) -> None:
    first = churn_service.train_churn(db_session, buyers_org, random_seed=3, created_by=1)
    churn_service.train_churn(db_session, buyers_org, random_seed=3, created_by=1)

    assert len(first["families"]) == 4
    versions = _churn_versions(db_session, buyers_org)
    assert [v.version_no for v in versions] == list(range(1, 9))
    active = _active_versions(db_session, buyers_org)
    assert len(active) == 1
    assert active[0].version_no in range(5, 9)


def test_insufficient_buyers_raises(db_session, tiny_org) -> None:
    with pytest.raises(InsufficientDataError):
        churn_service.train_churn(db_session, tiny_org, families=["logistic"])


def test_score_writes_predictions_for_all_buyers(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=11, created_by=1)

    result = churn_service.score_customers(db_session, buyers_org)
    assert result["n_scored"] >= 16
    assert len(result["items"]) == result["n_scored"]
    assert all(item["risk_level"] in RiskLevel.ALL for item in result["items"])

    model_id = db_session.execute(
        select(Model.id).where(Model.model_type == "churn", Model.organization_id == buyers_org)
    ).scalar_one()
    version = db_session.execute(
        select(ModelVersion).where(
            ModelVersion.model_id == model_id,
            ModelVersion.organization_id == buyers_org,
            ModelVersion.active.is_(True),
        )
    ).scalar_one()

    predictions = db_session.execute(
        select(Prediction).where(
            Prediction.organization_id == buyers_org,
            Prediction.model_version_id == version.id,
        )
    ).scalars().all()
    assert len(predictions) == result["n_scored"]
    assert all(p.prediction_proba >= 0.0 and p.prediction_proba <= 1.0 for p in predictions)
    assert all(p.risk_level for p in predictions)


def test_score_specific_customers_and_at_risk(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=5, created_by=1)

    buyers = db_session.execute(
        select(Customer.id).where(Customer.organization_id == buyers_org)
    ).scalars().all()
    subset = buyers[:5]
    result = churn_service.score_customers(db_session, buyers_org, customer_ids=subset)
    assert result["n_scored"] == 5
    assert {item["customer_id"] for item in result["items"]} == set(subset)

    at_risk = churn_service.at_risk_customers(db_session, buyers_org)
    assert at_risk["total"] >= 0
    assert all(item["risk_level"] in ("HIGH", "CRITICAL") for item in at_risk["items"])


def test_score_without_model_raises(db_session, buyers_org) -> None:
    with pytest.raises(InsufficientDataError):
        churn_service.score_customers(db_session, buyers_org)