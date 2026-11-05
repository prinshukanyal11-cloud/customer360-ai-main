"""Campaign builder, audience resolution, simulator, and A/B measurement.

Design notes:

* **The audience is resolved once, at start.** Members are snapshotted so a
  campaign keeps the same population even after features are rebuilt.
* **A/B assignment is deterministic** - a stable hash of campaign + customer -
  so nobody can drift between arms across retries or a second worker. That is
  the prerequisite for a trustworthy test, and it also means the variant can be
  recomputed from scratch rather than trusted as stored state.
* **Simulation is explicitly a forecast.** It blends caller-supplied
  assumptions with churn-risk priors and reports binomial bands, never a single
  confident number.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.constants import (
    AuditAction,
    CampaignChannel,
    CampaignMemberStatus,
    CampaignStatus,
    CampaignVariant,
    RfmSegment,
    RiskLevel,
)
from app.core.exceptions import NotFoundError, ValidationError
from app.models.campaign import Campaign, CampaignMember, CampaignResult
from app.models.customer_features_model import CustomerFeature, CustomerSegment, Prediction, Segment
from app.models.transaction import Customer
from app.services import audit_service
from app.services import experiment_stats as stats

UNSCORED = "UNSCORED"

# --- audience filter vocabulary -------------------------------------------------
# Keys are validated explicitly: a typo must fail loudly rather than silently
# widening the audience to "everyone".
AUDIENCE_KEYS = frozenset(
    {
        "segment_ids",
        "rfm_segments",
        "risk_levels",
        "channels",
        "customer_ids",
        "exclude_customer_ids",
        "min_clv",
        "max_clv",
        "min_frequency",
        "max_recency_days",
        "min_recency_days",
        "variant_split",
    }
)
_INT_LIST_KEYS = ("segment_ids", "customer_ids", "exclude_customer_ids")
_LABEL_LIST_KEYS = ("rfm_segments", "risk_levels", "channels")
_NUMERIC_KEYS = ("min_clv", "max_clv", "min_frequency", "max_recency_days", "min_recency_days")

MAX_AUDIENCE_SIZE = 20_000

DEFAULT_ASSUMPTIONS: dict = {
    "response_rate": 0.12,
    "conversion_rate": 0.20,
    "revenue_per_conversion": None,  # defaults to the audience's median CLV
    "cost_per_contact": 0.35,
    "control_discount_depth": 0.0,
    "treatment_discount_depth": 0.15,
    "confidence": 0.95,
}

# Response priors by churn risk: a riskier customer is easier to re-engage.
# The caller's `response_rate` scales these relative to UNSCORED.
RISK_RESPONSE_PRIOR: dict[str, float] = {
    RiskLevel.CRITICAL: 0.22,
    RiskLevel.HIGH: 0.16,
    RiskLevel.MEDIUM: 0.10,
    RiskLevel.LOW: 0.05,
    UNSCORED: 0.09,
}

# Response lift prior for the treatment arm, per 100% of discount depth.
LIFT_PER_DISCOUNT_DEPTH = 0.10


@dataclass(frozen=True)
class AudienceRow:
    """One targeted customer plus the attributes the simulator needs."""

    customer_id: int
    clv_est: float | None
    monetary: float | None
    frequency: float | None
    recency_days: float | None
    risk_level: str


# --------------------------------------------------------------------------- #
# validation


def validate_audience_filter(audience: dict | None) -> dict:
    """Reject unknown keys and impossible ranges, then normalise the payload."""
    payload = audience or {}
    if not isinstance(payload, dict):
        raise ValidationError("audience_filter must be an object.")
    unknown = sorted(set(payload) - AUDIENCE_KEYS)
    if unknown:
        raise ValidationError(f"Unknown audience filter keys: {', '.join(unknown)}")
    clean: dict = {}
    for key, value in payload.items():
        if key in _INT_LIST_KEYS:
            clean[key] = _int_list(value, key)
        elif key in _LABEL_LIST_KEYS:
            clean[key] = _label_list(value, key)
        elif key in _NUMERIC_KEYS:
            if value is None:
                continue
            clean[key] = _number(value, f"audience_filter.{key}", minimum=0)
        elif key == "variant_split":
            split = _number(value, "audience_filter.variant_split")
            if not 0.1 <= split <= 0.9:
                raise ValidationError("audience_filter.variant_split must be between 0.1 and 0.9.")
            clean[key] = split

    for low, high in (("min_clv", "max_clv"), ("min_recency_days", "max_recency_days")):
        if low in clean and high in clean and clean[low] > clean[high]:
            raise ValidationError(f"audience_filter.{low} cannot exceed {high}.")

    if "rfm_segments" in clean:
        valid = set(RfmSegment.ALL)
        invalid = [value for value in clean["rfm_segments"] if value not in valid]
        if invalid:
            raise ValidationError(f"Unknown rfm_segments: {', '.join(invalid)}")
    if "risk_levels" in clean:
        clean["risk_levels"] = [value.upper() for value in clean["risk_levels"]]
        invalid = [value for value in clean["risk_levels"] if value not in RiskLevel.ALL]
        if invalid:
            raise ValidationError(f"Unknown risk_levels: {', '.join(invalid)}")
    if "channels" in clean:
        clean["channels"] = [value.lower() for value in clean["channels"]]
        invalid = [value for value in clean["channels"] if value not in CampaignChannel.ALL]
        if invalid:
            raise ValidationError(f"Unknown channels: {', '.join(invalid)}")
    return clean


def validate_assumptions(assumptions: dict | None) -> dict:
    """Merge caller assumptions over the defaults, validating each override."""
    payload = assumptions or {}
    if not isinstance(payload, dict):
        raise ValidationError("assumptions must be an object.")
    unknown = sorted(set(payload) - set(DEFAULT_ASSUMPTIONS))
    if unknown:
        raise ValidationError(f"Unknown simulation assumptions: {', '.join(unknown)}")
    merged = dict(DEFAULT_ASSUMPTIONS)
    for key, value in payload.items():
        if value is None:
            continue
        if key == "confidence":
            merged[key] = _number(value, "assumption confidence")
            if round(merged[key], 2) not in (0.9, 0.95, 0.99):
                raise ValidationError("assumption confidence must be 0.9, 0.95 or 0.99.")
            continue
        if key == "revenue_per_conversion":
            merged[key] = _number(value, "assumption revenue_per_conversion", minimum=0)
            continue
        merged[key] = _number(value, f"assumption {key}", minimum=0)
        if (
            key in ("response_rate", "conversion_rate", "control_discount_depth", "treatment_discount_depth")
            and merged[key] > 1
        ):
            raise ValidationError(f"assumption {key} must be between 0 and 1.")
    if merged["treatment_discount_depth"] <= merged["control_discount_depth"]:
        raise ValidationError(
            "assumption treatment_discount_depth must exceed control_discount_depth, "
            "otherwise there is nothing to test."
        )
    return merged


# --------------------------------------------------------------------------- #
# audience resolution


def _latest_features(db: Session, organization_id: int) -> dict[int, CustomerFeature]:
    """Newest feature row per customer (one grouped query, no N+1)."""
    latest = (
        select(CustomerFeature.customer_id, func.max(CustomerFeature.id).label("latest_id"))
        .where(CustomerFeature.organization_id == organization_id)
        .group_by(CustomerFeature.customer_id)
        .subquery()
    )
    rows = db.execute(
        select(CustomerFeature)
        .join(latest, latest.c.latest_id == CustomerFeature.id)
        .where(CustomerFeature.organization_id == organization_id)
    ).scalars().all()
    return {row.customer_id: row for row in rows}


def _latest_risk_levels(db: Session, organization_id: int) -> dict[int, str]:
    """Newest prediction per customer -> risk level (UNSCORED when none exist)."""
    latest = (
        select(Prediction.customer_id, func.max(Prediction.id).label("latest_id"))
        .where(Prediction.organization_id == organization_id)
        .group_by(Prediction.customer_id)
        .subquery()
    )
    rows = db.execute(
        select(Prediction.customer_id, Prediction.risk_level)
        .join(latest, latest.c.latest_id == Prediction.id)
        .where(Prediction.organization_id == organization_id)
    ).all()
    return {customer_id: (risk_level or UNSCORED) for customer_id, risk_level in rows}


def _segment_members(db: Session, organization_id: int, segment_ids: list[int]) -> set[int]:
    """Customer ids in any of the given active segments."""
    if not segment_ids:
        return set()
    rows = db.execute(
        select(CustomerSegment.customer_id)
        .join(Segment, Segment.id == CustomerSegment.segment_id)
        .where(
            CustomerSegment.organization_id == organization_id,
            CustomerSegment.segment_id.in_(segment_ids),
            Segment.organization_id == organization_id,
            Segment.active.is_(True),
        )
    ).all()
    return {row[0] for row in rows}


def _candidate_customers(db: Session, organization_id: int, audience: dict) -> list[int]:
    """Inclusive selectors first (segments / explicit ids), then every customer."""
    segment_ids = audience.get("segment_ids") or []
    include_ids = audience.get("customer_ids") or []
    if not segment_ids and not include_ids:
        return list(
            db.execute(select(Customer.id).where(Customer.organization_id == organization_id)).scalars().all()
        )
    candidates = _segment_members(db, organization_id, segment_ids) | set(include_ids)
    if not candidates:
        return []
    # Re-check ownership: a customer id from another tenant must never survive.
    owned = db.execute(
        select(Customer.id).where(
            Customer.organization_id == organization_id,
            Customer.id.in_(sorted(candidates)),
        )
    ).scalars().all()
    return sorted(set(owned))


def resolve_audience(db: Session, organization_id: int, audience: dict | None = None) -> list[AudienceRow]:
    """Resolve a filter into the concrete list of customers to target."""
    audience = validate_audience_filter(audience)
    candidates = _candidate_customers(db, organization_id, audience)
    candidates = set(candidates) - set(audience.get("exclude_customer_ids") or [])
    if not candidates:
        return []

    wanted_channels = set(audience.get("channels") or [])
    if wanted_channels:
        allowed = db.execute(
            select(Customer.id).where(
                Customer.organization_id == organization_id,
                Customer.acquisition_channel.in_(sorted(wanted_channels)),
            )
        ).scalars().all()
        candidates &= set(allowed)

    features = _latest_features(db, organization_id)
    risk_levels = _latest_risk_levels(db, organization_id)
    wanted_risk = set(audience.get("risk_levels") or [])
    wanted_rfm = {value.lower() for value in (audience.get("rfm_segments") or [])}

    rows: list[AudienceRow] = []
    for customer_id in sorted(candidates):
        feature = features.get(customer_id)
        if wanted_rfm:
            label = (feature.rfm_segment if feature else None) or ""
            if label.lower() not in wanted_rfm:
                continue
        risk_level = risk_levels.get(customer_id, UNSCORED)
        if wanted_risk and risk_level not in wanted_risk:
            continue
        clv = _attr(feature, "clv_est")
        frequency = _attr(feature, "frequency")
        recency = _attr(feature, "recency_days")
        if not _at_least(clv, audience.get("min_clv")):
            continue
        if audience.get("max_clv") is not None and (clv or 0.0) > audience["max_clv"]:
            continue
        if not _at_least(frequency, audience.get("min_frequency")):
            continue
        if audience.get("max_recency_days") is not None and (recency or 0.0) > audience["max_recency_days"]:
            continue
        if audience.get("min_recency_days") is not None and (recency or 0.0) < audience["min_recency_days"]:
            continue
        rows.append(
            AudienceRow(
                customer_id=customer_id,
                clv_est=clv,
                monetary=_attr(feature, "monetary"),
                frequency=frequency,
                recency_days=recency,
                risk_level=risk_level,
            )
        )
        if len(rows) >= MAX_AUDIENCE_SIZE:
            break
    return rows


def _at_least(value: float | None, threshold: float | None) -> bool:
    """Missing feature rows fail a lower bound - an unscored customer is not a match."""
    if threshold is None:
        return True
    return value is not None and value >= threshold


def audience_breakdown(rows: list[AudienceRow]) -> dict:
    """Size and value profile of a resolved audience, for review before launch."""
    clvs = [row.clv_est for row in rows if row.clv_est is not None]
    by_risk: dict[str, int] = {}
    for row in rows:
        by_risk[row.risk_level] = by_risk.get(row.risk_level, 0) + 1
    return {
        "size": len(rows),
        "total_clv": round(sum(clvs), 2),
        "avg_clv": round(sum(clvs) / len(clvs), 2) if clvs else 0.0,
        "median_clv": round(_median(clvs), 2),
        "by_risk_level": by_risk,
        "with_features": sum(1 for row in rows if row.clv_est is not None),
        "scored_for_risk": len(rows) - by_risk.get(UNSCORED, 0),
        "capped_at": MAX_AUDIENCE_SIZE,
    }


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


# --------------------------------------------------------------------------- #
# A/B assignment


def variant_for(campaign_id: int, customer_id: int, *, treatment_share: float = 0.5) -> str:
    """Deterministic A/B assignment for a (campaign, customer) pair."""
    digest = hashlib.sha256(f"{campaign_id}:{customer_id}".encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return CampaignVariant.TREATMENT if bucket < treatment_share else CampaignVariant.CONTROL


def treatment_share_of(audience_filter: dict | None) -> float:
    return float((audience_filter or {}).get("variant_split", 0.5))


# --------------------------------------------------------------------------- #
# lifecycle


def _get_campaign(db: Session, organization_id: int, campaign_id: int) -> Campaign:
    campaign = db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise NotFoundError("Campaign not found.")
    return campaign


def _validate_window(start: date | None, end: date | None) -> None:
    if start and end and start > end:
        raise ValidationError("start_date cannot be after end_date.")


def create_campaign(db: Session, organization_id: int, payload: dict, *, user_id: int | None = None) -> dict:
    """Create a DRAFT campaign with a validated audience filter and assumptions."""
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValidationError("Campaign name is required.")
    channel = (payload.get("channel") or CampaignChannel.EMAIL).lower()
    if channel not in CampaignChannel.ALL:
        raise ValidationError(f"Unknown channel: {channel}.")
    audience = validate_audience_filter(payload.get("audience_filter"))
    assumptions = validate_assumptions(payload.get("assumptions"))
    start_date = _as_date(payload.get("start_date"), "start_date")
    end_date = _as_date(payload.get("end_date"), "end_date")
    _validate_window(start_date, end_date)

    campaign = Campaign(
        organization_id=organization_id,
        name=name,
        objective=(payload.get("objective") or "").strip() or None,
        offer=(payload.get("offer") or "").strip() or None,
        channel=channel,
        audience_filter_json=audience,
        assumptions_json=assumptions,
        start_date=start_date,
        end_date=end_date,
        status=CampaignStatus.DRAFT,
        created_by=user_id,
    )
    db.add(campaign)
    db.flush()
    audit_service.audit(
        db,
        organization_id,
        user_id,
        AuditAction.CAMPAIGN_CREATE,
        entity_type="campaign",
        entity_id=campaign.id,
        metadata_json={"name": name, "channel": channel},
    )
    return campaign_detail(db, organization_id, campaign.id)


def update_campaign(db: Session, organization_id: int, campaign_id: int, payload: dict) -> dict:
    """Edit a draft. Once launched the audience is frozen, so nothing changes."""
    campaign = _get_campaign(db, organization_id, campaign_id)
    if campaign.status != CampaignStatus.DRAFT:
        raise ValidationError("Only draft campaigns can be edited.")
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValidationError("Campaign name cannot be empty.")
        campaign.name = name
    for field in ("objective", "offer"):
        if field in payload:
            setattr(campaign, field, (payload.get(field) or "").strip() or None)
    if payload.get("channel"):
        channel = str(payload["channel"]).lower()
        if channel not in CampaignChannel.ALL:
            raise ValidationError(f"Unknown channel: {channel}.")
        campaign.channel = channel
    if "audience_filter" in payload:
        campaign.audience_filter_json = validate_audience_filter(payload["audience_filter"])
    if "assumptions" in payload:
        campaign.assumptions_json = validate_assumptions(payload["assumptions"])
    for field in ("start_date", "end_date"):
        if field in payload:
            setattr(campaign, field, _as_date(payload.get(field), field))
    _validate_window(campaign.start_date, campaign.end_date)

    db.flush()
    audit_service.audit(
        db,
        organization_id,
        None,
        AuditAction.CAMPAIGN_UPDATE,
        entity_type="campaign",
        entity_id=campaign.id,
    )
    return campaign_detail(db, organization_id, campaign.id)


def simulate_campaign(
    db: Session,
    organization_id: int,
    campaign_id: int,
    overrides: dict | None = None,
    *,
    user_id: int | None = None,
) -> dict:
    """Forecast funnel, revenue, ROI, and experiment power for a campaign."""
    campaign = _get_campaign(db, organization_id, campaign_id)
    rows = resolve_audience(db, organization_id, campaign.audience_filter_json or {})
    assumptions = validate_assumptions({**(campaign.assumptions_json or {}), **(overrides or {})})
    breakdown = audience_breakdown(rows)
    audit_service.audit(
        db,
        organization_id,
        user_id,
        AuditAction.CAMPAIGN_SIMULATE,
        entity_type="campaign",
        entity_id=campaign.id,
        metadata_json={"audience": breakdown["size"], "overrides": bool(overrides)},
    )
    if not rows:
        return {
            "campaign_id": campaign.id,
            "status": campaign.status,
            "audience": breakdown,
            "assumptions": assumptions,
            "forecast": None,
            "experiment": None,
            "note": "The audience is empty - widen the filter before simulating.",
        }

    confidence = assumptions["confidence"]
    size = breakdown["size"]
    scale = assumptions["response_rate"] / RISK_RESPONSE_PRIOR[UNSCORED]
    by_risk = {
        risk: {"customers": count, "assumed_response_rate": round(_response_prior(risk) * scale, 4)}
        for risk, count in breakdown["by_risk_level"].items()
    }
    blended_rate = _blended_response_rate(rows, assumptions)
    expected_responses = size * blended_rate
    expected_conversions = expected_responses * assumptions["conversion_rate"]
    revenue_per_conversion = assumptions["revenue_per_conversion"] or breakdown["median_clv"]
    discount = assumptions["treatment_discount_depth"]
    expected_revenue = expected_conversions * revenue_per_conversion * (1 - discount)
    cost = size * assumptions["cost_per_contact"]
    roi = (expected_revenue - cost) / cost if cost > 0 else None
    revenue_per_contact = assumptions["conversion_rate"] * revenue_per_conversion * (1 - discount)
    break_even = (
        assumptions["cost_per_contact"] / revenue_per_contact if revenue_per_contact > 0 else None
    )

    return {
        "campaign_id": campaign.id,
        "status": campaign.status,
        "audience": breakdown,
        "assumptions": assumptions,
        "by_risk_level": by_risk,
        "forecast": {
            "contacts": size,
            "response_rate": round(blended_rate, 4),
            "expected_responses": round(expected_responses, 1),
            "response_range": list(stats.binomial_range(size, blended_rate, confidence=confidence)),
            "expected_conversions": round(expected_conversions, 1),
            "revenue_per_conversion": round(revenue_per_conversion, 2),
            "expected_revenue": round(expected_revenue, 2),
            "estimated_cost": round(cost, 2),
            "roi": round(roi, 3) if roi is not None else None,
            "break_even_response_rate": round(break_even, 4) if break_even is not None else None,
        },
        "experiment": _power_preview(
            int(size * (1 - treatment_share_of(campaign.audience_filter_json))),
            int(size * treatment_share_of(campaign.audience_filter_json)),
            assumptions,
            blended_rate,
        ),
        "note": (
            "Forecast, not a promise: the response rate is your assumption scaled by "
            "each segment's churn-risk prior, and the range is a binomial confidence band."
        ),
    }


def _response_prior(risk_level: str) -> float:
    return min(1.0, RISK_RESPONSE_PRIOR.get(risk_level, RISK_RESPONSE_PRIOR[UNSCORED]))


def _blended_response_rate(rows: list[AudienceRow], assumptions: dict) -> float:
    """Average per-customer response rate after applying risk priors."""
    scale = assumptions["response_rate"] / RISK_RESPONSE_PRIOR[UNSCORED]
    total = sum(_response_prior(row.risk_level) * scale for row in rows)
    return total / len(rows)


def _power_preview(control_n: int, treatment_n: int, assumptions: dict, base_rate: float) -> dict:
    """Would the projected arms be large enough to call a winner?"""
    discount_delta = assumptions["treatment_discount_depth"] - assumptions["control_discount_depth"]
    expected_lift = base_rate * discount_delta * LIFT_PER_DISCOUNT_DEPTH
    treatment_rate = min(1.0, base_rate + expected_lift)
    test = stats.two_proportion_test(
        round(control_n * base_rate),
        control_n,
        round(treatment_n * treatment_rate),
        treatment_n,
        confidence=assumptions["confidence"],
    )
    return {
        "control_size": control_n,
        "treatment_size": treatment_n,
        "expected_lift": round(expected_lift, 4),
        "p_value": test["p_value"],
        "significant": test["significant"],
        "required_sample_size": test["required_sample_size"],
        "verdict": test["verdict"],
        "split": f"{control_n} control / {treatment_n} treatment",
    }


def start_campaign(db: Session, organization_id: int, campaign_id: int, *, user_id: int | None = None) -> dict:
    """Snapshot the audience, assign variants, and move the campaign to ACTIVE."""
    campaign = _get_campaign(db, organization_id, campaign_id)
    if campaign.status == CampaignStatus.ACTIVE:
        raise ValidationError("Campaign is already running.")
    audience_filter = campaign.audience_filter_json or {}
    rows = resolve_audience(db, organization_id, audience_filter)
    if not rows:
        raise ValidationError("The audience is empty - there is nothing to launch.")

    db.execute(delete(CampaignMember).where(CampaignMember.campaign_id == campaign.id))
    now = datetime.now(UTC)
    db.add_all(
        CampaignMember(
            organization_id=organization_id,
            campaign_id=campaign.id,
            customer_id=row.customer_id,
            status=CampaignMemberStatus.ELIGIBLE,
            assigned_at=now,
        )
        for row in rows
    )
    assumptions = campaign.assumptions_json or {}
    campaign.status = CampaignStatus.ACTIVE
    campaign.target_count = len(rows)
    campaign.cost_est = round(len(rows) * float(assumptions.get("cost_per_contact", 0.0)), 2)
    campaign.start_date = campaign.start_date or date.today()
    db.flush()
    audit_service.audit(
        db,
        organization_id,
        user_id,
        AuditAction.CAMPAIGN_START,
        entity_type="campaign",
        entity_id=campaign.id,
        metadata_json={"audience": len(rows), "cost_est": float(campaign.cost_est)},
    )
    return campaign_detail(db, organization_id, campaign.id)


def record_results(
    db: Session,
    organization_id: int,
    campaign_id: int,
    payload: dict,
    *,
    user_id: int | None = None,
) -> dict:
    """Store a measurement, and close the campaign when asked.

    `variant_metrics` is optional but required for an A/B verdict: supply
    per-arm delivered/responses/conversions and the statistical test can run.
    """
    campaign = _get_campaign(db, organization_id, campaign_id)
    if campaign.status == CampaignStatus.DRAFT:
        raise ValidationError("Launch the campaign before recording results.")
    metrics = payload.get("variant_metrics") or {}
    if not isinstance(metrics, dict):
        raise ValidationError("variant_metrics must be an object keyed by variant.")
    unknown = sorted(set(metrics) - set(CampaignVariant.ALL))
    if unknown:
        raise ValidationError(f"Unknown variants: {', '.join(unknown)}.")
    for arm, values in metrics.items():
        if not isinstance(values, dict):
            raise ValidationError(f"variant_metrics.{arm} must be an object.")
        for field in ("delivered", "responses", "conversions", "revenue"):
            if field in values:
                _number(values[field], f"variant_metrics.{arm}.{field}", minimum=0)

    revenue = _optional_number(payload.get("revenue"), "revenue", minimum=0)
    cost = _optional_number(payload.get("cost"), "cost", minimum=0)
    response_rate = _optional_rate(payload.get("response_rate"), "response_rate")
    conversion_rate = _optional_rate(payload.get("conversion_rate"), "conversion_rate")
    roi = (revenue - cost) / cost if revenue is not None and cost else None

    result = CampaignResult(
        organization_id=organization_id,
        campaign_id=campaign.id,
        response_rate=response_rate,
        conversion_rate=conversion_rate,
        revenue=revenue,
        cost=cost,
        roi=round(roi, 4) if roi is not None else None,
        metrics_json=metrics or None,
        calculated_at=datetime.now(UTC),
    )
    db.add(result)
    if payload.get("complete"):
        campaign.status = CampaignStatus.COMPLETED
        campaign.end_date = campaign.end_date or date.today()
    db.flush()
    audit_service.audit(
        db,
        organization_id,
        user_id,
        AuditAction.CAMPAIGN_RESULTS,
        entity_type="campaign",
        entity_id=campaign.id,
        metadata_json={"revenue": revenue, "cost": cost, "roi": result.roi},
    )
    return {
        "campaign_id": campaign.id,
        "result_id": result.id,
        "status": campaign.status,
        "response_rate": response_rate,
        "conversion_rate": conversion_rate,
        "revenue": revenue,
        "cost": cost,
        "roi": result.roi,
        "ab_analysis": ab_analysis(db, organization_id, campaign.id),
    }


def ab_analysis(db: Session, organization_id: int, campaign_id: int, *, confidence: float = 0.95) -> dict:
    """Deterministic variant sizes plus the verdict from the most recent result."""
    campaign = _get_campaign(db, organization_id, campaign_id)
    share = treatment_share_of(campaign.audience_filter_json)
    customer_ids = db.execute(
        select(CampaignMember.customer_id).where(CampaignMember.campaign_id == campaign.id)
    ).scalars().all()
    sizes = {arm: 0 for arm in CampaignVariant.ALL}
    for customer_id in customer_ids:
        sizes[variant_for(campaign.id, customer_id, treatment_share=share)] += 1

    result = _latest_result(db, organization_id, campaign.id)
    metrics = (result.metrics_json or {}) if result else {}
    control_arm, treatment_arm = CampaignVariant.CONTROL, CampaignVariant.TREATMENT
    metric = "responses" if _arm_value(metrics, control_arm, "responses") is not None else "conversions"
    control_trials = int(_arm_value(metrics, control_arm, "delivered") or sizes[control_arm])
    treatment_trials = int(_arm_value(metrics, treatment_arm, "delivered") or sizes[treatment_arm])
    control_successes = int(_arm_value(metrics, control_arm, metric) or 0)
    treatment_successes = int(_arm_value(metrics, treatment_arm, metric) or 0)

    revenue_control = _arm_value(metrics, control_arm, "revenue")
    revenue_treatment = _arm_value(metrics, treatment_arm, "revenue")
    revenue_lift = None
    if revenue_control and revenue_treatment is not None:
        revenue_lift = round((revenue_treatment - revenue_control) / revenue_control, 4)

    return {
        "campaign_id": campaign.id,
        "campaign_status": campaign.status,
        "members": len(customer_ids),
        "variant_split": share,
        "variants": {
            CampaignVariant.CONTROL: {"size": sizes[CampaignVariant.CONTROL], "role": "control"},
            CampaignVariant.TREATMENT: {"size": sizes[CampaignVariant.TREATMENT], "role": "treatment"},
        },
        "metric_compared": metric,
        "result_id": result.id if result else None,
        "measured_at": result.calculated_at.isoformat() if result else None,
        "test": stats.two_proportion_test(
            control_successes,
            control_trials,
            treatment_successes,
            treatment_trials,
            confidence=confidence,
        ),
        "revenue_lift": revenue_lift,
        "note": (
            "Arm membership is deterministic per (campaign, customer). Record "
            "variant_metrics to produce a verdict; sizes alone cannot."
        ),
    }


# --------------------------------------------------------------------------- #
# reads


def list_campaigns(
    db: Session,
    organization_id: int,
    *,
    status: str | None = None,
    channel: str | None = None,
    limit: int = 50,
) -> dict:
    stmt = select(Campaign).where(Campaign.organization_id == organization_id)
    if status:
        if status.upper() not in CampaignStatus.ALL:
            raise ValidationError(f"Unknown campaign status: {status}.")
        stmt = stmt.where(Campaign.status == status.upper())
    if channel:
        stmt = stmt.where(Campaign.channel == channel.lower())
    campaigns = db.execute(stmt.order_by(Campaign.id.desc()).limit(limit)).scalars().all()

    member_counts = dict(
        db.execute(
            select(CampaignMember.campaign_id, func.count(CampaignMember.id))
            .join(Campaign, Campaign.id == CampaignMember.campaign_id)
            .where(Campaign.organization_id == organization_id)
            .group_by(CampaignMember.campaign_id)
        ).all()
    )
    latest_results: dict[int, CampaignResult] = {}
    for result in _results_ordered(db, organization_id, [c.id for c in campaigns]):
        # results arrive newest-first, so the first hit for a campaign wins
        latest_results.setdefault(result.campaign_id, result)
    items = [
        {
            "id": campaign.id,
            "name": campaign.name,
            "objective": campaign.objective,
            "channel": campaign.channel,
            "status": campaign.status,
            "target_count": campaign.target_count,
            "member_count": member_counts.get(campaign.id, 0),
            "cost_est": float(campaign.cost_est) if campaign.cost_est is not None else None,
            "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
            "end_date": campaign.end_date.isoformat() if campaign.end_date else None,
            "latest_result": _result_summary(latest_results.get(campaign.id)),
            "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        }
        for campaign in campaigns
    ]
    return {
        "total": len(items),
        "limit": limit,
        "items": items,
        "by_status": {state: sum(1 for item in items if item["status"] == state) for state in CampaignStatus.ALL},
    }


def campaign_detail(db: Session, organization_id: int, campaign_id: int) -> dict:
    campaign = _get_campaign(db, organization_id, campaign_id)
    customer_ids = db.execute(
        select(CampaignMember.customer_id).where(CampaignMember.campaign_id == campaign.id)
    ).scalars().all()
    share = treatment_share_of(campaign.audience_filter_json)
    variant_counts = {arm: 0 for arm in CampaignVariant.ALL}
    for customer_id in customer_ids:
        variant_counts[variant_for(campaign.id, customer_id, treatment_share=share)] += 1
    return {
        "id": campaign.id,
        "name": campaign.name,
        "objective": campaign.objective,
        "offer": campaign.offer,
        "channel": campaign.channel,
        "status": campaign.status,
        "audience_filter": campaign.audience_filter_json or {},
        "assumptions": campaign.assumptions_json or {},
        "target_count": campaign.target_count,
        "member_count": len(customer_ids),
        "variant_counts": variant_counts,
        "cost_est": float(campaign.cost_est) if campaign.cost_est is not None else None,
        "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
        "end_date": campaign.end_date.isoformat() if campaign.end_date else None,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "results": [_result_summary(result) for result in _results_ordered(db, organization_id, [campaign.id])],
    }


def list_members(
    db: Session,
    organization_id: int,
    campaign_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Paginated audience preview with the deterministic variant per customer."""
    campaign = _get_campaign(db, organization_id, campaign_id)
    total = db.execute(
        select(func.count(CampaignMember.id)).where(CampaignMember.campaign_id == campaign.id)
    ).scalar_one()
    rows = db.execute(
        select(CampaignMember.customer_id, CampaignMember.status, CampaignMember.assigned_at)
        .where(CampaignMember.campaign_id == campaign.id)
        .order_by(CampaignMember.id)
        .limit(limit)
        .offset(offset)
    ).all()
    share = treatment_share_of(campaign.audience_filter_json)
    names = _customer_labels(db, [row[0] for row in rows])
    return {
        "campaign_id": campaign.id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "customer_id": row[0],
                "label": names.get(row[0]),
                "status": row[1],
                "variant": variant_for(campaign.id, row[0], treatment_share=share),
                "assigned_at": row[2].isoformat() if row[2] else None,
            }
            for row in rows
        ],
    }


