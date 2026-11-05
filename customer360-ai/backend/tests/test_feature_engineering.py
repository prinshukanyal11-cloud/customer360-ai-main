"""Feature engineering tests: RFM, behavioral features, CLV, segment persistence."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.core.constants import RfmSegment
from app.models.customer_features_model import CustomerFeature, CustomerSegment, Segment
from app.models.dataset import Dataset, DatasetVersion
from app.models.transaction import Customer
from app.services.feature_engineering import compute_features
from app.services.processing_service import process_dataset_core


@pytest.fixture()
def upload_root(tmp_path):
    return tmp_path / "uploads"


def _make_dataset(
    db_session,
    org_id: int,
    name: str,
    df: pd.DataFrame,
    upload_root: Path,
    mapping: dict | None = None,
) -> tuple[Dataset, DatasetVersion]:
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
    if mapping:
        version.column_mapping_json = {"entities": mapping}
    db_session.commit()
    return dataset, version


TXNS_DF = pd.DataFrame(
    {
        "order_id": ["O1", "O2", "O3", "O4", "O5", "O6"],
        "customer_id": ["C1", "C1", "C2", "C3", "C3", "C3"],
        "order_date": [
            "2024-06-01",
            "2024-07-15",
            "2024-05-20",
            "2024-03-10",
            "2024-06-20",
            "2024-08-05",
        ],
        "product_name": ["Widget", "Gadget", "Widget", "Gadget", "Widget", "Gadget"],
        "category": ["Tools", "Tools", "Tools", "Tools", "Tools", "Tools"],
        "quantity": [2, 1, 1, 2, 1, 3],
        "amount": ["100.00", "250.00", "80.00", "200.00", "60.00", "300.00"],
        "discount": ["0.00", "10.00", "0.00", "0.00", "5.00", "0.00"],
        "channel": ["web", "app", "web", "app", "web", "web"],
    }
)

TXNS_MAPPING = {
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


def _process_txns(db_session, org_id: int, upload_root) -> dict:
    dataset, _version = _make_dataset(db_session, org_id, "orders", TXNS_DF, upload_root, TXNS_MAPPING)
    return process_dataset_core(db_session, dataset, org_id, build_features=True)


def _feature_for(db_session, org_id: int, external_customer_id: str) -> CustomerFeature | None:
    cid = db_session.execute(
        select(Customer.id).where(
            Customer.organization_id == org_id, Customer.external_customer_id == external_customer_id
        )
    ).scalar_one_or_none()
    if cid is None:
        return None
    return db_session.execute(
        select(CustomerFeature).where(
            CustomerFeature.customer_id == cid, CustomerFeature.organization_id == org_id
        )
    ).scalars().first()


def test_process_builds_features_for_buyers(db_session, upload_root) -> None:
    summary = _process_txns(db_session, 77, upload_root)

    fs = summary["feature_summary"]
    assert fs is not None
    assert fs["status"] == "OK"
    assert fs["buyers"] == 3
    assert fs["customers_with_features"] == 3
    assert fs["segments"] >= 1

    c1 = _feature_for(db_session, 77, "C1")
    assert c1 is not None
    assert c1.frequency == 2
    assert round(c1.monetary, 2) == 350.00
    assert c1.recency_days == 21  # reference date is 2024-08-05 (C3's last order)
    assert round(c1.aov, 2) == 175.00
    assert c1.avg_days_between is not None  # 2 orders -> gap computed
    assert c1.category_diversity == 1
    assert c1.discount_usage is not None
    assert c1.engagement_score is not None
    assert c1.clv_est > 0
    assert c1.rfm_score is not None and len(c1.rfm_score) == 3

    c3 = _feature_for(db_session, 77, "C3")
    assert c3 is not None
    assert c3.frequency == 3
    assert c3.recency_days == 0  # most recent order in the org -> best recency
    assert c3.rfm_score[0] == "5"
    assert c3.tenure_days == (pd.Timestamp("2024-08-05") - pd.Timestamp("2024-03-10")).days


def test_segments_persisted(db_session, upload_root) -> None:
    _process_txns(db_session, 78, upload_root)

    segments = db_session.execute(
        select(Segment).where(Segment.organization_id == 78, Segment.type == "RFM")
    ).scalars().all()
    assert len(segments) >= 1

    assignments = db_session.execute(
        select(CustomerSegment)
        .join(Segment, Segment.id == CustomerSegment.segment_id)
        .where(CustomerSegment.organization_id == 78, Segment.type == "RFM")
    ).scalars().all()
    assert len(assignments) == 3  # one per buyer

    for a in assignments:
        assert a.segment_id in {s.id for s in segments}


def test_recompute_is_idempotent(db_session, upload_root) -> None:
    _process_txns(db_session, 79, upload_root)
    before = db_session.execute(
        select(func.count()).select_from(CustomerFeature).where(CustomerFeature.organization_id == 79)
    ).scalar_one()

    summary = compute_features(db_session, 79)
    after = db_session.execute(
        select(func.count()).select_from(CustomerFeature).where(CustomerFeature.organization_id == 79)
    ).scalar_one()

    assert summary["buyers"] == 3
    assert before == after == 3
    assignments = db_session.execute(
        select(func.count())
        .select_from(CustomerSegment)
        .join(Segment, Segment.id == CustomerSegment.segment_id)
        .where(CustomerSegment.organization_id == 79, Segment.type == "RFM")
    ).scalar_one()
    assert assignments == 3


def test_every_buyer_has_a_valid_segment_label(db_session, upload_root) -> None:
    _process_txns(db_session, 80, upload_root)
    labels = {
        s for s in db_session.execute(select(Segment.name)).scalars().all() if s is not None
    }
    valid = set(RfmSegment.__dict__[name] for name in RfmSegment.__dict__ if name.isupper())
    assert labels <= valid


def test_non_buyer_gets_no_feature_row(db_session, upload_root) -> None:
    _process_txns(db_session, 81, upload_root)
    db_session.add(
        Customer(
            organization_id=81, external_customer_id="C99", name="No-Tx", email="c99@x.com"
        )
    )
    db_session.commit()
    summary = compute_features(db_session, 81)
    assert summary["buyers"] == 3
    assert summary["customers_with_features"] == 3
    assert _feature_for(db_session, 81, "C99") is None