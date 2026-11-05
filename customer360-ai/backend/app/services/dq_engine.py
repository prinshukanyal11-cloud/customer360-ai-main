"""Data quality engine: completeness, duplicates, format and logic checks.

Every check returns a structured row that the API and UI render as a report.
A failed ERROR check blocks processing; WARN/INFO do not.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import pandas as pd

from app.services.ingest_utils import coerce_date, coerce_int, coerce_money, strip_text, valid_email_mask
from app.services.schema_registry import (
    ENTITIES,
    Entity,
    get_field,
    required_fields,
)

SEVERITY_ERROR = "ERROR"
SEVERITY_WARN = "WARN"
SEVERITY_INFO = "INFO"

STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
STATUS_INFO = "INFO"

# Column-level tolerance before a completeness violation becomes an ERROR.
REQUIRED_NULL_TOLERANCE = 0.05

CATEGORY_COMPLETENESS = "completeness"
CATEGORY_DUPLICATES = "duplicates"
CATEGORY_FORMAT = "format"
CATEGORY_LOGIC = "logic"


@dataclass(frozen=True)
class CheckResult:
    check: str
    label: str
    category: str
    severity: str
    status: str
    affected: int
    total: int
    samples: list[str] | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["affected_pct"] = round(100 * (self.affected / self.total), 2) if self.total else 0.0
        return payload


def _text_violations(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    cleaned = series.map(strip_text)
    invalid = series.notna() & cleaned.isna()
    return cleaned, invalid


def run_quality_checks(df: pd.DataFrame, mapping: dict[str, dict[str, str]]) -> dict:
    """Execute DQ checks for every entity present in the mapping.

    mapping: {entity: {canonical_key: source_column}}.
    Returns a serializable report with score, checks, blocking flag and summary.
    """
    now = datetime.now(timezone.utc).isoformat()
    checks: list[dict] = []
    summary: dict[str, dict] = {}
    blocked = False

    for entity in ENTITIES:
        entity_mapping = mapping.get(entity) or {}
        if not entity_mapping:
            continue
        results = _checks_for_entity(df, entity, entity_mapping)
        errors = [r for r in results if r.severity == SEVERITY_ERROR and r.status == STATUS_FAIL]
        warns = [r for r in results if r.severity in (SEVERITY_WARN, SEVERITY_INFO) and r.status == STATUS_WARN]
        summary[entity] = {
            "checks": len(results),
            "errors": len(errors),
            "warnings": len(warns),
            "blocked": bool(errors),
        }
        blocked = blocked or bool(errors)
        checks.extend(r.to_dict() for r in results)

    total_checks = max(len(checks), 1)
    weighted = 0.0
    for check in checks:
        if check["status"] == STATUS_PASS:
            weighted += 1.0
        elif check["status"] == STATUS_WARN:
            weighted += 0.6
    score = round(100 * weighted / total_checks, 1)

    return {
        "score": score,
        "blocked": blocked,
        "checks": checks,
        "summary": summary,
        "generated_at": now,
    }


def _checks_for_entity(df: pd.DataFrame, entity: str, mapping: dict[str, str]) -> list[CheckResult]:
    cols = {key: mapping[key] for key in mapping if mapping[key] in df.columns}
    rows = len(df)
    results: list[CheckResult] = []

    # Completeness for required fields.
    for spec in required_fields(entity):
        column = cols.get(spec.key)
        if column is None:
            results.append(
                CheckResult(
                    check=f"{entity}.{spec.key}.missing",
                    label=f"Required column '{spec.label}' not mapped",
                    category=CATEGORY_COMPLETENESS,
                    severity=SEVERITY_ERROR,
                    status=STATUS_FAIL,
                    affected=rows,
                    total=rows,
                )
            )
            continue
        nulls = int(df[column].isna().sum())
        ratio = nulls / rows if rows else 0.0
        if ratio > REQUIRED_NULL_TOLERANCE:
            status, severity = STATUS_FAIL, SEVERITY_ERROR
        elif nulls:
            status, severity = STATUS_WARN, SEVERITY_WARN
        else:
            status, severity = STATUS_PASS, SEVERITY_ERROR
        results.append(
            CheckResult(
                check=f"{entity}.{spec.key}.completeness",
                label=f"{spec.label}: missing values",
                category=CATEGORY_COMPLETENESS,
                severity=severity,
                status=status,
                affected=nulls,
                total=rows,
                samples=_sample_invalid(df[column], df[column].isna(), 3),
            )
        )

    # Duplicates on the natural unique key.
    if entity == Entity.CUSTOMERS:
        unique_key = "external_customer_id"
    elif entity == Entity.PRODUCTS:
        unique_key = "external_product_id"
    else:
        unique_key = "external_order_id"
    key_col = cols.get(unique_key)
    # Skip when the entity has no explicit unique-id column (e.g. a product
    # file without a product_id), since a derived key would report noise.
    if key_col is not None:
        dup_count = int(df[key_col].dropna().duplicated(keep="first").sum())
        results.append(
            CheckResult(
                check=f"{entity}.unique_key.duplicates",
                label=f"Duplicate '{key_col}' values (last row wins)",
                category=CATEGORY_DUPLICATES,
                severity=SEVERITY_WARN,
                status=STATUS_WARN if dup_count else STATUS_PASS,
                affected=dup_count,
                total=rows,
                samples=_sample_invalid(
                    df[key_col],
                    df[key_col].dropna().duplicated(keep="first").reindex(df.index).fillna(False).astype(bool),
                    3,
                ),
            )
        )

    # Format / parseability per typed field.
    for key, column in cols.items():
        spec = get_field(key)
        if column not in df.columns:
            continue
        if spec.field_type == "email":
            invalid = ~valid_email_mask(df[column])
            results.append(
                CheckResult(
                    check=f"{entity}.{key}.format_email",
                    label=f"Invalid email format in '{column}'",
                    category=CATEGORY_FORMAT,
                    severity=SEVERITY_WARN,
                    status=STATUS_WARN if int(invalid.sum()) else STATUS_PASS,
                    affected=int(invalid.sum()),
                    total=rows,
                    samples=_sample_invalid(df[column], invalid, 3),
                )
            )
        elif spec.field_type == "date":
            _, invalid = coerce_date(df[column])
            results.append(
                CheckResult(
                    check=f"{entity}.{key}.format_date",
                    label=f"Unparseable dates in '{column}'",
                    category=CATEGORY_FORMAT,
                    severity=SEVERITY_ERROR if spec.required else SEVERITY_WARN,
                    status=STATUS_FAIL if int(invalid.sum()) else STATUS_PASS,
                    affected=int(invalid.sum()),
                    total=rows,
                    samples=_sample_invalid(df[column], invalid, 3),
                )
            )
        elif spec.field_type == "money":
            _, invalid = coerce_money(df[column])
            results.append(
                CheckResult(
                    check=f"{entity}.{key}.format_money",
                    label=f"Unparseable amounts in '{column}'",
                    category=CATEGORY_FORMAT,
                    severity=SEVERITY_ERROR if spec.required else SEVERITY_WARN,
                    status=STATUS_FAIL if int(invalid.sum()) else STATUS_PASS,
                    affected=int(invalid.sum()),
                    total=rows,
                    samples=_sample_invalid(df[column], invalid, 3),
                )
            )
        elif spec.field_type == "int":
            _, invalid = coerce_int(df[column])
            results.append(
                CheckResult(
                    check=f"{entity}.{key}.format_int",
                    label=f"Non-integer values in '{column}'",
                    category=CATEGORY_FORMAT,
                    severity=SEVERITY_WARN,
                    status=STATUS_WARN if int(invalid.sum()) else STATUS_PASS,
                    affected=int(invalid.sum()),
                    total=rows,
                    samples=_sample_invalid(df[column], invalid, 3),
                )
            )
        elif spec.field_type == "text":
            _, invalid = _text_violations(df[column])
            if int(invalid.sum()):
                results.append(
                    CheckResult(
                        check=f"{entity}.{key}.text_non_string",
                        label=f"Values in '{column}' are not plain text",
                        category=CATEGORY_FORMAT,
                        severity=SEVERITY_WARN,
                        status=STATUS_WARN,
                        affected=int(invalid.sum()),
                        total=rows,
                        samples=_sample_invalid(df[column], invalid, 3),
                    )
                )

    results.extend(_logic_checks(df, entity, cols, rows))
    return results


def _logic_checks(df: pd.DataFrame, entity: str, cols: dict[str, str], rows: int) -> list[CheckResult]:
    results: list[CheckResult] = []

    if entity == Entity.CUSTOMERS:
        col = cols.get("age")
        if col:
            parsed, _ = coerce_int(df[col])
            out_of_range = parsed.notna() & ((parsed < 0) | (parsed > 120))
            results.append(
                CheckResult(
                    check=f"{entity}.age.range",
                    label="Age out of range (0-120)",
                    category=CATEGORY_LOGIC,
                    severity=SEVERITY_WARN,
                    status=STATUS_WARN if int(out_of_range.sum()) else STATUS_PASS,
                    affected=int(out_of_range.sum()),
                    total=rows,
                    samples=_sample_invalid(df[col], out_of_range, 3),
                )
            )

    if entity == Entity.TRANSACTIONS:
        if "amount" in cols:
            parsed, _ = coerce_money(df[cols["amount"]])
            negative = parsed.notna() & (parsed < 0)
            results.append(
                CheckResult(
                    check=f"{entity}.amount.logic_negative",
                    label="Negative revenue values",
                    category=CATEGORY_LOGIC,
                    severity=SEVERITY_WARN,
                    status=STATUS_WARN if int(negative.sum()) else STATUS_PASS,
                    affected=int(negative.sum()),
                    total=rows,
                    samples=_sample_invalid(df[cols["amount"]], negative, 3),
                )
            )
        if "quantity" in cols:
            parsed, _ = coerce_int(df[cols["quantity"]])
            zero = parsed.notna() & (parsed < 1)
            results.append(
                CheckResult(
                    check=f"{entity}.quantity.logic_zero",
                    label="Quantity below 1",
                    category=CATEGORY_LOGIC,
                    severity=SEVERITY_WARN,
                    status=STATUS_WARN if int(zero.sum()) else STATUS_PASS,
                    affected=int(zero.sum()),
                    total=rows,
                    samples=_sample_invalid(df[cols["quantity"]], zero, 3),
                )
            )
    return results


def _text_violations(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    cleaned = series.map(strip_text)
    invalid = series.notna() & cleaned.isna()
    return cleaned, invalid


def _sample_invalid(series: pd.Series, mask: pd.Series, limit: int) -> list[str]:
    mask = mask.reindex(series.index).fillna(False).astype(bool)
    return [str(v) for v in series[mask].astype(str).head(limit).tolist()]