# --------------------------------------------------------------------------- #
# helpers


def _results_ordered(db: Session, organization_id: int, campaign_ids: list[int]) -> list[CampaignResult]:
    """All results for the given campaigns, newest first (one query)."""
    if not campaign_ids:
        return []
    return list(
        db.execute(
            select(CampaignResult)
            .where(
                CampaignResult.organization_id == organization_id,
                CampaignResult.campaign_id.in_(campaign_ids),
            )
            .order_by(CampaignResult.calculated_at.desc(), CampaignResult.id.desc())
        ).scalars().all()
    )


def _latest_result(db: Session, organization_id: int, campaign_id: int) -> CampaignResult | None:
    results = _results_ordered(db, organization_id, [campaign_id])
    return results[0] if results else None


def _arm_value(metrics: dict, arm: str, field: str) -> float | None:
    values = metrics.get(arm) or {}
    value = values.get(field)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _customer_labels(db: Session, customer_ids: list[int]) -> dict[int, str]:
    if not customer_ids:
        return {}
    rows = db.execute(
        select(Customer.id, Customer.name, Customer.external_customer_id, Customer.email).where(
            Customer.id.in_(customer_ids)
        )
    ).all()
    return {row[0]: (row[1] or row[2] or row[3] or f"#{row[0]}") for row in rows}


