"""Processing pipeline: apply column mapping, clean and persist canonical rows.

Phase 2 core: given a mapped dataset version, this module
- applies coercion/normalization per canonical field type,
- deduplicates on natural keys,
- idempotently upserts customers / products / transactions,
- records the DQ report and dataset status on the version.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models.dataset import Dataset, DatasetVersion
from app.models.transaction import Customer, Product, Transaction
from app.services.dq_engine import run_quality_checks
from app.services.ingest_utils import (
    coerce_date,
    coerce_int,
    coerce_money,
    integer_or_none,
    strip_text,
    valid_email_mask,
)
from app.services.schema_registry import (
    ENTITIES,
    Entity,
    FieldType,
    get_field,
    infer_mapping,
    required_fields,
)

BATCH_SIZE = 1000


class DatasetNotFoundError(ValidationError):
    code = "DATASET_NOT_FOUND"
    message = "Dataset not found."


def ensure_valid_mapping(mapping: dict[str, dict[str, str]], columns: list[str]) -> dict[str, dict[str, str]]:
    """Validate a user-provided mapping against canonical fields and file columns.

    Raises ValidationError (COLUMN_MAPPING_ERROR) when a column is unknown,
    a field is unknown, a required source column is missing, or a column is
    mapped twice. Returns the mapping (only entities with >= 1 mapped field).
    """
    column_set = set(columns)
    claimed: set[str] = set()
    normalized: dict[str, dict[str, str]] = {}
    for entity in ENTITIES:
        entity_map = mapping.get(entity) or {}
        entity_map = {key: value for key, value in entity_map.items() if value}
        if not entity_map:
            continue
        for key, column in entity_map.items():
            try:
                spec = get_field(key)
            except KeyError:
                raise ValidationError(
                    f"Unknown canonical field '{entity}.{key}'.",
                    details={"field": key, "entity": entity},
                ) from None
            if spec.entity != entity:
                raise ValidationError(
                    f"Field '{key}' belongs to entities '{spec.entity}', not '{entity}'.",
                    details={"field": key},
                ) from None
            if column not in column_set:
                raise ValidationError(
                    f"Column '{column}' does not exist in the uploaded file.",
                    details={"column": column},
                )
            if column in claimed:
                raise ValidationError(
                    f"Column '{column}' is mapped to more than one field.",
                    details={"column": column},
                )
            claimed.add(column)
            normalized.setdefault(entity, {})[key] = column
    return normalized


def _iter_field_keys(mapping: dict[str, dict[str, str]]) -> list[str]:
    keys: list[str] = []
    for entity_map in mapping.values():
        keys.extend(entity_map.keys())
    return keys


def missing_required(mapping: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for entity in ENTITIES:
        entity_map = mapping.get(entity) or {}
        for spec in required_fields(entity):
            if spec.key not in entity_map:
                missing.setdefault(entity, []).append(spec.key)
    return missing


def representative_entities(mapping: dict[str, dict[str, str]]) -> list[str]:
    """Entities that will be processed (non-empty mapping, required fields covered)."""
    usable: list[str] = []
    for entity in ENTITIES:
        entity_map = mapping.get(entity) or {}
        if not entity_map:
            continue
        missing = [spec.key for spec in required_fields(entity) if spec.key not in entity_map]
        if not missing:
            usable.append(entity)
    return usable


def read_dataframe(path: str) -> pd.DataFrame:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "csv"
    if ext in ("xlsx", "xls"):
        return pd.read_excel(path)
    if ext == "parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, low_memory=False)


def apply_mapping_and_clean(df: pd.DataFrame, mapping: dict[str, dict[str, str]]) -> dict[str, pd.DataFrame]:
    """Map and clean the raw frame into canonical entity frames.

    Rows with a missing required identifier or unparseable required values are
    dropped. Email/text are stripped; phone/currency retained as text.
    """
    cleaned_entities: dict[str, pd.DataFrame] = {}

    if mapping.get(Entity.CUSTOMERS):
        cleaned_entities[Entity.CUSTOMERS] = _clean_customers(df, mapping[Entity.CUSTOMERS])
    if mapping.get(Entity.PRODUCTS):
        cleaned_entities[Entity.PRODUCTS] = _clean_products(df, mapping[Entity.PRODUCTS])
    if mapping.get(Entity.TRANSACTIONS):
        product_ref = None
        if mapping.get(Entity.PRODUCTS):
            product_ref = mapping[Entity.PRODUCTS].get("external_product_id") or mapping[Entity.PRODUCTS].get(
                "product_name"
            )
        cleaned_entities[Entity.TRANSACTIONS] = _clean_transactions(df, mapping[Entity.TRANSACTIONS], product_ref)
    return cleaned_entities


def _coerce_column(frame: pd.DataFrame, column: str, field_type: str) -> tuple[pd.Series, pd.Series]:
    """Return (coerced series, drop_mask) for a canonical field type."""
    src = frame[column]
    if field_type == FieldType.DATE:
        parsed, invalid = coerce_date(pd.Series(src))
        return parsed.dt.date.astype(object).where(parsed.notna()), invalid
    if field_type == FieldType.INTEGER:
        parsed, invalid = coerce_int(pd.Series(src))
        return parsed, invalid
    if field_type == FieldType.MONEY:
        parsed, invalid = coerce_money(pd.Series(src))
        return parsed, invalid
    if field_type == FieldType.EMAIL:
        invalid = ~valid_email_mask(pd.Series(src))
        return pd.Series(
            [v if ok else None for v, ok in zip(src, ~invalid, strict=True)], index=src.index
        ), invalid
    cleaned = pd.Series(src.map(strip_text), index=src.index)
    invalid = pd.Series(src.notna() & cleaned.isna(), index=src.index)
    return cleaned, invalid


def _drop_by_mask(frame: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    return frame[~mask.fillna(False).astype(bool)]


def _clean_customers(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    columns = {spec.key: mapping[spec.key] for spec in [get_field(k) for k in mapping] if mapping.get(spec.key)}
    spec_map = {spec.key: get_field(spec.key) for spec in [get_field(k) for k in mapping]}
    out = pd.DataFrame(index=df.index)
    drop = pd.Series(False, index=df.index)
    for key, column in columns.items():
        spec = spec_map[key]
        if spec.field_type == FieldType.ID:
            raw = df[column].map(strip_text)
            drop |= raw.isna()
            out[key] = raw
        else:
            coerced, invalid = _coerce_column(df, column, spec.field_type)
            if spec.required:
                out[key] = coerced
                drop |= invalid
            else:
                out[key] = coerced.where(pd.notna(coerced), None)
    return _drop_by_mask(out, drop)


def _clean_products(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    columns = {spec.key: mapping[spec.key] for spec in [get_field(k) for k in mapping] if mapping.get(spec.key)}
    spec_map = {spec.key: get_field(spec.key) for spec in [get_field(k) for k in mapping]}
    out = df.copy()[[]]
    drop = pd.Series(False, index=df.index)
    for key, column in columns.items():
        spec = spec_map[key]
        coerced, invalid = _coerce_column(df, column, spec.field_type)
        out[key] = coerced.where(pd.notna(coerced), None)
        if spec.required:
            drop |= coerced.isna() | invalid
    if "external_product_id" not in out.columns:
        out["external_product_id"] = out["product_name"]
    return _drop_by_mask(out, drop)


def _clean_transactions(
    df: pd.DataFrame,
    mapping: dict[str, str],
    product_ref: str | None = None,
) -> pd.DataFrame:
    spec_map = {spec.key: get_field(spec.key) for spec in [get_field(k) for k in mapping]}
    out = pd.DataFrame(index=df.index)
    drop = pd.Series(False, index=df.index)
    for key, column in mapping.items():
        spec = spec_map[key]
        if spec.field_type == FieldType.ID:
            raw = df[column].map(strip_text)
            if spec.required:
                drop |= raw.isna()
            out[key] = raw
        else:
            coerced, invalid = _coerce_column(df, column, spec.field_type)
            out[key] = coerced.where(pd.notna(coerced), None)
            if spec.required:
                drop |= invalid | coerced.isna()
    if "product_id" not in mapping and product_ref and product_ref in df.columns:
        # Link transactions to products inferred from the same file.
        out["product_id"] = df[product_ref].map(strip_text)
    return _drop_by_mask(out, drop)


def _to_money(value) -> Decimal | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return Decimal(str(round(float(value), 2)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def persist_processed_data(db: Session, organization_id: int, cleaned: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Upsert canonical rows (idempotent on natural keys) and return distinct counts."""
    counts: dict[str, int] = {"customers": 0, "products": 0, "transactions": 0}

    customer_ids: dict[str, int] = {}
    if Entity.CUSTOMERS in cleaned and not cleaned[Entity.CUSTOMERS].empty:
        frame = cleaned[Entity.CUSTOMERS].drop_duplicates(subset="external_customer_id", keep="last")
        customer_ids = _upsert_customers(db, organization_id, frame)
        counts[Entity.CUSTOMERS] = len(frame)

    product_ids: dict[str, int] = {}
    if Entity.PRODUCTS in cleaned and not cleaned[Entity.PRODUCTS].empty:
        frame = cleaned[Entity.PRODUCTS].drop_duplicates(subset="external_product_id", keep="last")
        product_ids = _upsert_products(db, organization_id, frame)
        counts[Entity.PRODUCTS] = len(frame)

    if Entity.TRANSACTIONS in cleaned and not cleaned[Entity.TRANSACTIONS].empty:
        frame = cleaned[Entity.TRANSACTIONS].drop_duplicates(subset="external_order_id", keep="last")
        counts[Entity.TRANSACTIONS] = _upsert_transactions(
            db, organization_id, frame, customer_ids, product_ids
        )
    return counts


