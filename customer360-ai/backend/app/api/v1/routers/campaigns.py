"""Campaign builder, simulator, and A/B experiment endpoints."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import Roles
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.models.user import User
from app.services import campaign_service

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


class StrictModel(BaseModel):
    """Reject unknown keys instead of dropping them.

    A silently ignored typo in an audience filter would widen the target to
    everyone, so the edge refuses input the service would not understand.
    """

    model_config = ConfigDict(extra="forbid")


class AudienceFilter(StrictModel):
    """Every field narrows the audience; unknown keys are rejected server-side."""

    segment_ids: list[int] | None = None
    rfm_segments: list[str] | None = None
    risk_levels: list[str] | None = None
    channels: list[str] | None = None
    customer_ids: list[int] | None = None
    exclude_customer_ids: list[int] | None = None
    min_clv: float | None = Field(default=None, ge=0)
    max_clv: float | None = Field(default=None, ge=0)
    min_frequency: float | None = Field(default=None, ge=0)
    max_recency_days: float | None = Field(default=None, ge=0)
    min_recency_days: float | None = Field(default=None, ge=0)
    variant_split: float | None = Field(default=None, ge=0.1, le=0.9)


class SimulationAssumptions(StrictModel):
    response_rate: float | None = Field(default=None, ge=0, le=1)
    conversion_rate: float | None = Field(default=None, ge=0, le=1)
    revenue_per_conversion: float | None = Field(default=None, ge=0)
    cost_per_contact: float | None = Field(default=None, ge=0)
    control_discount_depth: float | None = Field(default=None, ge=0, le=1)
    treatment_discount_depth: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = None


class VariantMetrics(BaseModel):
    delivered: int | None = Field(default=None, ge=0)
    responses: int | None = Field(default=None, ge=0)
    conversions: int | None = Field(default=None, ge=0)
    revenue: float | None = Field(default=None, ge=0)


class CreateCampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    objective: str | None = Field(default=None, max_length=255)
    offer: str | None = Field(default=None, max_length=255)
    channel: str | None = None
    audience_filter: AudienceFilter | None = None
    assumptions: SimulationAssumptions | None = None
    start_date: date | None = None
    end_date: date | None = None


class UpdateCampaignRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    objective: str | None = Field(default=None, max_length=255)
    offer: str | None = Field(default=None, max_length=255)
    channel: str | None = None
    audience_filter: AudienceFilter | None = None
    assumptions: SimulationAssumptions | None = None
    start_date: date | None = None
    end_date: date | None = None


class SimulateCampaignRequest(BaseModel):
    """Assumption overrides for a what-if; the campaign keeps its own settings."""

    assumptions: SimulationAssumptions | None = None


class RecordResultsRequest(BaseModel):
    response_rate: float | None = Field(default=None, ge=0, le=1)
    conversion_rate: float | None = Field(default=None, ge=0, le=1)
    revenue: float | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    variant_metrics: dict[str, VariantMetrics] | None = None
    complete: bool = False


@router.post("", status_code=201)
def create_campaign(
    db: DbSession,
    user: MutableRoles,
    body: CreateCampaignRequest,
) -> dict:
    return campaign_service.create_campaign(
        db,
        user.organization_id,
        _as_payload(body),
        user_id=user.id,
    )


@router.get("")
def list_campaigns(
    db: DbSession,
    user: CurrentUser,
    status: str | None = Query(default=None, description="DRAFT | ACTIVE | PAUSED | COMPLETED"),
    channel: str | None = Query(default=None, description="email | sms | push | in_app"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    return campaign_service.list_campaigns(
        db,
        user.organization_id,
        status=status,
        channel=channel,
        limit=limit,
    )


@router.get("/{campaign_id}")
def get_campaign(db: DbSession, user: CurrentUser, campaign_id: int) -> dict:
    return campaign_service.campaign_detail(db, user.organization_id, campaign_id)


@router.patch("/{campaign_id}")
def update_campaign(
    db: DbSession,
    user: MutableRoles,
    campaign_id: int,
    body: UpdateCampaignRequest,
) -> dict:
    return campaign_service.update_campaign(
        db,
        user.organization_id,
        campaign_id,
        _as_payload(body, exclude_unset=True),
    )


@router.post("/{campaign_id}/simulate")
def simulate_campaign(
    db: DbSession,
    user: MutableRoles,
    campaign_id: int,
    body: SimulateCampaignRequest | None = None,
) -> dict:
    overrides = _as_payload(body.assumptions) if body and body.assumptions else None
    return campaign_service.simulate_campaign(
        db,
        user.organization_id,
        campaign_id,
        overrides,
        user_id=user.id,
    )


@router.post("/{campaign_id}/start")
def start_campaign(db: DbSession, user: MutableRoles, campaign_id: int) -> dict:
    return campaign_service.start_campaign(db, user.organization_id, campaign_id, user_id=user.id)


@router.post("/{campaign_id}/results")
def record_results(
    db: DbSession,
    user: MutableRoles,
    campaign_id: int,
    body: RecordResultsRequest,
) -> dict:
    payload = body.model_dump(exclude_unset=True)
    payload["variant_metrics"] = {
        arm: values.model_dump(exclude_none=True)
        for arm, values in (body.variant_metrics or {}).items()
    } or None
    return campaign_service.record_results(
        db,
        user.organization_id,
        campaign_id,
        payload,
        user_id=user.id,
    )


@router.get("/{campaign_id}/members")
def list_members(
    db: DbSession,
    user: CurrentUser,
    campaign_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return campaign_service.list_members(
        db,
        user.organization_id,
        campaign_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{campaign_id}/ab-analysis")
def ab_analysis(
    db: DbSession,
    user: CurrentUser,
    campaign_id: int,
    confidence: float = Query(default=0.95, ge=0.5, lt=1.0),
) -> dict:
    return campaign_service.ab_analysis(db, user.organization_id, campaign_id, confidence=confidence)


def _as_payload(model: BaseModel | None, *, exclude_unset: bool = False) -> dict[str, Any]:
    """Pydantic model -> plain dict with nested models expanded, dropping empties."""
    if model is None:
        return {}
    payload = model.model_dump(exclude_none=True, exclude_unset=exclude_unset)
    for key in ("audience_filter", "assumptions"):
        nested = payload.get(key)
        if isinstance(nested, BaseModel):
            payload[key] = nested.model_dump(exclude_none=True)
    return payload
