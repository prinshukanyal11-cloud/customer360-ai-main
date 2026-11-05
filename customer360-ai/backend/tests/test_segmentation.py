"""Segmentation tests: K-Means training, artifact persistence, analytics."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.exceptions import InsufficientDataError
from app.ml import segmentation as segmentation_service
from app.models.customer_features_model import CustomerSegment, ModelVersion, Segment
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


@pytest.fixture()
def buyers_org(db_session, upload_root) -> int:
    df = buyer_dataframe()
    upload_root.mkdir(parents=True, exist_ok=True)
    path = upload_root / "orders.csv"
    df.to_csv(path, index=False)
    dataset = Dataset(
        organization_id=501,
        name="orders",
        status="UPLOADED",
        total_rows=len(df),
        columns_json={"columns": df.columns.tolist()},
        created_by=1,
    )
    db_session.add(dataset)
    db_session.flush()
    version = DatasetVersion(
        organization_id=501,
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

    summary = process_dataset_core(db_session, dataset, 501)
    assert summary["feature_summary"]["buyers"] >= 24
    return 501


def buyer_dataframe() -> pd.DataFrame:
    """36 buyers across three behavioral groups (high/mid/low+lapsed)."""
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


def model_artifacts_dir() -> Path:
    return Path(settings.model_artifact_dir)


def _ml_segments(db_session, org_id: int) -> list[Segment]:
    return db_session.execute(
        select(Segment).where(Segment.organization_id == org_id, Segment.type == "ML").order_by(Segment.id)
    ).scalars().all()


def _buyer_count(db_session, org_id: int) -> int:
    return (
        db_session.execute(
            select(func.count()).select_from(Customer).where(Customer.organization_id == org_id)
        ).scalar_one()
    )


def _ml_assignment_count(db_session, org_id: int) -> int:
    return db_session.execute(
        select(func.count())
        .select_from(CustomerSegment)
        .join(Segment, Segment.id == CustomerSegment.segment_id)
        .where(CustomerSegment.organization_id == org_id, Segment.type == "ML")
    ).scalar_one()


def _assert_members_sum(members: list[int], expected: int) -> None:
    assert sum(members) == expected


def test_train_segmentation_persists_everything(db_session, buyers_org) -> None:
    summary = segmentation_service.train_segmentation(
        db_session, buyers_org, n_clusters=3, created_by=1
    )

    assert summary["status"] == "TRAINED"
    assert summary["n_clusters"] == 3
    assert summary["silhouette_score"] is not None and -1 <= summary["silhouette_score"] <= 1
    assert summary["version_no"] == 1
    assert len(summary["segments"]) == 3
    assert all(s["profile"] for s in summary["segments"])

    version = db_session.execute(
        select(ModelVersion).where(
            ModelVersion.id == summary["model_version_id"],
            ModelVersion.organization_id == buyers_org,
        )
    ).scalar_one()
    assert version.features_list
    assert version.params_json["n_clusters"] == 3
    assert version.metrics_json["n_samples"] == summary["n_samples"]
    _assert_members_sum(
        [int(m) for m in version.metrics_json["members_per_cluster"]], summary["n_samples"]
    )
    assert version.active is True

    segments = _ml_segments(db_session, buyers_org)
    assert len(segments) == 3
    assert all(s.rules_json and s.color for s in segments)

    assignments = _ml_assignment_count(db_session, buyers_org)
    assert assignments == _buyer_count(db_session, buyers_org)

    expected_artifact = (
        model_artifacts_dir()
        / str(buyers_org)
        / str(summary["model_id"])
        / f"v{summary['version_no']}"
        / "model.joblib"
    )
    assert expected_artifact.exists()


def test_auto_select_k(db_session, buyers_org) -> None:
    summary = segmentation_service.train_segmentation(db_session, buyers_org, n_clusters=None)
    assert 2 <= summary["n_clusters"] <= 8
    assert summary["auto_selected"] is True
    assert len(_ml_segments(db_session, buyers_org)) == summary["n_clusters"]


def test_retrain_replaces_assignments(db_session, buyers_org) -> None:
    first = segmentation_service.train_segmentation(db_session, buyers_org, n_clusters=3)
    second = segmentation_service.train_segmentation(db_session, buyers_org, n_clusters=4)

    assert first["version_no"] == 1
    assert second["version_no"] == 2
    assert second["model_version_id"] != first["model_version_id"]

    versions = db_session.execute(
        select(ModelVersion).where(ModelVersion.organization_id == buyers_org).order_by(
            ModelVersion.version_no
        )
    ).scalars().all()
    assert [v.version_no for v in versions] == [1, 2]
    active = [v for v in versions if v.active]
    assert len(active) == 1 and active[0].version_no == 2

    assignments = _ml_assignment_count(db_session, buyers_org)
    assert assignments == _buyer_count(db_session, buyers_org)
    assert len(_ml_segments(db_session, buyers_org)) == 4


def test_insufficient_buyers_raises(db_session, upload_root, model_artifacts) -> None:
    df = pd.DataFrame(
        {
            "order_id": ["O1", "O2"],
            "customer_id": ["C1", "C2"],
            "order_date": ["2024-06-01", "2024-06-02"],
            "amount": ["100.00", "250.00"],
            "channel": ["web", "app"],
        }
    )
    upload_root.mkdir(parents=True, exist_ok=True)
    path = upload_root / "tiny.csv"
    df.to_csv(path, index=False)
    dataset = Dataset(
        organization_id=502,
        name="tiny",
        status="UPLOADED",
        total_rows=len(df),
        columns_json={"columns": df.columns.tolist()},
        created_by=1,
    )
    db_session.add(dataset)
    db_session.flush()
    version = DatasetVersion(
        organization_id=502,
        dataset_id=dataset.id,
        version_no=1,
        file_path=str(path),
        checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        row_count=len(df),
        created_by=1,
    )
    db_session.add(version)
    version.column_mapping_json = {
        "entities": {"transactions": {"external_order_id": "order_id", "customer_id": "customer_id", "order_date": "order_date", "amount": "amount"}}
    }
    db_session.commit()

    summary = process_dataset_core(db_session, dataset, 502)
    assert summary["feature_summary"]["buyers"] == 2

    try:
        segmentation_service.train_segmentation(db_session, 502)
        raised = False
    except InsufficientDataError:
        raised = True
    assert raised


def test_segment_analytics_shape(db_session, buyers_org) -> None:
    segmentation_service.train_segmentation(db_session, buyers_org, n_clusters=3)
    segments = _ml_segments(db_session, buyers_org)

    rows = _nonempty_segments(segments)
    assert rows

    analytics = segmentation_service.segment_analytics(db_session, buyers_org, rows[0].id)
    assert analytics is not None
    assert analytics["member_count"] > 0
    assert analytics["stats"]
    metrics = {row["metric"] for row in analytics["stats"]}
    assert {"frequency", "monetary", "clv_est"}.issubset(metrics)
    assert analytics["channels"]
    assert analytics["top_members"]
    assert 0 <= analytics["repeat_purchase_share"] <= 1


def _nonempty_segments(segments):
    return [s for s in segments if s.rules_json]