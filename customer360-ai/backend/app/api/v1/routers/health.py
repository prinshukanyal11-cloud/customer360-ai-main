"""Health and readiness endpoints (observability)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security_deps import DbSession
from app.schemas.schemas import HealthCheck

router = APIRouter(tags=["health"])

APP_VERSION = "0.1.0"


def _check_db(db: Session) -> str:
    try:
        db.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


def _check_redis() -> str:
    try:
        import redis

        from app.core.config import settings

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        return "ok"
    except Exception:  # noqa: BLE001
        return "unavailable"


@router.get("/health", response_model=HealthCheck, include_in_schema=False)
def health(db: DbSession) -> HealthCheck:
    from app.core.config import settings

    db_status = _check_db(db)
    redis_status = _check_redis() if settings.redis_url else "unavailable"
    overall = "ok" if db_status == "ok" else "degraded"
    return HealthCheck(
        status=overall,
        components={"app": "ok", "database": db_status, "redis": redis_status},
        version=APP_VERSION,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/ready", response_model=HealthCheck, include_in_schema=False)
def ready(db: DbSession) -> HealthCheck:
    return health(db)