def _result_summary(result: CampaignResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "id": result.id,
        "response_rate": result.response_rate,
        "conversion_rate": result.conversion_rate,
        "revenue": result.revenue,
        "cost": result.cost,
        "roi": result.roi,
        "measured_at": result.calculated_at.isoformat(),
    }


def _attr(feature: CustomerFeature | None, name: str) -> float | None:
    value = getattr(feature, name, None) if feature is not None else None
    return float(value) if isinstance(value, (int, float)) else None


def _as_date(value, field: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValidationError(f"{field} must be an ISO date (YYYY-MM-DD).") from exc


def _int_list(value, field: str) -> list[int]:
    """Non-empty list of integer ids.

    Empty lists are rejected on purpose: an empty inclusive selector would
    otherwise read as "no selector" and quietly target the whole tenant.
    """
    if not isinstance(value, (list, tuple)) or not value:
        raise ValidationError(f"audience_filter.{field} must be a non-empty list of ids.")
    ids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValidationError(f"audience_filter.{field} must contain integer ids.")
        ids.append(item)
    return ids


def _label_list(value, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValidationError(f"audience_filter.{field} must be a non-empty list of labels.")
    return [str(item).strip().lower() for item in value]


def _number(value, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a number.")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ValidationError(f"{field} must be >= {minimum}.")
    return number


def _optional_number(value, field: str, *, minimum: float | None = None) -> float | None:
    return None if value is None else _number(value, field, minimum=minimum)


def _optional_rate(value, field: str) -> float | None:
    if value is None:
        return None
    rate = _number(value, field, minimum=0)
    if rate > 1:
        raise ValidationError(f"{field} must be between 0 and 1.")
    return rate
