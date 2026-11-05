"""Churn predictions API: score customers with the active model, list at-risk."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.constants import Roles
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.ml import churn as churn_service
from app.ml import explain as explain_service
from app.models.user import User

router = APIRouter(prefix="/predictions", tags=["predictions"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


class ChurnScoreRequest(BaseModel):
    customer_ids: list[int] | None = Field(default=None, max_length=1000)


class ExplainRequest(BaseModel):
    customer_ids: list[int] | None = Field(default=None, max_length=1000)


@router.post("/churn")
def score_churn(db: DbSession, user: MutableRoles, body: ChurnScoreRequest) -> dict:
    """Score all buyers (or the given customers) with the active churn model."""
    return churn_service.score_customers(
        db,
        user.organization_id,
        customer_ids=body.customer_ids,
    )


@router.post("/churn/explain")
def explain_churn(db: DbSession, user: MutableRoles, body: ExplainRequest) -> dict:
    """Compute and persist SHAP explanations for the active model's predictions."""
    return explain_service.explain_predictions(
        db,
        user.organization_id,
        customer_ids=body.customer_ids,
    )


@router.get("/at-risk")
def at_risk(
    db: DbSession,
    user: CurrentUser,
    min_risk_level: str = Query(default="HIGH"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    if min_risk_level not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        min_risk_level = "HIGH"
    return churn_service.at_risk_customers(
        db,
        user.organization_id,
        min_risk_level=min_risk_level,
        page=page,
        page_size=page_size,
    )