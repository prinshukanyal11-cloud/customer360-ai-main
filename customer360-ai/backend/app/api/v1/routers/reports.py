"""Report export endpoints.

Exports are a data-egress path, so the catalogue is readable by any role but
every download requires ADMIN or ANALYST and is written to the audit log.
"""
from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import AuditAction, Roles
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.models.user import User
from app.services import audit_service, reports_service

router = APIRouter(prefix="/reports", tags=["reports"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


class StrictModel(BaseModel):
    """Reject unknown keys so a mistyped filter never silently widens a report."""

    model_config = ConfigDict(extra="forbid")


class ExportRequest(StrictModel):
    report: str = Field(min_length=1, max_length=64, description="Report key from /reports/catalog.")
    format: str = Field(default="csv", min_length=1, max_length=16)
    filters: dict[str, Any] | None = None


@router.get("/catalog")
def get_catalog(db: DbSession, user: CurrentUser) -> dict:
    """Available reports, their filters, and the supported output formats."""
    return reports_service.catalog()


@router.post("/export")
def export_report(
    db: DbSession,
    user: MutableRoles,
    body: ExportRequest,
) -> Response:
    result = reports_service.export_report(
        db,
        user.organization_id,
        body.report,
        body.format,
        filters=body.filters,
    )
    audit_service.audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.REPORT_EXPORT,
        entity_type="report",
        entity_id=body.report,
        metadata_json={
            "format": body.format,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "filters": body.filters or {},
        },
    )
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={
            # RFC 5987 form keeps non-ASCII filenames safe; the ASCII fallback is
            # always present because report keys are slugged.
            "Content-Disposition": (
                f"attachment; filename=\"{result.filename}\"; "
                f"filename*=UTF-8''{quote(result.filename)}"
            ),
            "X-Report-Rows": str(result.row_count),
            "X-Report-Truncated": "true" if result.truncated else "false",
            "X-Report-Max-Rows": str(reports_service.settings.report_max_rows),
        },
    )
