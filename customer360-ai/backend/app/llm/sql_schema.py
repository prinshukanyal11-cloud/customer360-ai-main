"""Curated schema exposed to the LLM for NL-to-SQL.

Only the tenant-safe, business-meaningful tables are described. The prompt
context is the *first* defence (the model should ask the right question);
`app.llm.sql_validator` is the second (it refuses anything unscoped).
"""
from __future__ import annotations

from app.llm.sql_validator import ALLOWED_TABLES

SCHEMA_CONTEXT: dict[str, dict] = {
    "customers": {
        "description": "One row per buyer. Denormalized customer master record.",
        "columns": {
            "id": "internal primary key (join key)",
            "external_customer_id": "source system customer id",
            "name": "display name",
            "email": "email address",
            "city": "location city",
            "age": "age in years",
            "signup_date": "acquisition date",
            "acquisition_channel": "web | app | partner | other",
            "organization_id": "tenant id (always filtered automatically)",
        },
    },
    "products": {
        "description": "Product catalog.",
        "columns": {
            "id": "internal primary key (join key)",
            "external_product_id": "source system product id",
            "product_name": "product name",
            "category": "product category",
            "subcategory": "product subcategory",
            "unit_price": "list price",
        },
    },
    "transactions": {
        "description": "Order lines. Exactly one product per order row.",
        "columns": {
            "id": "internal primary key",
            "customer_id": "FK -> customers.id",
            "external_order_id": "source order id",
            "order_date": "order date",
            "product_id": "FK -> products.id",
            "quantity": "units ordered",
            "amount": "line revenue (already net)",
            "discount": "discount amount",
            "channel": "web | app | store | partner",
        },
    },
    "customer_features": {
        "description": (
            "Engineered features per customer per dataset version. The newest row per "
            "customer is the current one (use MAX(dataset_version_id) per customer)."
        ),
        "columns": {
            "customer_id": "FK -> customers.id",
            "dataset_version_id": "feature snapshot id (higher = newer)",
            "recency_days": "days since last purchase",
            "frequency": "distinct purchase months",
            "monetary": "lifetime revenue",
            "rfm_score": "champion | loyal | potential | at_risk | lost (or numeric score)",
            "rfm_segment": "RFM segment label",
            "aov": "average order value",
            "avg_days_between": "mean days between orders",
            "tenure_days": "days since first purchase",
            "category_diversity": "distinct categories bought",
            "discount_usage": "share of orders using a discount",
            "engagement_score": "0-1 engagement index",
            "clv_est": "estimated customer lifetime value",
        },
    },
    "segments": {
        "description": "RFM or ML segment definitions.",
        "columns": {
            "id": "segment id",
            "type": "RFM | ML",
            "name": "segment name",
            "description": "what the segment means",
            "active": "boolean",
        },
    },
    "customer_segments": {
        "description": "Membership of a customer in a segment.",
        "columns": {
            "customer_id": "FK -> customers.id",
            "segment_id": "FK -> segments.id",
            "assigned_at": "assignment timestamp",
            "confidence": "0-1 assignment confidence",
        },
    },
    "predictions": {
        "description": "Churn model output per customer per model version.",
        "columns": {
            "customer_id": "FK -> customers.id",
            "model_version_id": "scoring run id (higher = newer)",
            "prediction_label": "true = predicted churner",
            "prediction_proba": "churn probability 0-1",
            "risk_level": "LOW | MEDIUM | HIGH | CRITICAL",
            "inferred_at": "scoring timestamp",
        },
    },
    "recommendations": {
        "description": "Retention next-best-action rows generated from churn output.",
        "columns": {
            "customer_id": "FK -> customers.id",
            "type": "RETENTION for the retention playbook",
            "action": (
                "win_back | concierge_save | cycle_reduce | loyal | reengagement | "
                "cross_sell | subscription | nourish"
            ),
            "priority": "HIGH | MEDIUM | LOW",
            "priority_score": "0-100 ranking score",
            "confidence": "0-1 rule confidence",
            "reason": "human readable justification",
        },
    },
}

RULES = (
    "Rules you MUST follow:\n"
    "1. Output a single SELECT statement only. No INSERT/UPDATE/DELETE/DDL, no semicolons, no comments.\n"
    "2. Use only the tables and columns listed above. Never reference information_schema or system tables.\n"
    "3. No subqueries. Aggregate with GROUP BY instead.\n"
    "4. Never filter or select organization_id yourself; the platform injects the tenant filter.\n"
    "5. Include ORDER BY for ranked questions and GROUP BY for counts/sums per group.\n"
    "6. Keep the result small (a few hundred rows at most).\n"
    "7. Reply with the SQL only - no prose, no markdown fences."
)


def schema_prompt() -> str:
    """Render the curated schema + rules for the NL-to-SQL system prompt."""
    lines: list[str] = ["Available schema (all tables are multi-tenant):"]
    for table, spec in SCHEMA_CONTEXT.items():
        lines.append(f"\n{table} - {spec['description']}")
        for column, comment in spec["columns"].items():
            lines.append(f"  {column}: {comment}")
    lines.append("")
    lines.append(RULES)
    return "\n".join(lines)


def allowed_tables() -> frozenset[str]:
    return ALLOWED_TABLES
