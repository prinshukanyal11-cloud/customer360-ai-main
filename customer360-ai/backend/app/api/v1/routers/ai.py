"""AI endpoints: grounded Retention Advisor and NL-to-SQL."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.constants import AuditAction
from app.core.security_deps import CurrentUser, DbSession
from app.llm.ai_analyst import answer, retrieve_context
from app.llm.nl2sql import run_query
from app.services import audit_service

router = APIRouter(prefix="/ai", tags=["ai"])


class HistoryTurn(BaseModel):
    question: str = Field(max_length=2000)
    answer: str = Field(default="", max_length=8000)


class AiQueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=12)


class NlToSqlRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    generated_sql: str | None = Field(default=None, max_length=4000)


@router.post("/query")
def ai_query(db: DbSession, user: CurrentUser, payload: AiQueryRequest) -> dict:
    """Answer a business question from retrieved, tenant-scoped context only."""
    result = answer(
        db,
        user.organization_id,
        payload.question,
        history=[turn.model_dump() for turn in payload.history],
    )
    audit_service.audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.AI_QUERY,
        entity_type="organization",
        entity_id=user.organization_id,
        metadata_json={"question": payload.question, "contexts": result["context_labels"]},
    )
    return result


@router.get("/context")
def ai_context(db: DbSession, user: CurrentUser, question: str = "overview") -> dict:
    """Inspect which context blocks a question would retrieve (transparency)."""
    return retrieve_context(db, user.organization_id, question)


@router.post("/nl-to-sql")
def nl_to_sql(db: DbSession, user: CurrentUser, payload: NlToSqlRequest) -> dict:
    """Generate (or accept), guard, and execute one tenant-scoped SELECT.

    `generated_sql` lets a caller submit SQL directly; it goes through the same
    guard chain as model output: SELECT-only, allowed tables, tenant scope, and
    a row cap. Nothing else can ever run.
    """
    result = run_query(
        db,
        user.organization_id,
        payload.question,
        generated_sql=payload.generated_sql or None,
    )
    audit_service.audit(
        db,
        user.organization_id,
        user.id,
        AuditAction.AI_NL_TO_SQL,
        entity_type="organization",
        entity_id=user.organization_id,
        metadata_json={
            "question": payload.question,
            "source": result["source"],
            "row_count": result["row_count"],
        },
    )
    return result
