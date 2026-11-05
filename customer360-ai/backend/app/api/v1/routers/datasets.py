"""Dataset endpoints: upload, list, map columns, validate (DQ) and process.

Phase 2 wiring: every step of ingestion is exposed here with proper
org-scoping, RBAC, and audit trail.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from app.core.constants import AuditAction, Roles
from app.core.exceptions import NotFoundError, ValidationError
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.models.dataset import Dataset, DatasetVersion
from app.models.user import User
from app.services.audit_service import audit
from app.services.dataset_service import upload_csv
from app.services.dq_engine import run_quality_checks
from app.services.processing_service import (
    ensure_valid_mapping,
    missing_required,
    process_dataset_core,
    read_dataframe,
    representative_entities,
)
from app.services.schema_registry import infer_mapping

router = APIRouter(prefix="/datasets", tags=["datasets"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


class MapColumnsRequest(BaseModel):
    mapping: dict[str, dict[str, str]]


def _get_dataset(db: DbSession, user: CurrentUser, dataset_id: int) -> tuple[Dataset, DatasetVersion]:
    dataset = db.execute(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.organization_id == user.organization_id)
    ).scalar_one_or_none()
    if dataset is None:
        raise NotFoundError("Dataset not found.")
    version = db.execute(
        select(DatasetVersion)
        .where(
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.organization_id == user.organization_id,
        )
        .order_by(DatasetVersion.version_no.desc())
    ).scalars().first()
    if version is None:
        raise NotFoundError("Dataset has no uploaded version.")
    return dataset, version


@router.get("")
def list_datasets(
    db: DbSession,
    user: CurrentUser,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    stmt = select(Dataset).where(Dataset.organization_id == user.organization_id)
    if status:
        stmt = stmt.where(Dataset.status == status.upper())
    total = len(db.execute(stmt).scalars().all())
    items = db.execute(
        stmt.order_by(Dataset.id.desc()).limit(page_size).offset((page - 1) * page_size)
    ).scalars().all()
    result = []
    for ds in items:
        latest = db.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == ds.id, DatasetVersion.organization_id == user.organization_id)
            .order_by(DatasetVersion.version_no.desc())
        ).scalars().first()
        result.append(
            {
                "id": ds.id,
                "name": ds.name,
                "source_type": ds.source_type,
                "status": ds.status,
                "total_rows": ds.total_rows,
                "columns": (ds.columns_json or {}).get("columns", []),
                "version_no": latest.version_no if latest else None,
                "dq_score": latest.dq_score if latest else None,
                "processed_at": latest.processed_at.isoformat() if latest and latest.processed_at else None,
                "created_by": ds.created_by,
                "created_at": ds.created_at.isoformat(),
            }
        )
    return {"items": result, "total": total, "page": page, "page_size": page_size}


@router.get("/{dataset_id}")
def get_dataset(db: DbSession, user: CurrentUser, dataset_id: int) -> dict:
    dataset, version = _get_dataset(db, user, dataset_id)
    stored = (version.column_mapping_json or {}).get("entities") if version.column_mapping_json else None
    suggestion = stored or infer_mapping((dataset.columns_json or {}).get("columns", []))
    return {
        "id": dataset.id,
        "name": dataset.name,
        "source_type": dataset.source_type,
        "status": dataset.status,
        "total_rows": dataset.total_rows,
        "columns": (dataset.columns_json or {}).get("columns", []),
        "detected_dtypes": (dataset.columns_json or {}).get("dtypes", {}),
        "mapping": suggestion,
        "required_missing": missing_required(suggestion),
        "versions": [
            {
                "id": v.id,
                "version_no": v.version_no,
                "row_count": v.row_count,
                "checksum": v.checksum[:16] if v.checksum else None,
                "dq_score": v.dq_score,
                "processed_at": v.processed_at.isoformat() if v.processed_at else None,
                "created_at": v.created_at.isoformat(),
            }
            for v in sorted(
                db.execute(
                    select(DatasetVersion).where(
                        DatasetVersion.dataset_id == dataset.id,
                        DatasetVersion.organization_id == user.organization_id,
                    )
                ).scalars().all(),
                key=lambda v: v.version_no,
            )
        ],
    }


@router.post("/upload", status_code=201)
def upload_dataset(
    db: DbSession,
    user: MutableRoles,
    file: UploadFile = File(...),
) -> dict:
    content = file.file.read()
    return upload_csv(db, user, content, file.filename or "upload.csv")


@router.post("/{dataset_id}/map-columns")
def map_columns(
    db: DbSession,
    user: MutableRoles,
    dataset_id: int,
    payload: MapColumnsRequest = Body(...),
) -> dict:
    dataset, version = _get_dataset(db, user, dataset_id)
    columns = (dataset.columns_json or {}).get("columns", [])
    mapping = ensure_valid_mapping(payload.mapping, columns)
    version.column_mapping_json = {"entities": mapping}
    dataset.status = "MAPPED"
    db.commit()

    audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.DATASET_MAP,
        entity_type="dataset",
        entity_id=dataset.id,
        metadata_json={"mapping": mapping},
    )
    return {
        "dataset_id": dataset.id,
        "status": dataset.status,
        "mapping": mapping,
        "required_missing": missing_required(mapping),
        "unmapped_columns": [c for c in columns if c not in {v for m in mapping.values() for v in m.values()}],
        "eligible_entities": representative_entities(mapping),
    }


@router.get("/{dataset_id}/mapping-suggestion")
def get_mapping_suggestion(db: DbSession, user: CurrentUser, dataset_id: int) -> dict:
    dataset, version = _get_dataset(db, user, dataset_id)
    columns = (dataset.columns_json or {}).get("columns", [])
    suggestion = infer_mapping(columns)
    claimed = {col for mapping in suggestion.values() for col in mapping.values()}
    return {
        "dataset_id": dataset.id,
        "entities": suggestion,
        "required_missing": missing_required(suggestion),
        "unmapped_columns": [c for c in columns if c not in claimed],
        "columns": columns,
    }


@router.post("/{dataset_id}/validate")
def validate_dataset(
    db: DbSession,
    user: MutableRoles,
    dataset_id: int,
) -> dict:
    dataset, version = _get_dataset(db, user, dataset_id)
    columns = (dataset.columns_json or {}).get("columns", [])
    stored = (version.column_mapping_json or {}).get("entities") if version.column_mapping_json else None
    mapping = stored or infer_mapping(columns)

    eligible = representative_entities(mapping)
    if not eligible:
        raise ValidationError(
            "Required columns are not mapped. Map the dataset before validating.",
            details={"required_missing": missing_required(mapping)},
        )

    df = read_dataframe(version.file_path)
    report = run_quality_checks(df, mapping)
    version.dq_score = report["score"]
    version.dq_report_json = report
    version.column_mapping_json = {"entities": mapping}
    dataset.status = "VALIDATED"
    db.commit()

    audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.DATASET_VALIDATE,
        entity_type="dataset",
        entity_id=dataset.id,
        metadata_json={"score": report["score"], "blocked": report["blocked"]},
    )
    return {
        "dataset_id": dataset.id,
        "status": dataset.status,
        "score": report["score"],
        "blocked": report["blocked"],
        "report": report,
        "mapping": mapping,
    }


@router.post("/{dataset_id}/process")
def process_dataset(
    db: DbSession,
    user: MutableRoles,
    dataset_id: int,
    force_validation: bool = False,
) -> dict:
    dataset, _version = _get_dataset(db, user, dataset_id)
    # The core pipeline validates (DQ) and persists clean rows. In production a
    # broker queues this same core via the Celery worker; the API path runs it
    # inline within the request transaction (deterministic, test-friendly).
    try:
        result = process_dataset_core(db, dataset, user.organization_id, force_validation=force_validation)
    except Exception:
        dataset.status = "FAILED"
        db.commit()
        raise
    return result


@router.get("/{dataset_id}/quality")
def get_dataset_quality(db: DbSession, user: CurrentUser, dataset_id: int) -> dict:
    dataset, version = _get_dataset(db, user, dataset_id)
    return {
        "dataset_id": dataset_id,
        "status": dataset.status,
        "score": version.dq_score,
        "blocked": bool(version.dq_report_json and version.dq_report_json.get("blocked")),
        "report": version.dq_report_json,
    }