def _id_map(db: Session, model: type, organization_id: int, ext_ids: set[str], key_column: str) -> dict[str, int]:
    if not ext_ids:
        return {}
    id_attribute = getattr(model, key_column)
    rows = db.execute(
        select(id_attribute, model.id).where(model.organization_id == organization_id, id_attribute.in_(ext_ids))
    ).all()
    return {ext: pk for ext, pk in rows}


def _upsert_customers(db: Session, organization_id: int, frame: pd.DataFrame) -> dict[str, int]:
    ext = frame["external_customer_id"].tolist()
    all_ids = {v for v in ext if v is not None}
    existing = _id_map(db, Customer, organization_id, all_ids, "external_customer_id")

    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            {
                "organization_id": organization_id,
                "external_customer_id": row.external_customer_id,
                "name": getattr(row, "name", None),
                "email": getattr(row, "email", None),
                "phone": getattr(row, "phone", None),
                "location": getattr(row, "location", None),
                "age": integer_or_none(getattr(row, "age", None)),
                "gender": getattr(row, "gender", None),
                "signup_date": getattr(row, "signup_date", None),
                "acquisition_channel": getattr(row, "acquisition_channel", None),
            }
        )
    update_fields = ["name", "email", "phone", "location", "age", "gender", "signup_date", "acquisition_channel"]
    _exec_upserts(
        db,
        Customer,
        rows,
        conflict=["organization_id", "external_customer_id"],
        update=update_fields,
    )
    new_ids = {v for v in all_ids if v not in existing}
    if new_ids:
        existing.update(_id_map(db, Customer, organization_id, new_ids, "external_customer_id"))
    return {external_id: pk for external_id, pk in existing.items() if external_id in all_ids}


