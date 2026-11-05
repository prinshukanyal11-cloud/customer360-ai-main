"""Data ingestion: upload → inspect → preview (Phase 2 foundation).

This module performs the real first steps of ingestion:
- validates file type and size
- reads the CSV safely
- inspects columns, dtypes, missing values
- computes a preview and column-mapping hints

Full validation, mapping, async processing, DQ scoring and clean storage
are implemented in Phase 2 (see processing_service, dq_engine).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import AuditAction
from app.core.exceptions import ValidationError
from app.models.dataset import Dataset, DatasetVersion
from app.models.user import User
from app.services.audit_service import audit
from app.services.schema_registry import infer_mapping

UPLOAD_DIR = Path("data/uploads")


def _safe_filename(original: str) -> str:
    ext = Path(original).suffix.lower()
    if ext not in settings.allowed_extension_list:
        raise ValidationError(f"Unsupported file type '{ext}'. Allowed: {settings.allowed_extension_list}.")
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"


def _read_preview(path: Path) -> tuple[pd.DataFrame, dict]:
    full = pd.read_csv(path, low_memory=False)
    preview = full.head(10).where(pd.notnull(full.head(10)), None).to_dict(orient="records")
    info = {
        "columns": [
            {
                "name": col,
                "detected_type": str(full[col].dtype),
                "missing": int(full[col].isna().sum()),
                "sample_values": full[col].dropna().astype(str).head(3).tolist(),
            }
            for col in full.columns
        ],
        "total_rows_inspected": int(len(preview)),
    }
    return full, {"preview": preview, "info": info}


def upload_csv(db: Session, user: User, file_bytes: bytes, original_filename: str) -> dict:
    if len(file_bytes) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationError(f"File exceeds the {settings.max_upload_mb} MB limit.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(original_filename)
    path = UPLOAD_DIR / filename
    path.write_bytes(file_bytes)

    try:
        full, inspection = _read_preview(path)
    except Exception:
        raise ValidationError(
            "The file could not be read as CSV. Please check the file and retry."
        ) from None

    checksum = hashlib.sha256(file_bytes).hexdigest()
    columns = [c["name"] for c in inspection["info"]["columns"]]

    existing = db.execute(
        select(Dataset)
        .where(Dataset.organization_id == user.organization_id, Dataset.name == Path(original_filename).stem)
    ).scalar_one_or_none()

    if existing is not None:
        dataset = existing
        dataset.status = "UPLOADED"
        dataset.total_rows = int(len(full))
        dataset.columns_json = {"columns": columns, "dtypes": {
            c["name"]: c["detected_type"] for c in inspection["info"]["columns"]
        }}
        db.flush()
        latest = db.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id, DatasetVersion.organization_id == user.organization_id)
            .order_by(DatasetVersion.version_no.desc())
        ).scalars().first()
        version_no = (latest.version_no + 1) if latest else 1
    else:
        dataset = Dataset(
            organization_id=user.organization_id,
            name=Path(original_filename).stem,
            source_type="csv",
            status="UPLOADED",
            total_rows=int(len(full)),
            columns_json={"columns": columns, "dtypes": {
                c["name"]: c["detected_type"] for c in inspection["info"]["columns"]
            }},
            created_by=user.id,
        )
        db.add(dataset)
        db.flush()
        version_no = 1

    version = DatasetVersion(
        organization_id=user.organization_id,
        dataset_id=dataset.id,
        version_no=version_no,
        file_path=str(path),
        checksum=checksum,
        row_count=int(len(full)),
        dq_report_json=None,
        created_by=user.id,
        processed_at=None,
    )
    db.add(version)
    db.commit()

    audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.DATASET_UPLOAD,
        entity_type="dataset",
        entity_id=dataset.id,
        metadata_json={"rows": len(full), "version_no": version_no, "checksum": checksum[:16]},
    )

    return {
        "dataset_id": dataset.id,
        "version_id": version.id,
        "status": dataset.status,
        "file_name": original_filename,
        "checksum": checksum,
        "total_rows": dataset.total_rows,
        "mapping_suggestion": infer_mapping(columns),
        "preview": inspection["preview"],
        "columns": inspection["info"]["columns"],
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }