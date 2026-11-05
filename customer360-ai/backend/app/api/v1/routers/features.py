"""Customer 360 feature store: trigger (re)build of RFM + behavioral features."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.constants import AuditAction, Roles
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.models.user import User
from app.services import analytics_service, feature_engineering
from app.services.audit_service import audit

router = APIRouter(prefix="/features", tags=["features"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


@router.post("/build")
def build_features(db: DbSession, user: MutableRoles) -> dict:
    summary = feature_engineering.compute_features(db, user.organization_id)
    audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.FEATURES_BUILD,
        entity_type="features",
        metadata_json={"status": summary["status"], "buyers": summary["buyers"]},
    )
    return summary


@router.get("/summary")
def features_summary(db: DbSession, user: CurrentUser) -> dict:
    return analytics_service.features_summary(db, user.organization_id)