def _upsert_products(db: Session, organization_id: int, frame: pd.DataFrame) -> dict[str, int]:
    all_ids = {str(v) for v in frame["external_product_id"].dropna().tolist()}
    existing = _id_map(db, Product, organization_id, all_ids, "external_product_id")
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            {
                "organization_id": organization_id,
                "external_product_id": str(row.external_product_id),
                "product_name": getattr(row, "product_name", None),
                "category": getattr(row, "category", None),
                "subcategory": getattr(row, "subcategory", None),
                "unit_price": _to_money(getattr(row, "unit_price", None)),
            }
        )
    _exec_upserts(
        db,
        Product,
        rows,
        conflict=["organization_id", "external_product_id"],
        update=["product_name", "category", "subcategory", "unit_price"],
    )
    remaining = {v for v in all_ids if v not in existing}
    if remaining:
        existing.update(_id_map(db, Product, organization_id, remaining, "external_product_id"))
    return {external_id: pk for external_id, pk in existing.items() if external_id in all_ids}


def _upsert_transactions(
    db: Session,
    organization_id: int,
    frame: pd.DataFrame,
    customer_ids: dict[str, int],
    product_ids: dict[str, int],
) -> int:
    # Auto-provision stub customers/products for references we have not seen.
    _guarantee_customer_stubs(db, organization_id, frame, customer_ids)
    _guarantee_product_stubs(db, organization_id, frame, product_ids)

    rows = []
    for row in frame.itertuples(index=False):
        cust_ext = getattr(row, "customer_id", None)
        cust_pk = customer_ids.get(cust_ext)
        prod_ext = getattr(row, "product_id", None)
        prod_pk = product_ids.get(prod_ext)
        rows.append(
            {
                "organization_id": organization_id,
                "customer_id": cust_pk,
                "product_id": prod_pk,
                "external_order_id": getattr(row, "external_order_id", None),
                "order_date": getattr(row, "order_date", None),
                "quantity": integer_or_none(getattr(row, "quantity", None)),
                "amount": _to_money(getattr(row, "amount", None)),
                "discount": _to_money(getattr(row, "discount", None)),
                "currency": getattr(row, "currency", None),
                "channel": getattr(row, "channel", None),
            }
        )
    rows = [r for r in rows if r["customer_id"] is not None and r["external_order_id"] and r["order_date"] is not None]
    _exec_upserts(
        db,
        Transaction,
        rows,
        conflict=["organization_id", "external_order_id"],
        update=["customer_id", "product_id", "order_date", "quantity", "amount", "discount", "currency", "channel"],
    )
    return len(rows)


