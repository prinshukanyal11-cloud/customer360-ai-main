"""Model management: list registered models, train churn, activate versions."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.constants import AuditAction, Roles
from app.core.exceptions import NotFoundError, ValidationError
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.ml import churn as churn_service
from app.models.customer_features_model import Model, ModelVersion
from app.models.user import User
from app.services.audit_service import audit

router = APIRouter(prefix="/models", tags=["models"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


class TrainChurnRequest(BaseModel):
    families: list[str] | None = Field(default=None)
    churn_window_days: int | None = Field(default=None, ge=1, le=365)
    random_seed: int | None = Field(default=None, ge=0)


def _churn_payload(user: User, version: ModelVersion) -> dict:
    return {
        "id": version.id,
        "version_no": version.version_no,
        "family": (version.params_json or {}).get("family"),
        "metrics": version.metrics_json,
        "features": version.features_list,
        "trained_at": version.trained_at.isoformat() if version.trained_at else None,
        "active": version.active,
    }


@router.get("")
def list_models(db: DbSession, user: CurrentUser) -> dict:
    models = db.execute(
        select(Model).where(Model.organization_id == user.organization_id).order_by(Model.id)
    ).scalars().all()
    items = []
    for model in models:
        versions = db.execute(
            select(ModelVersion)
            .where(ModelVersion.model_id == model.id, ModelVersion.organization_id == user.organization_id)
            .order_by(ModelVersion.version_no.desc())
        ).scalars().all()
        items.append(
            {
                "id": model.id,
                "name": model.name,
                "model_type": model.model_type,
                "family": model.family,
                "status": model.status,
                "versions": [_churn_payload(user, v) for v in versions],
            }
        )
    return {"items": items, "total": len(items)}


@router.post("/churn/train")
def train_churn_model(db: DbSession, user: MutableRoles, body: TrainChurnRequest) -> dict:
    if body.families:
        unknown = set(body.families) - set(churn_service.AVAILABLE_FAMILIES)
        if unknown:
            raise ValidationError(f"Unknown churn model families: {sorted(unknown)}")
    return churn_service.train_churn(
        db,
        user.organization_id,
        families=body.families,
        churn_window_days=body.churn_window_days,
        random_seed=body.random_seed or 42,
        created_by=user.id,
    )


@router.post("/{model_id}/versions/{version_id}/activate")
def activate_version(db: DbSession, user: MutableRoles, model_id: int, version_id: int) -> dict:
    model = db.execute(
        select(Model).where(Model.id == model_id, Model.organization_id == user.organization_id)
    ).scalar_one_or_none()
    if model is None:
        raise NotFoundError("Model not found in this organization.")
    version = db.execute(
        select(ModelVersion).where(
            ModelVersion.id == version_id,
            ModelVersion.model_id == model_id,
            ModelVersion.organization_id == user.organization_id,
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFoundError("Model version not found in this organization.")

    db.execute(
        ModelVersion.__table__.update()
        .where(ModelVersion.model_id == model.id, ModelVersion.organization_id == user.organization_id)
        .values(active=False)
    )
    version.active = True
    db.commit()

    audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.MODEL_ACTIVATE,
        entity_type="ml-model",
        entity_id=model.id,
        metadata_json={"version_no": version.version_no, "model_type": model.model_type},
    )
    return _churn_payload(user, version)