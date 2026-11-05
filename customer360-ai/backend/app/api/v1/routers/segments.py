"""Segmentation endpoints: train K-Means, list segments, cluster analytics, relabel."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.constants import Roles
from app.core.exceptions import NotFoundError
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.ml import segmentation as segmentation_service
from app.models.customer_features_model import CustomerSegment, Segment
from app.models.user import User

router = APIRouter(prefix="/segments", tags=["segments"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


class TrainRequest(BaseModel):
    n_clusters: int | None = Field(default=None, ge=2, le=8)
    use_pca: bool = True
    random_seed: int | None = Field(default=None, ge=0)


class RenameSegmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=32)


@router.post("/train")
def train_segmentation(db: DbSession, user: MutableRoles, body: TrainRequest) -> dict:
    return segmentation_service.train_segmentation(
        db,
        user.organization_id,
        n_clusters=body.n_clusters,
        use_pca=body.use_pca,
        random_seed=body.random_seed or 42,
        created_by=user.id,
    )


@router.get("")
def list_segments(
    db: DbSession,
    user: CurrentUser,
    segment_type: str | None = Query(default=None),
) -> dict:
    stmt = select(Segment).where(Segment.organization_id == user.organization_id)
    if segment_type:
        stmt = stmt.where(Segment.type == segment_type)
    segments = db.execute(stmt.order_by(Segment.type, Segment.id)).scalars().all()

    counts = dict(
        db.execute(
            select(Segment.id, func.count(CustomerSegment.id))
            .select_from(Segment)
            .join(CustomerSegment, CustomerSegment.segment_id == Segment.id)
            .where(Segment.organization_id == user.organization_id)
            .group_by(Segment.id)
        ).all()
    )
    return {
        "items": [
            {
                "id": segment.id,
                "name": segment.name,
                "type": segment.type,
                "description": segment.description,
                "color": segment.color,
                "active": segment.active,
                "member_count": int(counts.get(segment.id, 0)),
                "rules_json": segment.rules_json,
            }
            for segment in segments
        ],
        "total": len(segments),
    }


@router.get("/{segment_id}/clusters")
def cluster_analytics(db: DbSession, user: CurrentUser, segment_id: int) -> dict:
    result = segmentation_service.segment_analytics(db, user.organization_id, segment_id)
    if result is None:
        raise NotFoundError("Segment not found in this organization.")
    return result


@router.patch("/{segment_id}")
def rename_segment(db: DbSession, user: MutableRoles, segment_id: int, body: RenameSegmentRequest) -> dict:
    row = db.execute(
        select(Segment).where(Segment.id == segment_id, Segment.organization_id == user.organization_id)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Segment not found in this organization.")
    row.name = body.name
    if body.description is not None:
        row.description = body.description
    if body.color is not None:
        row.color = body.color
    db.commit()
    return {
        "id": row.id,
        "name": row.name,
        "type": row.type,
        "description": row.description,
        "color": row.color,
    }