def _guarantee_customer_stubs(
    db: Session, organization_id: int, frame: pd.DataFrame, customer_ids: dict[str, int]
) -> None:
    missing = {v for v in frame["customer_id"].dropna().tolist() if v not in customer_ids}
    if not missing:
        return
    rows = [{"organization_id": organization_id, "external_customer_id": v} for v in missing]
    _exec_upserts(db, Customer, rows, conflict=["organization_id", "external_customer_id"], update=[])
    customer_ids.update(_id_map(db, Customer, organization_id, missing, "external_customer_id"))


def _guarantee_product_stubs(
    db: Session, organization_id: int, frame: pd.DataFrame, product_ids: dict[str, int]
) -> None:
    if "product_id" not in frame.columns or frame["product_id"].isna().all():
        return
    missing = {v for v in frame["product_id"].dropna().tolist() if v not in product_ids}
    if not missing:
        return
    rows = [
        {
            "organization_id": organization_id,
            "external_product_id": v,
            "product_name": v,
            "category": None,
            "subcategory": None,
            "unit_price": None,
        }
        for v in missing
    ]
    _exec_upserts(db, Product, rows, conflict=["organization_id", "external_product_id"], update=["product_name"])
    product_ids.update(_id_map(db, Product, organization_id, missing, "external_product_id"))


def _dialect_insert(db: Session, model: type):
    if db.get_bind().dialect.name == "postgresql":
        return postgresql.insert(model)
    return sqlite.insert(model)


def _exec_upserts(
    db: Session,
    model: type,
    rows: list[dict],
    *,
    conflict: list[str],
    update: list[str],
) -> None:
    if not rows:
        return
    for start in range(0, len(rows), BATCH_SIZE):
        chunk = rows[start : start + BATCH_SIZE]
        statement = _dialect_insert(db, model).values(chunk)
        if update:
            statement = statement.on_conflict_do_update(
                index_elements=conflict,
                set_={field: getattr(statement.excluded, field) for field in update},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=conflict)
        db.execute(statement)
    db.flush()


def process_dataset_core(
    db: Session,
    dataset: Dataset,
    organization_id: int,
    *,
    force_validation: bool = False,
    version: DatasetVersion | None = None,
    build_features: bool = True,
) -> dict:
    """Run the full validation + cleaning + persist pipeline for a dataset.

    Safe to call once (HTTP sync fallback) or via the Celery task.
    Returns a serializable processing summary.
    """
    if version is None:
        version = (
            db.execute(
                select(DatasetVersion)
                .where(
                    DatasetVersion.dataset_id == dataset.id,
                    DatasetVersion.organization_id == organization_id,
                )
                .order_by(DatasetVersion.version_no.desc())
            )
            .scalars()
            .first()
        )
    if version is None or not version.file_path:
        raise DatasetNotFoundError()

    columns = (dataset.columns_json or {}).get("columns", [])
    stored = (version.column_mapping_json or {}).get("entities") if version.column_mapping_json else None
    mapping = stored if stored else infer_mapping(columns)

    eligible = representative_entities(mapping)
    if not eligible:
        missing = missing_required(mapping)
        raise ValidationError(
            "Required columns are not mapped. Map the dataset before processing.",
            details={"required_missing": missing},
        )

    df = read_dataframe(version.file_path)
    report = run_quality_checks(df, mapping)
    version.dq_score = report["score"]
    version.dq_report_json = report
    version.column_mapping_json = {"entities": mapping}
    if report["blocked"] and not force_validation:
        db.commit()
        raise ValidationError(
            "Data quality checks found blocking issues. Review and validate before processing.",
            details={"report": report},
        )

    cleaned = apply_mapping_and_clean(df, mapping)
    counts = persist_processed_data(db, organization_id, cleaned)

    dataset.status = "PROCESSED"
    dataset.total_rows = int(len(df))
    version.processed_at = datetime.now(UTC)

    summary = {
        "dataset_id": dataset.id,
        "status": "PROCESSED",
        "rows": int(len(df)),
        "entities_processed": eligible,
        "inserted": counts,
        "dq_score": report["score"],
        "report": report,
        "processed_at": datetime.now(UTC).isoformat(),
    }
    db.commit()

    from app.core.constants import AuditAction
    from app.services.audit_service import audit

    actor = version.created_by
    audit(
        db,
        organization_id,
        actor,
        AuditAction.DATASET_PROCESS,
        entity_type="dataset",
        entity_id=dataset.id,
        metadata_json={"rows": len(df), "inserted": counts, "dq_score": report["score"]},
    )

    summary["feature_summary"] = None
    if build_features:
        try:
            from app.services.feature_engineering import compute_features

            summary["feature_summary"] = compute_features(db, organization_id)
        except Exception:  # noqa: BLE001 — features must not fail the process step
            pass
    return summary