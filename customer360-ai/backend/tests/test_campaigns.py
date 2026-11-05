"""Campaign audience targeting, simulator math, lifecycle, and A/B measurement."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.constants import CampaignStatus, CampaignVariant
from app.core.exceptions import NotFoundError, ValidationError
from app.models.campaign import CampaignResult
from app.models.customer_features_model import CustomerFeature, CustomerSegment, Prediction, Segment
from app.models.dataset import Dataset, DatasetVersion
from app.models.transaction import Customer
from app.services import campaign_service as svc
from app.services import experiment_stats as stats
from app.services.processing_service import process_dataset_core

ORG = 901
OTHER_ORG = 902


@pytest.fixture(autouse=True)
def _artifacts(tmp_path):
    from app.core.config import settings

    settings.model_artifact_dir = str(tmp_path / "model_artifacts")
    return tmp_path


def _orders_frame() -> pd.DataFrame:
    """Four buyers; C4 is a lapsed high-value account, C3 a recent small one."""
    orders = [
        ("O1", "C1", "2024-03-01", "Widget"),
        ("O2", "C1", "2024-05-01", "Widget"),
        ("O3", "C2", "2024-02-10", "Gadget"),
        ("O4", "C2", "2024-04-11", "Sprocket"),
        ("O5", "C3", "2024-08-01", "Widget"),
        ("O6", "C4", "2023-11-02", "Gadget"),
        ("O7", "C4", "2024-01-05", "Gadget"),
    ]
    return pd.DataFrame(
        [
            {
                "order_id": oid,
                "customer_id": customer,
                "order_date": order_date,
                "product_name": product,
                "category": "Tools",
                "quantity": 1,
                "amount": "100.00",
                "discount": "0.00",
                "channel": "web",
            }
            for oid, customer, order_date, product in orders
        ]
    )


def _process(db_session, org_id: int, upload_root: Path, name: str) -> int:
    upload_root.mkdir(parents=True, exist_ok=True)
    path = upload_root / f"{name}.csv"
    frame = _orders_frame()
    frame.to_csv(path, index=False)
    dataset = Dataset(
        organization_id=org_id,
        name=name,
        status="UPLOADED",
        total_rows=len(frame),
        columns_json={"columns": frame.columns.tolist()},
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
        row_count=len(frame),
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


@pytest.fixture()
def org(db_session, tmp_path) -> int:
    assert _process(db_session, ORG, tmp_path / "uploads", "orders") == 4
    return ORG


@pytest.fixture()
def two_orgs(db_session, tmp_path) -> tuple[int, int]:
    assert _process(db_session, ORG, tmp_path / "uploads", "a") == 4
    assert _process(db_session, OTHER_ORG, tmp_path / "uploads", "b") == 4
    return ORG, OTHER_ORG


def _customers(db_session, org_id: int) -> dict[str, int]:
    rows = db_session.execute(select(Customer).where(Customer.organization_id == org_id)).scalars().all()
    return {row.external_customer_id: row.id for row in rows}


def _score_risk(db_session, org_id: int, external_id: str, risk_level: str) -> None:
    customer_id = _customers(db_session, org_id)[external_id]
    db_session.add(
        Prediction(
            organization_id=org_id,
            model_version_id=1,
            customer_id=customer_id,
            prediction_proba=0.9 if risk_level in ("HIGH", "CRITICAL") else 0.1,
            risk_level=risk_level,
            created_at=pd.Timestamp.utcnow().to_pydatetime(),
        )
    )
    db_session.commit()


def _draft(db_session, org_id: int, **overrides) -> dict:
    payload = {
        "name": "Win-back Q3",
        "objective": "Re-engage lapsed high-value buyers",
        "offer": "20% off",
        "channel": "email",
        "audience_filter": {},
    }
    payload.update(overrides)
    return svc.create_campaign(db_session, org_id, payload, user_id=1)


# --------------------------------------------------------------------------- #
# validation


def test_unknown_audience_key_is_rejected(db_session) -> None:
    with pytest.raises(ValidationError, match="Unknown audience filter keys"):
        svc.validate_audience_filter({"min_ltv": 100})


def test_empty_inclusive_list_is_rejected(db_session) -> None:
    """An empty id list must not read as 'no selector' and target everyone."""
    with pytest.raises(ValidationError, match="non-empty list"):
        svc.validate_audience_filter({"customer_ids": []})


def test_inverted_range_is_rejected(db_session) -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        svc.validate_audience_filter({"min_clv": 500, "max_clv": 100})


def test_unknown_enum_values_are_rejected(db_session) -> None:
    with pytest.raises(ValidationError, match="risk_levels"):
        svc.validate_audience_filter({"risk_levels": ["SOMETHING"]})
    with pytest.raises(ValidationError, match="channels"):
        svc.validate_audience_filter({"channels": ["carrier_pigeon"]})
    with pytest.raises(ValidationError, match="rfm_segments"):
        svc.validate_audience_filter({"rfm_segments": ["vips"]})


def test_variant_split_bounds(db_session) -> None:
    assert svc.validate_audience_filter({"variant_split": 0.3})["variant_split"] == 0.3
    with pytest.raises(ValidationError, match="between 0.1 and 0.9"):
        svc.validate_audience_filter({"variant_split": 0.95})


def test_assumptions_must_describe_a_real_test(db_session) -> None:
    with pytest.raises(ValidationError, match="nothing to test"):
        svc.validate_assumptions({"control_discount_depth": 0.2, "treatment_discount_depth": 0.1})
    with pytest.raises(ValidationError, match="confidence"):
        svc.validate_assumptions({"confidence": 0.7})
    with pytest.raises(ValidationError, match="Unknown simulation assumptions"):
        svc.validate_assumptions({"open_rate": 0.4})


def test_assumptions_merge_over_defaults(db_session) -> None:
    merged = svc.validate_assumptions({"response_rate": 0.2})
    assert merged["response_rate"] == 0.2
    assert merged["cost_per_contact"] == svc.DEFAULT_ASSUMPTIONS["cost_per_contact"]


# --------------------------------------------------------------------------- #
# audience resolution


def test_default_audience_is_every_customer(db_session, org) -> None:
    rows = svc.resolve_audience(db_session, org)
    assert len(rows) == 4
    assert {row.customer_id for row in rows} == set(_customers(db_session, org).values())


def test_clv_floor_narrows_the_audience(db_session, org) -> None:
    everything = svc.resolve_audience(db_session, org)
    high_value = max(row.clv_est or 0 for row in everything)
    threshold = (high_value + everything[0].clv_est) / 2
    rows = svc.resolve_audience(db_session, org, {"min_clv": threshold})
    assert 0 < len(rows) < 4
    assert all((row.clv_est or 0) >= threshold for row in rows)


def test_missing_features_fail_a_clv_floor(db_session, org) -> None:
    """An unscored customer is not a match for 'high value' - silence, not a pass."""
    customer_id = _customers(db_session, org)["C3"]
    for feature in db_session.execute(
        select(CustomerFeature).where(CustomerFeature.customer_id == customer_id)
    ).scalars().all():
        db_session.delete(feature)
    db_session.commit()
    assert customer_id not in {row.customer_id for row in svc.resolve_audience(db_session, org, {"min_clv": 1})}
    assert customer_id in {row.customer_id for row in svc.resolve_audience(db_session, org)}


def test_risk_level_filter(db_session, org) -> None:
    _score_risk(db_session, org, "C4", "CRITICAL")
    _score_risk(db_session, org, "C3", "LOW")
    rows = svc.resolve_audience(db_session, org, {"risk_levels": ["CRITICAL"]})
    assert [row.risk_level for row in rows] == ["CRITICAL"]


def test_unscored_customers_are_labelled(db_session, org) -> None:
    rows = svc.resolve_audience(db_session, org)
    assert all(row.risk_level == svc.UNSCORED for row in rows)


def test_exclusions_win(db_session, org) -> None:
    victim = _customers(db_session, org)["C2"]
    rows = svc.resolve_audience(db_session, org, {"exclude_customer_ids": [victim]})
    assert victim not in {row.customer_id for row in rows}
    assert len(rows) == 3


def test_segment_selector(db_session, org) -> None:
    customers = _customers(db_session, org)
    segment = Segment(organization_id=org, type="RFM", name="Lapsed", active=True)
    db_session.add(segment)
    db_session.flush()
    db_session.add_all(
        [
            CustomerSegment(organization_id=org, segment_id=segment.id, customer_id=customers["C4"]),
            CustomerSegment(organization_id=org, segment_id=segment.id, customer_id=customers["C1"]),
        ]
    )
    db_session.commit()
    rows = svc.resolve_audience(db_session, org, {"segment_ids": [segment.id]})
    assert {row.customer_id for row in rows} == {customers["C1"], customers["C4"]}


def test_inactive_segment_is_ignored(db_session, org) -> None:
    customers = _customers(db_session, org)
    segment = Segment(organization_id=org, type="RFM", name="Retired", active=False)
    db_session.add(segment)
    db_session.flush()
    db_session.add(CustomerSegment(organization_id=org, segment_id=segment.id, customer_id=customers["C1"]))
    db_session.commit()
    assert svc.resolve_audience(db_session, org, {"segment_ids": [segment.id]}) == []


def test_audience_never_leaks_another_tenant(db_session, two_orgs) -> None:
    org_a, org_b = two_orgs
    foreign = _customers(db_session, org_a)
    rows = svc.resolve_audience(db_session, org_b, {"customer_ids": list(foreign.values())})
    assert rows == []


def test_breakdown_reports_value_and_coverage(db_session, org) -> None:
    breakdown = svc.audience_breakdown(svc.resolve_audience(db_session, org))
    assert breakdown["size"] == 4
    assert breakdown["with_features"] == 4
    assert breakdown["by_risk_level"][svc.UNSCORED] == 4
    assert breakdown["total_clv"] > 0
    assert breakdown["capped_at"] == svc.MAX_AUDIENCE_SIZE


# --------------------------------------------------------------------------- #
# A/B assignment


def test_variant_assignment_is_deterministic(db_session) -> None:
    first = [svc.variant_for(7, customer_id) for customer_id in range(50)]
    second = [svc.variant_for(7, customer_id) for customer_id in range(50)]
    assert first == second


def test_variant_assignment_differs_per_campaign(db_session) -> None:
    shared = [customer_id for customer_id in range(200) if svc.variant_for(1, customer_id) == svc.variant_for(2, customer_id)]
    assert len(shared) < 200  # not every customer keeps the same arm


def test_variant_split_is_roughly_honoured(db_session) -> None:
    treatment = sum(1 for customer_id in range(2000) if svc.variant_for(1, customer_id) == CampaignVariant.TREATMENT)
    assert 900 < treatment < 1100
    skewed = sum(
        1
        for customer_id in range(2000)
        if svc.variant_for(1, customer_id, treatment_share=0.9) == CampaignVariant.TREATMENT
    )
    assert skewed > 1600


# --------------------------------------------------------------------------- #
# lifecycle


def test_create_returns_a_draft_detail(db_session, org) -> None:
    detail = _draft(db_session, org)
    assert detail["status"] == CampaignStatus.DRAFT
    assert detail["member_count"] == 0
    assert detail["assumptions"]["response_rate"] == svc.DEFAULT_ASSUMPTIONS["response_rate"]
    assert detail["audience_filter"] == {}


def test_create_requires_a_name_and_known_channel(db_session, org) -> None:
    with pytest.raises(ValidationError, match="name is required"):
        svc.create_campaign(db_session, org, {"name": "   "})
    with pytest.raises(ValidationError, match="Unknown channel"):
        svc.create_campaign(db_session, org, {"name": "x", "channel": "smoke_signal"})


def test_create_rejects_an_inverted_window(db_session, org) -> None:
    with pytest.raises(ValidationError, match="start_date cannot be after end_date"):
        svc.create_campaign(
            db_session,
            org,
            {"name": "x", "start_date": "2024-05-01", "end_date": "2024-04-01"},
        )


def test_update_only_while_draft(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    updated = svc.update_campaign(db_session, org, campaign_id, {"name": "Win-back Q4", "offer": "30% off"})
    assert updated["name"] == "Win-back Q4"
    assert updated["offer"] == "30% off"
    svc.start_campaign(db_session, org, campaign_id)
    with pytest.raises(ValidationError, match="Only draft"):
        svc.update_campaign(db_session, org, campaign_id, {"name": "too late"})


def test_update_validates_new_values(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    with pytest.raises(ValidationError, match="Unknown audience filter keys"):
        svc.update_campaign(db_session, org, campaign_id, {"audience_filter": {"segmentz": [1]}})


def test_start_snapshots_members_and_cost(db_session, org) -> None:
    detail = _draft(db_session, org, assumptions={"cost_per_contact": 1.0})
    campaign_id = detail["id"]
    started = svc.start_campaign(db_session, org, campaign_id)
    assert started["status"] == CampaignStatus.ACTIVE
    assert started["member_count"] == 4
    assert started["target_count"] == 4
    assert float(started["cost_est"]) == 4.0
    assert started["start_date"] is not None
    assert sum(started["variant_counts"].values()) == 4


def test_start_is_not_repeatable(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    svc.start_campaign(db_session, org, campaign_id)
    with pytest.raises(ValidationError, match="already running"):
        svc.start_campaign(db_session, org, campaign_id)


def test_start_refuses_an_empty_audience(db_session, org) -> None:
    campaign_id = _draft(db_session, org, audience_filter={"min_clv": 10_000_000})["id"]
    with pytest.raises(ValidationError, match="audience is empty"):
        svc.start_campaign(db_session, org, campaign_id)


def test_missing_campaign_is_a_404_style_error(db_session, org) -> None:
    with pytest.raises(NotFoundError):
        svc.campaign_detail(db_session, org, 4242)


def test_campaigns_are_invisible_across_tenants(db_session, two_orgs) -> None:
    org_a, org_b = two_orgs
    campaign_id = _draft(db_session, org_a)["id"]
    with pytest.raises(NotFoundError):
        svc.campaign_detail(db_session, org_b, campaign_id)
    assert svc.list_campaigns(db_session, org_b)["total"] == 0


# --------------------------------------------------------------------------- #
# simulation


def test_simulate_reports_a_banded_forecast(db_session, org) -> None:
    campaign_id = _draft(db_session, org, assumptions={"cost_per_contact": 1.0, "response_rate": 0.2})["id"]
    result = svc.simulate_campaign(db_session, org, campaign_id)
    forecast = result["forecast"]
    assert forecast["contacts"] == 4
    assert 0 < forecast["response_rate"] <= 1
    low, high = forecast["response_range"]
    assert low <= forecast["expected_responses"] <= high
    assert forecast["estimated_cost"] == 4.0
    assert forecast["roi"] is not None
    assert result["experiment"]["control_size"] + result["experiment"]["treatment_size"] == 4
    assert "Forecast, not a promise" in result["note"]


def test_simulation_assumptions_can_be_overridden(db_session, org) -> None:
    campaign_id = _draft(db_session, org, assumptions={"cost_per_contact": 1.0})["id"]
    cheap = svc.simulate_campaign(db_session, org, campaign_id)["forecast"]
    dear = svc.simulate_campaign(db_session, org, campaign_id, {"cost_per_contact": 50.0})["forecast"]
    assert dear["estimated_cost"] == 200.0
    assert dear["roi"] < cheap["roi"]


def test_simulation_flags_an_empty_audience(db_session, org) -> None:
    campaign_id = _draft(db_session, org, audience_filter={"min_clv": 10_000_000})["id"]
    result = svc.simulate_campaign(db_session, org, campaign_id)
    assert result["forecast"] is None
    assert "empty" in result["note"]


def test_break_even_rate_is_reported(db_session, org) -> None:
    campaign_id = _draft(db_session, org, assumptions={"cost_per_contact": 0.5, "conversion_rate": 0.25})["id"]
    forecast = svc.simulate_campaign(db_session, org, campaign_id)["forecast"]
    assert forecast["break_even_response_rate"] is not None
    assert forecast["break_even_response_rate"] > 0


# --------------------------------------------------------------------------- #
# results and A/B


def test_results_require_a_launch(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    with pytest.raises(ValidationError, match="before recording results"):
        svc.record_results(db_session, org, campaign_id, {"revenue": 100})


def test_record_results_computes_roi_and_closes(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    svc.start_campaign(db_session, org, campaign_id)
    recorded = svc.record_results(
        db_session,
        org,
        campaign_id,
        {
            "response_rate": 0.25,
            "conversion_rate": 0.1,
            "revenue": 500.0,
            "cost": 200.0,
            "variant_metrics": {
                CampaignVariant.CONTROL: {"delivered": 100, "responses": 20, "conversions": 4, "revenue": 400.0},
                CampaignVariant.TREATMENT: {"delivered": 100, "responses": 30, "conversions": 9, "revenue": 700.0},
            },
            "complete": True,
        },
    )
    assert recorded["roi"] == 1.5
    assert recorded["status"] == CampaignStatus.COMPLETED
    stored = db_session.execute(select(CampaignResult).where(CampaignResult.campaign_id == campaign_id)).scalars().all()
    assert len(stored) == 1


def test_result_validation_rejects_nonsense(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    svc.start_campaign(db_session, org, campaign_id)
    with pytest.raises(ValidationError, match="Unknown variants"):
        svc.record_results(db_session, org, campaign_id, {"variant_metrics": {"Z": {"responses": 1}}})
    with pytest.raises(ValidationError, match="between 0 and 1"):
        svc.record_results(db_session, org, campaign_id, {"response_rate": 4})
    with pytest.raises(ValidationError, match="must be a number"):
        svc.record_results(db_session, org, campaign_id, {"revenue": "lots"})


def test_ab_analysis_without_results_refuses_to_claim_a_winner(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    svc.start_campaign(db_session, org, campaign_id)
    analysis = svc.ab_analysis(db_session, org, campaign_id)
    assert analysis["members"] == 4
    assert sum(arm["size"] for arm in analysis["variants"].values()) == 4
    assert analysis["test"]["significant"] is False
    assert analysis["result_id"] is None


def test_ab_analysis_detects_a_real_lift(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    svc.start_campaign(db_session, org, campaign_id)
    svc.record_results(
        db_session,
        org,
        campaign_id,
        {
            "variant_metrics": {
                CampaignVariant.CONTROL: {"delivered": 500, "responses": 50, "revenue": 1000.0},
                CampaignVariant.TREATMENT: {"delivered": 500, "responses": 95, "revenue": 2100.0},
            }
        },
    )
    analysis = svc.ab_analysis(db_session, org, campaign_id)
    test = analysis["test"]
    assert analysis["metric_compared"] == "responses"
    assert test["significant"] is True
    assert test["lift_absolute"] == pytest.approx(0.09, abs=0.001)
    assert test["lift_relative"] == pytest.approx(0.9, abs=0.02)
    assert test["p_value"] < 0.05
    assert analysis["revenue_lift"] == pytest.approx(1.1, abs=0.001)
    assert "wins" in test["verdict"]


def test_ab_analysis_is_unreachable_from_another_tenant(db_session, two_orgs) -> None:
    org_a, org_b = two_orgs
    campaign_id = _draft(db_session, org_a)["id"]
    svc.start_campaign(db_session, org_a, campaign_id)
    svc.record_results(db_session, org_a, campaign_id, {"revenue": 10.0})
    with pytest.raises(NotFoundError):
        svc.ab_analysis(db_session, org_b, campaign_id)


def test_member_listing_paginates_and_labels(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    svc.start_campaign(db_session, org, campaign_id)
    page = svc.list_members(db_session, org, campaign_id, limit=3, offset=0)
    assert page["total"] == 4
    assert len(page["items"]) == 3
    assert {item["variant"] for item in page["items"]} <= {CampaignVariant.CONTROL, CampaignVariant.TREATMENT}
    assert all(item["label"] for item in page["items"])
    second = svc.list_members(db_session, org, campaign_id, limit=3, offset=3)
    assert {item["customer_id"] for item in page["items"]} & {item["customer_id"] for item in second["items"]} == set()


def test_list_campaigns_filters_and_counts(db_session, org) -> None:
    draft_id = _draft(db_session, org)["id"]
    live_id = _draft(db_session, org, name="Live one")["id"]
    svc.start_campaign(db_session, org, live_id)
    listing = svc.list_campaigns(db_session, org)
    assert listing["total"] == 2
    assert listing["by_status"][CampaignStatus.DRAFT] == 1
    assert listing["by_status"][CampaignStatus.ACTIVE] == 1
    assert {item["id"] for item in listing["items"]} == {draft_id, live_id}
    active_only = svc.list_campaigns(db_session, org, status="active")
    assert active_only["total"] == 1
    assert active_only["items"][0]["id"] == live_id
    with pytest.raises(ValidationError, match="Unknown campaign status"):
        svc.list_campaigns(db_session, org, status="sleeping")


def test_campaign_list_reports_the_latest_result(db_session, org) -> None:
    campaign_id = _draft(db_session, org)["id"]
    svc.start_campaign(db_session, org, campaign_id)
    svc.record_results(db_session, org, campaign_id, {"revenue": 100.0, "cost": 50.0})
    svc.record_results(db_session, org, campaign_id, {"revenue": 250.0, "cost": 50.0})
    items = svc.list_campaigns(db_session, org)["items"]
    assert items[0]["latest_result"]["revenue"] == 250.0
    assert items[0]["member_count"] == 4


def test_members_are_snapshot_not_live(db_session, org) -> None:
    """A campaign keeps its population even if features change underneath it."""
    campaign_id = _draft(db_session, org)["id"]
    svc.start_campaign(db_session, org, campaign_id)
    customer_id = _customers(db_session, org)["C4"]
    feature = db_session.execute(
        select(CustomerFeature).where(CustomerFeature.customer_id == customer_id)
    ).scalars().one()
    feature.clv_est = 0.0
    db_session.commit()
    # a fresh filter would now exclude C4, but the launched campaign keeps it
    assert customer_id not in {row.customer_id for row in svc.resolve_audience(db_session, org, {"min_clv": 1})}
    assert svc.campaign_detail(db_session, org, campaign_id)["member_count"] == 4


# --------------------------------------------------------------------------- #
# statistics


def test_two_proportion_test_flags_a_real_difference() -> None:
    result = stats.two_proportion_test(50, 500, 95, 500)
    assert result["significant"] is True
    assert result["p_value"] < 0.001
    assert result["lift_relative"] == pytest.approx(0.9, abs=0.02)
    assert result["control"]["ci_low"] < result["control"]["rate"] < result["control"]["ci_high"]


def test_two_proportion_test_stays_quiet_on_noise() -> None:
    result = stats.two_proportion_test(50, 500, 52, 500)
    assert result["significant"] is False
    assert result["p_value"] > 0.05
    assert "No significant difference" in result["verdict"]


def test_two_proportion_test_needs_both_arms() -> None:
    result = stats.two_proportion_test(0, 0, 3, 10)
    assert result["significant"] is False
    assert result["z"] is None
    assert "observations" in result["reason"]


def test_wilson_interval_stays_inside_the_unit_range() -> None:
    low, high = stats.wilson_interval(0, 20)
    assert low == 0.0
    assert 0 < high < 1
    low, high = stats.wilson_interval(20, 20)
    assert high == 1.0
    assert 0 < low < 1
    assert stats.wilson_interval(0, 0) == (0.0, 0.0)


def test_confidence_widens_the_interval() -> None:
    narrow = stats.wilson_interval(30, 100, confidence=0.90)
    wide = stats.wilson_interval(30, 100, confidence=0.99)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_required_sample_size_grows_for_smaller_effects() -> None:
    big = stats.required_sample_size(50, 500, 100, 500)
    small = stats.required_sample_size(50, 500, 55, 500)
    assert big is not None and small is not None
    assert small > big
    assert stats.required_sample_size(50, 500, 50, 500) is None


def test_binomial_range_brackets_the_mean() -> None:
    low, high = stats.binomial_range(1000, 0.12)
    assert low < 120 < high
    assert stats.binomial_range(0, 0.5) == (0, 0)
