"""Natural-language to SQL over the tenant-safe schema.

Two paths, both ending in the same guard chain:

1. **Intent templates** (default, no LLM needed): a small library of curated,
   pre-scoped SQL statements behind keyword matching. This keeps the feature
   useful and deterministic in every environment, including offline demos.
2. **LLM generation**: when a real provider is configured, the model is given
   the curated schema (`app.llm.sql_schema`) and asked for one SELECT.

Nothing is executed before `app.llm.sql_validator.apply_readonly_guards` has
enforced SELECT-only, table allow-listing, tenant scope, and a row cap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.llm.factory import get_provider
from app.llm.sql_schema import schema_prompt
from app.llm.sql_validator import apply_readonly_guards

MAX_RESULT_ROWS = 200
_STATEMENT_TIMEOUT_MS = 5000
_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Intent:
    """A curated question -> SQL template."""

    name: str
    keywords: tuple[str, ...]
    description: str
    sql: str
    question: str = ""


INTENTS: tuple[Intent, ...] = (
    Intent(
        name="customer_counts",
        keywords=("how many customers", "customer count", "total customers", "how many buyers", "number of customers"),
        description="Total number of customers in the workspace",
        question="How many customers do we have?",
        sql=("SELECT COUNT(*) AS customers FROM customers WHERE organization_id = {org}"),
    ),
    Intent(
        name="top_customers",
        keywords=(
            "top customer",
            "best customer",
            "highest value",
            "highest clv",
            "biggest spender",
            "who should we prioritize",
            "prioritize",
            "most valuable",
        ),
        description="Highest-value customers ranked by estimated CLV",
        question="Who are our most valuable customers?",
        sql=(
            "SELECT c.id AS customer_id, c.name, c.email, f.clv_est, f.monetary, f.frequency, f.recency_days "
            "FROM customer_features f JOIN customers c ON c.id = f.customer_id "
            "WHERE f.organization_id = {org} AND c.organization_id = {org} "
            "ORDER BY f.clv_est DESC LIMIT 20"
        ),
    ),
    Intent(
        name="at_risk_customers",
        keywords=("at risk", "churn", "risk", "churner", "about to leave", "likely to leave"),
        description="Customers scored HIGH or CRITICAL by the churn model",
        question="Which customers are at risk of churning?",
        sql=(
            "SELECT c.id AS customer_id, c.name, p.prediction_proba, p.risk_level, f.clv_est "
            "FROM predictions p JOIN customers c ON c.id = p.customer_id "
            "JOIN customer_features f ON f.customer_id = c.id "
            "WHERE p.organization_id = {org} AND c.organization_id = {org} AND f.organization_id = {org} "
            "AND p.risk_level IN ('HIGH', 'CRITICAL') "
            "ORDER BY p.prediction_proba DESC LIMIT 25"
        ),
    ),
    Intent(
        name="lapsed_customers",
        keywords=(
            "lapsed",
            "dormant",
            "inactive",
            "haven't purchased",
            "have not purchased",
            "no activity",
            "gone quiet",
            "stale",
        ),
        description="Customers with no purchase in the last 90+ days, most valuable first",
        question="Which customers have gone quiet?",
        sql=(
            "SELECT c.id AS customer_id, c.name, f.recency_days, f.frequency, f.monetary, f.clv_est "
            "FROM customer_features f JOIN customers c ON c.id = f.customer_id "
            "WHERE f.organization_id = {org} AND c.organization_id = {org} AND f.recency_days > 90 "
            "ORDER BY f.monetary DESC LIMIT 25"
        ),
    ),
    Intent(
        name="revenue_by_month",
        keywords=(
            "revenue by month",
            "monthly revenue",
            "revenue trend",
            "sales trend",
            "revenue over time",
            "monthly sales",
        ),
        description="Monthly order count and revenue",
        question="How has revenue trended month over month?",
        sql=(
            "SELECT substr(CAST(order_date AS VARCHAR), 1, 7) AS month, COUNT(*) AS orders, "
            "SUM(amount) AS revenue, AVG(amount) AS avg_order_value "
            "FROM transactions WHERE organization_id = {org} "
            "GROUP BY month ORDER BY month"
        ),
    ),
    Intent(
        name="category_performance",
        keywords=(
            "top product",
            "best selling",
            "best-selling",
            "product category",
            "by category",
            "top categories",
            "which product",
        ),
        description="Revenue and orders per product category",
        question="Which product categories perform best?",
        sql=(
            "SELECT pr.category, COUNT(*) AS orders, SUM(t.amount) AS revenue, "
            "SUM(t.quantity) AS units "
            "FROM transactions t JOIN products pr ON pr.id = t.product_id "
            "WHERE t.organization_id = {org} AND pr.organization_id = {org} "
            "GROUP BY pr.category ORDER BY revenue DESC"
        ),
    ),
    Intent(
        name="recent_orders",
        keywords=(
            "recent order",
            "latest order",
            "last order",
            "recent transaction",
            "newest order",
            "latest purchase",
        ),
        description="Most recent orders with customer and product",
        question="What are the most recent orders?",
        sql=(
            "SELECT c.name, t.external_order_id, t.order_date, pr.product_name, t.quantity, t.amount, t.channel "
            "FROM transactions t JOIN customers c ON c.id = t.customer_id "
            "JOIN products pr ON pr.id = t.product_id "
            "WHERE t.organization_id = {org} AND c.organization_id = {org} AND pr.organization_id = {org} "
            "ORDER BY t.order_date DESC LIMIT 25"
        ),
    ),
    Intent(
        name="rfm_mix",
        keywords=("rfm", "segment mix", "how are customers distributed", "customer distribution", "segment breakdown"),
        description="Customer count per RFM segment",
        question="How are our customers distributed across RFM segments?",
        sql=(
            "SELECT f.rfm_segment AS segment, COUNT(*) AS customers, AVG(f.monetary) AS avg_monetary "
            "FROM customer_features f WHERE f.organization_id = {org} "
            "GROUP BY segment ORDER BY customers DESC"
        ),
    ),
    Intent(
        name="retention_actions",
        keywords=("recommendation", "next best action", "retention action", "suggested action", "what should we offer"),
        description="Generated retention actions by priority",
        question="What retention actions are pending?",
        sql=(
            "SELECT r.priority, r.action, COUNT(*) AS customers, AVG(r.priority_score) AS avg_score "
            "FROM recommendations r WHERE r.organization_id = {org} AND r.type = 'RETENTION' "
            "GROUP BY r.priority, r.action ORDER BY avg_score DESC"
        ),
    ),
)


def match_intent(question: str) -> Intent | None:
    """Best keyword match for a question, or None when nothing is close enough."""
    text_q = question.lower()
    best: tuple[int, Intent] | None = None
    for intent in INTENTS:
        hits = sum(1 for keyword in intent.keywords if keyword in text_q)
        if hits and (best is None or hits > best[0]):
            best = (hits, intent)
    return best[1] if best else None


def sql_for_intent(intent: Intent, organization_id: int) -> str:
    return intent.sql.format(org=int(organization_id))


def extract_sql(raw: str) -> str:
    """Pull a single SELECT out of a model response (fences and prose tolerated)."""
    candidate = (raw or "").strip()
    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    lowered = candidate.lower()
    start = lowered.find("select")
    if start == -1:
        raise ValidationError("The model did not return a SQL query.")
    candidate = candidate[start:]
    end = candidate.find(";")
    if end != -1:
        candidate = candidate[:end]
    return candidate.strip()


def generate_sql(question: str, organization_id: int) -> tuple[str, str, str | None]:
    """Return (sql, source, intent) for a question, already tenant-scoped.

    Curated templates win when they match; otherwise a configured LLM is asked.
    """
    intent = match_intent(question)
    if intent is not None:
        return sql_for_intent(intent, organization_id), f"template:{intent.name}", intent.name
    provider = get_provider()
    if provider.name == "mock":
        raise ValidationError(
            "No configured LLM provider for open-ended questions. "
            f"Supported intents: {', '.join(i.name for i in INTENTS)}."
        )
    prompt = (
        f"{schema_prompt()}\n\n"
        f"Business question: {question}\n\n"
        "Return one SELECT statement for this question."
    )
    try:
        raw = provider.complete("You are a precise SQL translator. Output SQL only.", prompt).text
    except Exception as exc:  # noqa: BLE001 - provider failures degrade, never crash
        raise ValidationError(f"The language model could not be reached: {exc}") from exc
    return extract_sql(raw), f"llm:{provider.name}", None


def run_query(
    db: Session,
    organization_id: int,
    question: str,
    generated_sql: str | None = None,
) -> dict:
    """Validate, guard, execute and shape an analyst query.

    ``generated_sql`` lets a caller (or a human) supply SQL directly; it goes
    through the exact same guard chain as generated SQL.
    """
    if generated_sql and generated_sql.strip():
        sql, source, intent = generated_sql.strip(), "manual", None
    else:
        sql, source, intent = generate_sql(question, organization_id)
    guarded = apply_readonly_guards(sql, organization_id=organization_id, max_rows=MAX_RESULT_ROWS + 1)
    rows, truncated = _execute(db, guarded)
    return {
        "question": question,
        "sql": guarded,
        "executed": True,
        "intent": intent,
        "source": source,
        "row_count": len(rows),
        "truncated": truncated,
        "rows": rows,
        "note": "Tenant scope and row cap are enforced by the platform; the model cannot widen them.",
    }


def _execute(db: Session, sql: str) -> tuple[list[dict], bool]:
    try:
        _apply_statement_timeout(db)
        result = db.execute(text(sql))
        fetched = result.fetchall()
        columns = list(result.keys())
    except SQLAlchemyError as exc:
        db.rollback()
        raise ValidationError(f"The query could not be executed: {_short_error(exc)}") from exc
    records = [{col: _jsonable(row[idx]) for idx, col in enumerate(columns)} for row in fetched]
    if len(records) > MAX_RESULT_ROWS:
        return records[:MAX_RESULT_ROWS], True
    return records, False


def _apply_statement_timeout(db: Session) -> None:
    """Best-effort server-side timeout so a runaway query cannot pin a worker."""
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    try:
        db.execute(text(f"SET LOCAL statement_timeout = {_STATEMENT_TIMEOUT_MS}"))
    except SQLAlchemyError:  # pragma: no cover - defensive
        db.rollback()


def _short_error(exc: SQLAlchemyError) -> str:
    message = str(getattr(exc, "orig", exc))
    return " ".join(message.split())[:300]


def _jsonable(value):
    if value is None or isinstance(value, (int, float, bool, str)):
        return value
    return str(value)
