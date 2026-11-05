"""Report export tests: catalogue, tenant scoping, formats, caps, and filters."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.exceptions import ValidationError
from app.db.base import Base
from app.models.campaign import Campaign, CampaignResult
from app.models.customer_features_model import CustomerFeature, Model, ModelVersion, Prediction
from app.models.transaction import Customer
from app.models.user import Organization
from app.services import reports_service as rs


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed(db, org_id: int = 1, *, customers: int = 5) -> None:
    db.add(Organization(id=org_id, name=f"Org{org_id}", slug=f"org{org_id}"))
    # `at_risk` reports read through the active churn version.
    db.add(Model(id=org_id, organization_id=org_id, name=f"Churn {org_id}", model_type="churn"))
    db.add(
        ModelVersion(
            id=org_id,
            organization_id=org_id,
            model_id=org_id,
            version_no=1,
            artifact_path="artifacts/v1/model.joblib",
            preprocessing_path="artifacts/v1",
            active=True,
        )
    )
    for index in range(1, customers + 1):
        customer_id = 100 * org_id + index
        db.add(
            Customer(
                id=customer_id,
                organization_id=org_id,
                external_customer_id=f"ext-{index}",
                name=f"Customer {index}",
                email=f"c{index}@x.com",
                location="Berlin",
            )
        )
        db.add(
            CustomerFeature(
                organization_id=org_id,
                customer_id=customer_id,
                dataset_version_id=org_id,
                recency_days=float(index),
                frequency=float(index),
                monetary=float(index * 10),
                rfm_score="R3",
                rfm_segment="LOYAL",
                aov=50.0,
                avg_days_between=7.0,
                tenure_days=90.0,
                category_diversity=2,
                discount_usage=0.1,
                engagement_score=60.0,
                clv_est=float(index * 20),
            )
        )
        db.add(
            Prediction(
                organization_id=org_id,
                model_version_id=org_id,
                customer_id=customer_id,
                prediction_proba=0.8,
                prediction_label=True,
                risk_level="HIGH",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
    db.add(
        Campaign(
            id=org_id,
            organization_id=org_id,
            name=f"Campaign {org_id}",
            objective="retain",
            channel="email",
            status="COMPLETED",
        )
    )
    db.add(
        CampaignResult(
            id=org_id,
            organization_id=org_id,
            campaign_id=org_id,
            response_rate=0.12,
            conversion_rate=0.04,
            revenue=1000.0,
            cost=100.0,
            roi=9.0,
            calculated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    db.commit()


def _csv_rows(result: rs.ExportResult) -> list[dict]:
    text = result.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


# ------------------------------------------------------------------- catalogue


def test_catalog_lists_every_report_and_format(db_session):
    _seed(db_session)
    catalogue = rs.catalog()

    keys = {item["key"] for item in catalogue["items"]}
    assert keys == set(rs._REPORTS)
    assert {"customers", "at_risk", "features", "predictions", "campaigns"} <= keys
    assert catalogue["formats"] == ["csv", "xlsx", "json"]
    assert catalogue["max_rows"] == rs.settings.report_max_rows
    for item in catalogue["items"]:
        assert item["label"] and item["description"]
        assert item["formats"] == catalogue["formats"]


def test_catalog_reports_pdf_as_unavailable(db_session):
    # Roadmap tiers PDF as a later phase; it must not be advertised as usable.
    assert rs.catalog()["pdf_status"] == "not_available"
    assert "pdf" not in rs.available_formats()


# ------------------------------------------------------------------- validation


def test_unknown_report_is_rejected(db_session):
    with pytest.raises(ValidationError, match="Unknown report"):
        rs.export_report(db_session, 1, "nope", "csv")


def test_unknown_format_is_rejected(db_session):
    _seed(db_session)
    with pytest.raises(ValidationError, match="Unsupported format"):
        rs.export_report(db_session, 1, "customers", "parquet")


def test_report_and_format_are_case_insensitive(db_session):
    _seed(db_session)
    assert rs.export_report(db_session, 1, "CUSTOMERS", "CSV").row_count == 5


def test_unknown_filter_is_rejected_with_guidance(db_session):
    _seed(db_session)
    with pytest.raises(ValidationError, match="Unsupported filter"):
        rs.export_report(db_session, 1, "customers", "csv", filters={"colour": "red"})


def test_invalid_enum_filter_value_is_rejected(db_session):
    _seed(db_session)
    with pytest.raises(ValidationError, match="min_risk_level"):
        rs.export_report(db_session, 1, "at_risk", "csv", filters={"min_risk_level": "APOCALYPTIC"})
    with pytest.raises(ValidationError, match="status"):
        rs.export_report(db_session, 1, "campaigns", "csv", filters={"status": "NOPE"})
    with pytest.raises(ValidationError, match="channel"):
        rs.export_report(db_session, 1, "campaigns", "csv", filters={"channel": "carrier-pigeon"})


# ------------------------------------------------------------------- csv


def test_csv_export_has_header_and_rows(db_session):
    _seed(db_session)
    result = rs.export_report(db_session, 1, "customers", "csv")

    rows = _csv_rows(result)
    assert result.row_count == 5
    assert len(rows) == 5
    assert rows[0]["external_customer_id"] == "ext-1"
    assert result.media_type.startswith("text/csv")
    assert result.filename.startswith("customers_org1_")
    assert result.filename.endswith(".csv")
    assert result.truncated is False


def test_csv_uses_utf8_bom_and_writes_empty_for_nulls(db_session):
    _seed(db_session)
    result = rs.export_report(db_session, 1, "customers", "csv")
    assert result.content.startswith(b"\xef\xbb\xbf")
    rows = _csv_rows(result)
    assert rows[0]["signup_date"] == ""


# ------------------------------------------------------------------- json


def test_json_export_carries_metadata_and_rows(db_session):
    _seed(db_session)
    result = rs.export_report(db_session, 1, "features", "json")

    payload = json.loads(result.content.decode("utf-8"))
    assert payload["report"] == "features"
    assert payload["organization_id"] == 1
    assert payload["row_count"] == 5
    assert payload["truncated"] is False
    assert payload["columns"][0] == "customer_id"
    assert payload["rows"][0]["rfm_segment"] == "LOYAL"
    assert result.media_type == "application/json"


# ------------------------------------------------------------------- xlsx


def test_xlsx_export_is_a_real_workbook(db_session):
    from openpyxl import load_workbook

    _seed(db_session)
    result = rs.export_report(db_session, 1, "customers", "xlsx")

    assert zipfile.is_zipfile(io.BytesIO(result.content))
    assert result.media_type.endswith("spreadsheetml.sheet")
    workbook = load_workbook(io.BytesIO(result.content))
    sheet = workbook.active
    assert sheet.title == "Report"
    assert sheet.freeze_panes is not None
    assert sheet.auto_filter.ref is not None

    rows = list(sheet.iter_rows(values_only=True))
    header_index = next(i for i, row in enumerate(rows) if row and row[0] == "id")
    header = [cell for cell in rows[header_index] if cell is not None]
    assert header[:4] == ["id", "external_customer_id", "name", "email"]
    assert rows[header_index + 1][1] == "ext-1"


def test_xlsx_of_empty_report_still_opens(db_session):
    from openpyxl import load_workbook

    db_session.add(Organization(id=1, name="Empty", slug="empty"))
    db_session.commit()
    result = rs.export_report(db_session, 1, "customers", "xlsx")

    assert result.row_count == 0
    workbook = load_workbook(io.BytesIO(result.content))
    assert workbook.active.max_row >= 1


# ------------------------------------------------------------------- tenant scoping


@pytest.mark.parametrize(
    ("report", "expected_rows", "other_org_marker"),
    [
        ("customers", 5, "ext-6"),
        ("customer_directory", 5, "ext-6"),
        ("features", 5, '"customer_id": 201'),
        ("predictions", 5, '"customer_id": 201'),
        ("campaigns", 1, '"id": 2'),
        ("campaign_results", 1, '"campaign_name": "Campaign 2"'),
    ],
)
def test_every_report_stays_inside_one_tenant(db_session, report, expected_rows, other_org_marker):
    _seed(db_session, org_id=1)
    _seed(db_session, org_id=2)

    result = rs.export_report(db_session, 1, report, "json")
    payload = json.loads(result.content.decode("utf-8"))
    serialised = result.content.decode("utf-8")

    assert payload["organization_id"] == 1
    assert payload["row_count"] == expected_rows
    # Org 2 owns customers 201-205 and campaign 2; none may appear in org 1's file.
    assert other_org_marker not in serialised
    for foreign_id in (201, 202, 203, 204, 205):
        assert f": {foreign_id}," not in serialised


def test_at_risk_export_excludes_other_org_customers(db_session):
    _seed(db_session, org_id=1)
    _seed(db_session, org_id=2)
    result = rs.export_report(db_session, 1, "at_risk", "json")
    payload = json.loads(result.content.decode("utf-8"))
    customer_ids = {row["customer_id"] for row in payload["rows"]}
    assert customer_ids == {101, 102, 103, 104, 105}


# ------------------------------------------------------------------- row caps


def test_row_cap_truncates_and_reports_it(db_session, monkeypatch):
    _seed(db_session, customers=50)
    monkeypatch.setattr(rs.settings, "report_max_rows", 10)

    result = rs.export_report(db_session, 1, "customer_directory", "csv")
    assert result.row_count == 10
    assert result.truncated is True
    assert result.meta["truncated"] is True
    assert result.meta["max_rows"] == 10
    assert len(_csv_rows(result)) == 10


def test_row_cap_absent_when_under_limit(db_session, monkeypatch):
    _seed(db_session, customers=3)
    monkeypatch.setattr(rs.settings, "report_max_rows", 10)
    assert rs.export_report(db_session, 1, "customer_directory", "csv").truncated is False


def test_at_risk_limit_filter_cannot_exceed_the_global_cap(db_session, monkeypatch):
    _seed(db_session, customers=20)
    monkeypatch.setattr(rs.settings, "report_max_rows", 5)
    result = rs.export_report(db_session, 1, "at_risk", "csv", filters={"limit": 1000})
    assert result.row_count <= 5


# ------------------------------------------------------------------- filters


def test_campaign_status_filter_narrows_rows(db_session):
    _seed(db_session)
    campaign = db_session.get(Campaign, 1)
    campaign.status = "DRAFT"
    db_session.commit()

    # Matching (case-insensitively) returns the row; a different status returns none.
    assert rs.export_report(db_session, 1, "campaigns", "json", filters={"status": "draft"}).row_count == 1
    assert rs.export_report(db_session, 1, "campaigns", "json", filters={"status": "DRAFT"}).row_count == 1
    assert rs.export_report(db_session, 1, "campaigns", "json", filters={"status": "COMPLETED"}).row_count == 0


def test_customer_search_filter(db_session):
    _seed(db_session)
    result = rs.export_report(db_session, 1, "customers", "csv", filters={"search": "ext-3"})
    rows = _csv_rows(result)
    assert len(rows) == 1
    assert rows[0]["external_customer_id"] == "ext-3"


def test_prediction_version_filter(db_session):
    _seed(db_session)
    assert rs.export_report(db_session, 1, "predictions", "csv", filters={"model_version_id": 1}).row_count == 5
    assert rs.export_report(db_session, 1, "predictions", "csv", filters={"model_version_id": 999}).row_count == 0


def test_campaign_results_filter_by_campaign(db_session):
    _seed(db_session)
    assert rs.export_report(db_session, 1, "campaign_results", "csv", filters={"campaign_id": 1}).row_count == 1
    assert rs.export_report(db_session, 1, "campaign_results", "csv", filters={"campaign_id": 999}).row_count == 0


# ------------------------------------------------------------------- coverage


def test_campaigns_report_includes_latest_results(db_session):
    _seed(db_session)
    rows = _csv_rows(rs.export_report(db_session, 1, "campaigns", "csv"))
    assert rows[0]["roi"] == "9.0"
    assert rows[0]["response_rate"] == "0.12"
    assert rows[0]["name"] == "Campaign 1"


def test_customer_directory_reports_raw_profile_fields(db_session):
    _seed(db_session)
    rows = _csv_rows(rs.export_report(db_session, 1, "customer_directory", "csv"))
    assert rows[0]["name"] == "Customer 1"
    assert rows[0]["location"] == "Berlin"
