"""Analytics endpoints: RFM mix, CLV distribution, revenue trends, cohorts,
journey, product recommendations."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.security_deps import CurrentUser, DbSession
from app.services import analytics_service, engagement_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/features-summary")
def features_summary(db: DbSession, user: CurrentUser) -> dict:
    return analytics_service.features_summary(db, user.organization_id)


@router.get("/rfm")
def rfm_distribution(db: DbSession, user: CurrentUser) -> dict:
    return analytics_service.rfm_distribution(db, user.organization_id)


@router.get("/clv")
def clv_distribution(db: DbSession, user: CurrentUser) -> dict:
    return analytics_service.clv_distribution(db, user.organization_id)


@router.get("/trends")
def revenue_trends(
    db: DbSession,
    user: CurrentUser,
    months: int = Query(12, ge=1, le=36),
) -> dict:
    return analytics_service.revenue_trends(db, user.organization_id, months=months)


@router.get("/cohorts")
def cohorts(
    db: DbSession,
    user: CurrentUser,
    months: int = Query(12, ge=1, le=36),
) -> dict:
    return engagement_service.cohort_retention(db, user.organization_id, months=months)


@router.get("/journey")
def journey(db: DbSession, user: CurrentUser) -> dict:
    return engagement_service.customer_journey(db, user.organization_id)


@router.get("/products")
def products(
    db: DbSession,
    user: CurrentUser,
    product_id: int | None = Query(None, ge=1),
    customer_id: int | None = Query(None, ge=1),
    limit: int = Query(5, ge=1, le=20),
) -> dict:
    return engagement_service.product_recommendations(
        db,
        user.organization_id,
        product_id=product_id,
        customer_id=customer_id,
        limit=limit,
    )