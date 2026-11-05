"""Model monitoring endpoints: live drift, recorded runs, and a health roll-up."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.constants import AuditAction, Roles
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.models.user import User
from app.services import audit_service, monitoring_service

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


@router.get("/drift")
def get_drift(
    db: DbSession,
    user: CurrentUser,
    model_version_id: int | None = Query(
        default=None, description="Defaults to the organisation's active model version."
    ),
) -> dict:
    """Live feature/prediction drift for one model version. Writes nothing."""
    return monitoring_service.drift_report(
        db, user.organization_id, model_version_id=model_version_id
    )


@router.get("/models")
def list_model_health(db: DbSession, user: CurrentUser) -> dict:
    return monitoring_service.model_health(db, user.organization_id)


@router.get("/runs")
def list_monitoring_runs(
    db: DbSession,
    user: CurrentUser,
    model_version_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return monitoring_service.list_runs(
        db,
        user.organization_id,
        model_version_id=model_version_id,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}")
def get_monitoring_run(db: DbSession, user: CurrentUser, run_id: int) -> dict:
    return monitoring_service.get_run(db, user.organization_id, run_id)


@router.post("/runs", status_code=201)
def create_monitoring_run(
    db: DbSession,
    user: MutableRoles,
    model_version_id: int | None = Query(
        default=None, description="Defaults to the organisation's active model version."
    ),
) -> dict:
    """Run a scan and persist it so drift can be trended over time."""
    run = monitoring_service.record_run(
        db,
        user.organization_id,
        model_version_id,
        triggered_by=user.id,
    )
    audit_service.audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.MONITORING_SCAN,
        entity_type="monitoring_run",
        entity_id=str(run["id"]),
        metadata_json={
            "model_version_id": run["model_version_id"],
            "drift_status": run["drift_status"],
            "psi_overall": run["psi_overall"],
        },
    )
    return run
