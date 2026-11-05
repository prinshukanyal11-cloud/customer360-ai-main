"""Audit logging helper (writes AuditLog rows). Never log PII or secrets."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.campaign import AuditLog


def audit(
    db: Session,
    organization_id: int | None,
    user_id: int | None,
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    metadata_json: dict | None = None,
) -> None:
    entry = AuditLog(
        organization_id=organization_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=ip,
        user_agent=user_agent,
        metadata_json=metadata_json,
        created_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 — audit failure must not break the request
        db.rollback()