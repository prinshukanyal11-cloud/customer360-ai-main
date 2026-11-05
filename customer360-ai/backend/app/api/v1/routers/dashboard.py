"""Executive and customer dashboards (facts from live data)."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.security_deps import CurrentUser, DbSession
from app.services.dashboard_service import dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def get_summary(db: DbSession, user: CurrentUser) -> dict:
    return dashboard_summary(db, user.organization_id)