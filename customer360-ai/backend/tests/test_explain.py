"""SHAP explanation tests: per-prediction contributions + global importance."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import InsufficientDataError
from app.ml import churn as churn_service
from app.ml import explain as explain_service
from app.models.customer_features_model import Explanation, Model, ModelVersion, Prediction
from app.models.dataset import Dataset, DatasetVersion
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
    buyers = _process_df(db_session, 701, buyer_dataframe(), upload_root, "orders")
    assert buyers >= 16
    return 701


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


@pytest.mark.parametrize("families", [["logistic"], ["random_forest"], ["xgboost"], ["lightgbm"]])
def test_explain_each_family(db_session, buyers_org, families) -> None:
    churn_service.train_churn(db_session, buyers_org, families=families, random_seed=7, created_by=1)
    scoring = churn_service.score_customers(db_session, buyers_org)
    assert scoring["n_scored"] >= 16

    result = explain_service.explain_predictions(db_session, buyers_org)
    assert result["n_explained"] == scoring["n_scored"]
    assert result["family"] == families[0]
    assert result["global_importance"]
    total_share = sum(g["share"] for g in result["global_importance"])
    if total_share == 0.0:
        # degenerate model (e.g., LightGBM collapsing to a constant stump on tiny data)
        assert all(g["importance"] == 0.0 for g in result["global_importance"])
    else:
        assert total_share == pytest.approx(100.0, abs=1.0)

    version = _active_version(db_session, buyers_org)
    rows = db_session.execute(
        select(Explanation).where(
            Explanation.organization_id == buyers_org,
            Explanation.prediction_id.in_(
                select(Prediction.id).where(
                    Prediction.organization_id == buyers_org,
                    Prediction.model_version_id == version.id,
                )
            ),
        )
    ).scalars().all()
    assert len(rows) == scoring["n_scored"]
    assert all(r.method == "SHAP" and r.base_value is not None for r in rows)
    assert all(r.global_importance_json and r.local_contrib_json for r in rows)


def test_explain_contributions_sanity(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=3, created_by=1)
    churn_service.score_customers(db_session, buyers_org)
    result = explain_service.explain_predictions(db_session, buyers_org)

    item = result["items"][0]
    assert item["contributions"]
    total_share = sum(c["share"] for c in item["contributions"])
    assert total_share == pytest.approx(100.0, abs=1.0)
    assert all(c["feature"] for c in item["contributions"])
    # top driver listed first
    assert abs(item["contributions"][0]["contribution"]) >= abs(item["contributions"][-1]["contribution"])


def test_customer_explanation(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["xgboost"], random_seed=5, created_by=1)
    churn_service.score_customers(db_session, buyers_org)
    explain_service.explain_predictions(db_session, buyers_org)

    prediction = db_session.execute(
        select(Prediction).where(Prediction.organization_id == buyers_org)
    ).scalars().first()
    explanation = explain_service.explanation_for_customer(db_session, buyers_org, prediction.customer_id)
    assert explanation["prediction_id"] == prediction.id
    assert explanation["customer_id"] == prediction.customer_id
    assert explanation["contributions"]
    assert explanation["base_value"] is not None


def test_explain_without_model_raises(db_session, buyers_org) -> None:
    with pytest.raises(InsufficientDataError):
        explain_service.explain_predictions(db_session, buyers_org)


def test_explain_without_predictions_raises(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["logistic"], random_seed=9, created_by=1)
    with pytest.raises(InsufficientDataError):
        explain_service.explain_predictions(db_session, buyers_org)


def test_explain_specific_customers(db_session, buyers_org) -> None:
    churn_service.train_churn(db_session, buyers_org, families=["random_forest"], random_seed=11, created_by=1)
    churn_service.score_customers(db_session, buyers_org)
    targets = db_session.execute(
        select(Prediction.customer_id, Prediction.id)
        .where(Prediction.organization_id == buyers_org)
    ).all()[:3]
    result = explain_service.explain_predictions(
        db_session, buyers_org, customer_ids=[t[0] for t in targets]
    )
    assert result["n_explained"] == 3