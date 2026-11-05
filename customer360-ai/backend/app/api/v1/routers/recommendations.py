"""Retention recommendation endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.constants import PriorityLevel, Roles
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.ml import churn as churn_service  # noqa: F401  (kept for parity with sibling routers)
from app.models.user import User
from app.services import retention_service

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


class GenerateRecommendationsRequest(BaseModel):
    customer_ids: list[int] | None = Field(default=None, max_length=1000)
    min_risk_level: str = Field(default="MEDIUM")


@router.post("/generate")
def generate_recommendations(
    db: DbSession,
    user: MutableRoles,
    body: GenerateRecommendationsRequest,
) -> dict:
    return retention_service.generate_recommendations(
        db,
        user.organization_id,
        customer_ids=body.customer_ids,
        min_risk_level=body.min_risk_level,
    )


@router.get("")
def list_recommendations(
    db: DbSession,
    user: CurrentUser,
    customer_id: int | None = Query(None),
    min_priority: str = Query(PriorityLevel.LOW),
) -> dict:
    return retention_service.list_recommendations(
        db,
        user.organization_id,
        customer_id=customer_id,
        min_priority=min_priority,
    )