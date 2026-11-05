"""Processing pipeline tests: mapping, cleaning, idempotent persistence."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.models.dataset import Dataset, DatasetVersion
from app.models.transaction import Customer, Transaction
from app.services.processing_service import ensure_valid_mapping, process_dataset_core
from app.services.schema_registry import infer_mapping


@pytest.fixture()
def upload_root(tmp_path):
    return tmp_path / "uploads"


def _make_dataset(
    db_session,
    org_id: int,
    name: str,
    df: pd.DataFrame,
    upload_root: Path,
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
    version.column_mapping_json = {"entities": infer_mapping(df.columns.tolist())}
    db_session.commit()
    return dataset, version


def test_process_customers(db_session, upload_root) -> None:
    df = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C3"],
            "name": ["Ann", "Ben", "Cal"],
            "email": ["ann@x.com", "b@x.com", "c@x.com"],
            "signup_date": ["2024-01-01", "2024-02-01", "2024-03-01"],
        }
    )
    dataset, _version = _make_dataset(db_session, 77, "customers", df, upload_root)

    summary = process_dataset_core(db_session, dataset, 77)

    assert summary["status"] == "PROCESSED"
    assert summary["inserted"]["customers"] == 3
    count = db_session.execute(
        select(func.count()).select_from(Customer).where(Customer.organization_id == 77)
    ).scalar_one()
    assert count == 3
    assert dataset.status == "PROCESSED"
    assert dataset.total_rows == 3
    assert summary["dq_score"] == 100.0


def test_process_transactions_with_stub_customers_and_products(db_session, upload_root) -> None:
    df = pd.DataFrame(
        {
            "order_id": ["O1", "O2"],
            "customer_id": ["C1", "C2"],
            "order_date": ["2024-06-01", "2024-06-02"],
            "product_name": ["Widget", "Gadget"],
            "category": ["Tools", "Tools"],
            "quantity": [2, 1],
            "amount": ["100.00", "â‚¹250.00"],
            "channel": ["web", "app"],
        }
    )
    dataset, _version = _make_dataset(db_session, 88, "orders", df, upload_root)

    summary = process_dataset_core(db_session, dataset, 88)

    assert summary["inserted"]["transactions"] == 2
    assert summary["inserted"]["products"] == 2
    # Customers referenced but not present -> auto-provisioned stubs.
    stub_count = db_session.execute(
        select(func.count()).select_from(Customer).where(Customer.organization_id == 88)
    ).scalar_one()
    assert stub_count == 2

    txn = db_session.execute(
        select(Transaction).where(Transaction.external_order_id == "O1", Transaction.organization_id == 88)
    ).scalar_one()
    assert txn.amount is not None and float(txn.amount) == 100.00
    assert txn.customer_id is not None
    assert txn.product_id is not None


def test_processing_is_idempotent(db_session, upload_root) -> None:
    df = pd.DataFrame(
        {
            "customer_id": ["C1", "C2"],
            "name": ["Ann", "Ben"],
            "email": ["ann@x.com", "b@x.com"],
            "signup_date": ["2024-01-01", "2024-02-01"],
        }
    )
    dataset, _version = _make_dataset(db_session, 99, "customers", df, upload_root)

    process_dataset_core(db_session, dataset, 99)
    process_dataset_core(db_session, dataset, 99)

    count = db_session.execute(
        select(func.count()).select_from(Customer).where(Customer.organization_id == 99)
    ).scalar_one()
    assert count == 2


def test_processing_updates_existing_row(db_session, upload_root) -> None:
    df = pd.DataFrame({"customer_id": ["C1"], "name": ["Old"]})
    dataset, version = _make_dataset(db_session, 55, "customers", df, upload_root)
    process_dataset_core(db_session, dataset, 55)

    df2 = pd.DataFrame({"customer_id": ["C1"], "name": ["New"]})
    path = upload_root / "customers_v2.csv"
    df2.to_csv(path, index=False)
    version.file_path = str(path)
    version.checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    version.row_count = len(df2)
    db_session.commit()

    summary = process_dataset_core(db_session, dataset, 55)

    assert summary["inserted"]["customers"] == 1
    row = db_session.execute(
        select(Customer).where(Customer.organization_id == 55, Customer.external_customer_id == "C1")
    ).scalar_one()
    assert row.name == "New"


def test_blocking_dq_prevents_processing(db_session, upload_root) -> None:
    df = pd.DataFrame({"customer_id": ["C1"], "name": [None]})
    dataset, _version = _make_dataset(db_session, 44, "customers_bad", df, upload_root)
    # customer_id has a null -> required completeness may still pass at 1 row;
    # force a hard block by using a missing-required mapping instead.
    version = (
        db_session.execute(
            select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
        ).scalar_one()
    )
    version.column_mapping_json = {"entities": {"customers": {"name": "name"}}}
    db_session.commit()

    from app.core.exceptions import ValidationError

    try:
        process_dataset_core(db_session, dataset, 44)
        raised = False
    except ValidationError:
        raised = True
    assert raised
    assert dataset.status == "UPLOADED"


def test_process_customers_with_sparse_optionals(db_session, upload_root) -> None:
    df = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C3"],
            "name": ["Ann", "Ben", "Cal"],
            "email": ["ann@x.com", None, "c@x.com"],
            "signup_date": ["2024-01-01", None, ""],
        }
    )
    dataset, _version = _make_dataset(db_session, 66, "customers_sparse", df, upload_root)

    summary = process_dataset_core(db_session, dataset, 66)

    assert summary["status"] == "PROCESSED"
    assert summary["inserted"]["customers"] == 3
    rows = db_session.execute(
        select(Customer).where(Customer.organization_id == 66).order_by(Customer.id)
    ).scalars().all()
    # Missing optional values must be persisted as NULL, never float NaNs.
    assert len([r for r in rows if r.signup_date is None]) == 2
    assert len([r for r in rows if r.email is None]) == 1


def test_ensure_valid_mapping_rejects_unknowns(db_session) -> None:
    from app.core.exceptions import ValidationError

    try:
        ensure_valid_mapping({"customers": {"not_a_field": "customer_id"}}, ["customer_id"])
        raised = False
    except ValidationError:
        raised = True
    assert raised


def test_ensure_valid_mapping_rejects_duplicate_column(db_session) -> None:
    from app.core.exceptions import ValidationError

    try:
        ensure_valid_mapping(
            {"customers": {"external_customer_id": "id", "name": "id"}},
            ["id"],
        )
        raised = False
    except ValidationError:
        raised = True
    assert raised
