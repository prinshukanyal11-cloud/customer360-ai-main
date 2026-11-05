"""Report exports.

Every report is organisation-scoped by construction: each builder receives
`organization_id` and filters on it, so an export can never widen visibility
past the caller's tenant. Rows are capped by `settings.report_max_rows` to keep
a request from materialising an unbounded result set, and the response reports
whether it was truncated rather than silently returning a partial file.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import CampaignChannel, CampaignStatus, RiskLevel
from app.core.exceptions import ValidationError
from app.ml.churn import at_risk_customers
from app.models.campaign import Campaign, CampaignResult
from app.models.customer_features_model import CustomerFeature, Prediction
from app.models.transaction import Customer
from app.services import customer_service


@dataclass
class ExportResult:
    content: bytes
    filename: str
    media_type: str
    columns: list[str]
    row_count: int
    truncated: bool
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------- helpers


def _latest_features_subquery(db: Session, organization_id: int):
    """Latest feature row id per customer (avoids a huge `IN (...)` list)."""
    return (
        select(CustomerFeature.customer_id, func.max(CustomerFeature.id).label("latest_id"))
        .where(CustomerFeature.organization_id == organization_id)
        .group_by(CustomerFeature.customer_id)
        .subquery()
    )


def _known_filters(allowed: set[str], filters: dict | None) -> dict:
    supplied = dict(filters or {})
    unknown = sorted(set(supplied) - allowed)
    if unknown:
        raise ValidationError(
            f"Unsupported filter(s) for this report: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(allowed)) or 'none'}."
        )
    return supplied


def _fetch_limit() -> int:
    """Builders fetch one row past the cap so `export_report` can flag truncation.

    Applying the cap inside the builder would make a truncated result
    indistinguishable from one that exactly filled the limit.
    """
    return settings.report_max_rows + 1


def _cap(rows: list[dict], limit: int) -> tuple[list[dict], bool]:
    return (rows, False) if len(rows) <= limit else (rows[:limit], True)


def _csv_bytes(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(column) is None else row.get(column) for column in columns])
    return buffer.getvalue().encode("utf-8-sig")


def _xlsx_bytes(title: str, columns: list[str], rows: list[dict[str, Any]], meta: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"

    sheet.cell(row=1, column=1, value=title).font = Font(bold=True, size=13)
    sheet.cell(row=2, column=1, value=f"Generated {meta.get('generated_at')}").font = Font(size=9, italic=True)
    for offset, (key, value) in enumerate(
        [(key, value) for key, value in meta.items() if key != "generated_at"], start=3
    ):
        sheet.cell(row=offset, column=1, value=f"{key}: {value}").font = Font(size=9)

    header_row = len(meta) + 3
    header_fill = PatternFill("solid", fgColor="1F2937")
    for index, column in enumerate(columns, start=1):
        cell = sheet.cell(row=header_row, column=index, value=column)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for offset, row in enumerate(rows, start=header_row + 1):
        for index, column in enumerate(columns, start=1):
            sheet.cell(row=offset, column=index, value=row.get(column))

    last_row = header_row + max(len(rows), 1)
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)
    if rows:
        sheet.auto_filter.ref = (
            f"A{header_row}:{get_column_letter(len(columns))}{last_row}"
        )
    for index, column in enumerate(columns, start=1):
        widest = max([len(str(column))] + [len(str(row.get(column) or "")) for row in rows[:200]])
        sheet.column_dimensions[get_column_letter(index)].width = min(max(widest + 2, 10), 46)

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, indent=2, default=str).encode("utf-8")


# --------------------------------------------------------------------- reports


def _build_customers(db: Session, organization_id: int, filters: dict) -> tuple[list[str], list[dict]]:
    filters = _known_filters({"search"}, filters)
    items = customer_service.list_customers(
        db,
        organization_id,
        page=1,
        page_size=_fetch_limit(),
        search=filters.get("search"),
    )["items"]
    columns = [
        "id",
        "external_customer_id",
        "name",
        "email",
        "location",
        "signup_date",
        "total_orders",
        "total_revenue",
        "last_purchase",
        "rfm_score",
        "rfm_segment",
        "clv_est",
    ]
    return columns, list(items)


def _build_at_risk(db: Session, organization_id: int, filters: dict) -> tuple[list[str], list[dict]]:
    filters = _known_filters({"min_risk_level", "limit"}, filters)
    level = str(filters.get("min_risk_level") or RiskLevel.HIGH).upper()
    if level not in RiskLevel.ALL:
        raise ValidationError(
            f"min_risk_level must be one of: {', '.join(RiskLevel.ALL)}."
        )
    limit = min(int(filters.get("limit") or settings.report_max_rows), settings.report_max_rows)
    payload = at_risk_customers(db, organization_id, min_risk_level=level, page=1, page_size=limit + 1)
    columns = [
        "customer_id",
        "name",
        "email",
        "external_customer_id",
        "probability",
        "risk_level",
        "risk_category",
        "clv_est",
        "value_at_risk",
    ]
    return columns, list(payload["items"])


def _build_features(db: Session, organization_id: int, filters: dict) -> tuple[list[str], list[dict]]:
    _known_filters(set(), filters)
    latest = _latest_features_subquery(db, organization_id)
    rows = (
        db.execute(
            select(CustomerFeature)
            .join(latest, latest.c.latest_id == CustomerFeature.id)
            .order_by(CustomerFeature.customer_id)
            .limit(_fetch_limit())
        )
        .scalars()
        .all()
    )
    columns = [
        "customer_id",
        "recency_days",
        "frequency",
        "monetary",
        "rfm_score",
        "rfm_segment",
        "aov",
        "avg_days_between",
        "tenure_days",
        "category_diversity",
        "discount_usage",
        "engagement_score",
        "clv_est",
        "dataset_version_id",
    ]
    items = [{column: getattr(row, column, None) for column in columns} for row in rows]
    items.sort(key=lambda item: item["customer_id"] or 0)
    return columns, items


def _build_predictions(db: Session, organization_id: int, filters: dict) -> tuple[list[str], list[dict]]:
    filters = _known_filters({"model_version_id"}, filters)
    stmt = select(Prediction).where(Prediction.organization_id == organization_id)
    if filters.get("model_version_id") is not None:
        stmt = stmt.where(Prediction.model_version_id == int(filters["model_version_id"]))
    rows = db.execute(
        stmt.order_by(Prediction.model_version_id, Prediction.customer_id).limit(_fetch_limit())
    ).scalars().all()
    columns = [
        "customer_id",
        "model_version_id",
        "prediction_label",
        "prediction_proba",
        "risk_level",
        "created_at",
    ]
    items = [
        {
            "customer_id": row.customer_id,
            "model_version_id": row.model_version_id,
            "prediction_label": row.prediction_label,
            "prediction_proba": row.prediction_proba,
            "risk_level": row.risk_level,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return columns, items


def _build_campaigns(db: Session, organization_id: int, filters: dict) -> tuple[list[str], list[dict]]:
    filters = _known_filters({"status", "channel"}, filters)
    stmt = select(Campaign).where(Campaign.organization_id == organization_id)
    if filters.get("status"):
        status = str(filters["status"]).upper()
        if status not in CampaignStatus.ALL:
            raise ValidationError(f"status must be one of: {', '.join(CampaignStatus.ALL)}.")
        stmt = stmt.where(Campaign.status == status)
    if filters.get("channel"):
        channel = str(filters["channel"]).lower()
        if channel not in CampaignChannel.ALL:
            raise ValidationError(f"channel must be one of: {', '.join(CampaignChannel.ALL)}.")
        stmt = stmt.where(Campaign.channel == channel)
    campaigns = db.execute(
        stmt.order_by(Campaign.id.desc()).limit(_fetch_limit())
    ).scalars().all()

    latest_result: dict[int, CampaignResult] = {}
    if campaigns:
        results = db.execute(
            select(CampaignResult)
            .where(
                CampaignResult.organization_id == organization_id,
                CampaignResult.campaign_id.in_([c.id for c in campaigns]),
            )
            .order_by(CampaignResult.calculated_at.desc())
        ).scalars().all()
        for result in results:
            latest_result.setdefault(result.campaign_id, result)

    columns = [
        "id",
        "name",
        "objective",
        "offer",
        "channel",
        "status",
        "target_count",
        "cost_est",
        "start_date",
        "end_date",
        "response_rate",
        "conversion_rate",
        "revenue",
        "roi",
        "calculated_at",
    ]
    items = []
    for campaign in campaigns:
        result = latest_result.get(campaign.id)
        items.append(
            {
                "id": campaign.id,
                "name": campaign.name,
                "objective": campaign.objective,
                "offer": campaign.offer,
                "channel": campaign.channel,
                "status": campaign.status,
                "target_count": campaign.target_count,
                "cost_est": float(campaign.cost_est) if campaign.cost_est is not None else None,
                "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
                "end_date": campaign.end_date.isoformat() if campaign.end_date else None,
                "response_rate": result.response_rate if result else None,
                "conversion_rate": result.conversion_rate if result else None,
                "revenue": result.revenue if result else None,
                "roi": result.roi if result else None,
                "calculated_at": result.calculated_at.isoformat() if result and result.calculated_at else None,
            }
        )
    return columns, items


def _build_campaign_results(db: Session, organization_id: int, filters: dict) -> tuple[list[str], list[dict]]:
    _known_filters({"campaign_id"}, filters)
    stmt = (
        select(CampaignResult, Campaign.name)
        .join(Campaign, Campaign.id == CampaignResult.campaign_id)
        .where(CampaignResult.organization_id == organization_id)
    )
    if filters.get("campaign_id") is not None:
        stmt = stmt.where(CampaignResult.campaign_id == int(filters["campaign_id"]))
    rows = db.execute(
        stmt.order_by(CampaignResult.calculated_at.desc()).limit(_fetch_limit())
    ).all()
    columns = [
        "campaign_id",
        "campaign_name",
        "response_rate",
        "conversion_rate",
        "revenue",
        "cost",
        "roi",
        "calculated_at",
    ]
    return columns, [
        {
            "campaign_id": result.campaign_id,
            "campaign_name": name,
            "response_rate": result.response_rate,
            "conversion_rate": result.conversion_rate,
            "revenue": result.revenue,
            "cost": result.cost,
            "roi": result.roi,
            "calculated_at": result.calculated_at.isoformat() if result.calculated_at else None,
        }
        for result, name in rows
    ]


def _build_customers_raw(db: Session, organization_id: int, filters: dict) -> tuple[list[str], list[dict]]:
    _known_filters(set(), filters)
    customers = db.execute(
        select(Customer)
        .where(Customer.organization_id == organization_id)
        .order_by(Customer.id)
        .limit(_fetch_limit())
    ).scalars().all()
    columns = [
        "id",
        "external_customer_id",
        "name",
        "email",
        "phone",
        "location",
        "age",
        "gender",
        "signup_date",
        "acquisition_channel",
    ]
    return columns, [
        {
            "id": row.id,
            "external_customer_id": row.external_customer_id,
            "name": row.name,
            "email": row.email,
            "phone": row.phone,
            "location": row.location,
            "age": row.age,
            "gender": row.gender,
            "signup_date": row.signup_date.isoformat() if row.signup_date else None,
            "acquisition_channel": row.acquisition_channel,
        }
        for row in customers
    ]


_REPORTS: dict[str, dict[str, Any]] = {
    "customers": {
        "label": "Customer overview",
        "description": "Every customer with lifetime revenue, RFM segment, and CLV estimate.",
        "filters": ["search"],
        "builder": _build_customers,
    },
    "customer_directory": {
        "label": "Customer directory",
        "description": "Raw customer profile fields as captured at ingestion.",
        "filters": [],
        "builder": _build_customers_raw,
    },
    "at_risk": {
        "label": "At-risk customers",
        "description": "Customers scored as likely to churn by the active churn model.",
        "filters": ["min_risk_level", "limit"],
        "builder": _build_at_risk,
    },
    "features": {
        "label": "Customer features",
        "description": "Latest engineered feature row per customer.",
        "filters": [],
        "builder": _build_features,
    },
    "predictions": {
        "label": "Model predictions",
        "description": "Stored churn predictions, optionally for one model version.",
        "filters": ["model_version_id"],
        "builder": _build_predictions,
    },
    "campaigns": {
        "label": "Campaign performance",
        "description": "Campaigns with their most recent measured results.",
        "filters": ["status", "channel"],
        "builder": _build_campaigns,
    },
    "campaign_results": {
        "label": "Campaign results history",
        "description": "Every recorded campaign result row, newest first.",
        "filters": ["campaign_id"],
        "builder": _build_campaign_results,
    },
}


def available_formats() -> list[str]:
    return [fmt.strip().lower() for fmt in settings.report_formats.split(",") if fmt.strip()]


def catalog() -> dict:
    formats = available_formats()
    return {
        "items": [
            {
                "key": key,
                "label": spec["label"],
                "description": spec["description"],
                "filters": list(spec["filters"]),
                "formats": formats,
            }
            for key, spec in _REPORTS.items()
        ],
        "formats": formats,
        "max_rows": settings.report_max_rows,
        "pdf_status": "not_available",
    }


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value.lower()).strip("_")


def export_report(
    db: Session,
    organization_id: int,
    report: str,
    fmt: str,
    *,
    filters: dict | None = None,
) -> ExportResult:
    """Render one report for one organisation into the requested format."""
    key = str(report or "").strip().lower()
    spec = _REPORTS.get(key)
    if spec is None:
        raise ValidationError(
            f"Unknown report '{report}'. Available: {', '.join(sorted(_REPORTS))}."
        )
    extension = str(fmt or "").strip().lower()
    formats = available_formats()
    if extension not in formats:
        raise ValidationError(f"Unsupported format '{fmt}'. Available: {', '.join(formats)}.")

    columns, rows = spec["builder"](db, organization_id, dict(filters or {}))
    rows, truncated = _cap(list(rows), settings.report_max_rows)
    generated_at = datetime.now(timezone.utc)
    meta = {
        "report": key,
        "label": spec["label"],
        "organization_id": organization_id,
        "generated_at": generated_at.isoformat(),
        "row_count": len(rows),
        "truncated": truncated,
        "max_rows": settings.report_max_rows,
    }

    if extension == "csv":
        content = _csv_bytes(columns, rows)
        media_type = "text/csv; charset=utf-8"
    elif extension == "xlsx":
        content = _xlsx_bytes(spec["label"], columns, rows, meta)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        content = _json_bytes({**meta, "columns": columns, "rows": rows})
        media_type = "application/json"

    return ExportResult(
        content=content,
        filename=f"{_slug(key)}_org{organization_id}_{generated_at.strftime('%Y%m%dT%H%M%SZ')}.{extension}",
        media_type=media_type,
        columns=columns,
        row_count=len(rows),
        truncated=truncated,
        meta=meta,
    )
