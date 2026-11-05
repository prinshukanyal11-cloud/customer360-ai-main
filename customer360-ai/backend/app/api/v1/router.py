"""API router aggregation."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import (
    ai,
    analytics,
    auth,
    campaigns,
    customers,
    dashboard,
    datasets,
    features,
    health,
    models,
    monitoring,
    predictions,
    recommendations,
    reports,
    segments,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(customers.router)
api_router.include_router(datasets.router)
api_router.include_router(models.router)
api_router.include_router(analytics.router)
api_router.include_router(features.router)
api_router.include_router(segments.router)
api_router.include_router(predictions.router)
api_router.include_router(recommendations.router)
api_router.include_router(ai.router)
api_router.include_router(campaigns.router)
api_router.include_router(monitoring.router)
api_router.include_router(reports.router)
api_router.include_router(health.router)