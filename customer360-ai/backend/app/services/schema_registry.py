"""Canonical schema registry and automatic column-mapping inference.

Phase 2: when a file is uploaded the backend must know how each source column
relates to the canonical warehouse schema (customers / products / transactions).
This module owns that knowledge and the suggestion logic. No DB access.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


class Entity:
    CUSTOMERS = "customers"
    PRODUCTS = "products"
    TRANSACTIONS = "transactions"


ENTITIES: tuple[str, ...] = (Entity.CUSTOMERS, Entity.PRODUCTS, Entity.TRANSACTIONS)


class FieldType:
    ID = "id"
    TEXT = "text"
    EMAIL = "email"
    DATE = "date"
    INTEGER = "int"
    MONEY = "money"
    FLOAT = "float"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    entity: str
    label: str
    field_type: str
    required: bool = False
    aliases: tuple[str, ...] = ()
    help: str = ""


CANONICAL_FIELDS: list[FieldSpec] = [
    # --- customers ---
    FieldSpec(
        key="external_customer_id",
        entity=Entity.CUSTOMERS,
        label="Customer ID",
        field_type=FieldType.ID,
        required=True,
        aliases=("customer_id", "cust_id", "customerid", "client_id", "account_id", "member_id"),
        help="Unique customer identifier in the source system.",
    ),
    FieldSpec(
        key="name",
        entity=Entity.CUSTOMERS,
        label="Full name",
        field_type=FieldType.TEXT,
        aliases=("name", "customer_name", "full_name", "contact_name"),
    ),
    FieldSpec(
        key="email",
        entity=Entity.CUSTOMERS,
        label="Email",
        field_type=FieldType.EMAIL,
        aliases=("email", "email_address", "mail", "customer_email"),
    ),
    FieldSpec(
        key="phone",
        entity=Entity.CUSTOMERS,
        label="Phone",
        field_type=FieldType.TEXT,
        aliases=("phone", "mobile", "phone_number", "phone_no", "contact"),
    ),
    FieldSpec(
        key="location",
        entity=Entity.CUSTOMERS,
        label="City / location",
        field_type=FieldType.TEXT,
        aliases=("city", "location", "town", "city_name"),
    ),
    FieldSpec(
        key="age",
        entity=Entity.CUSTOMERS,
        label="Age",
        field_type=FieldType.INTEGER,
        aliases=("age",),
    ),
    FieldSpec(
        key="gender",
        entity=Entity.CUSTOMERS,
        label="Gender",
        field_type=FieldType.TEXT,
        aliases=("gender", "sex"),
    ),
    FieldSpec(
        key="signup_date",
        entity=Entity.CUSTOMERS,
        label="Sign-up date",
        field_type=FieldType.DATE,
        aliases=("signup_date", "signup", "join_date", "joined_on", "joined_at", "registration_date", "customer_since"),
    ),
    FieldSpec(
        key="acquisition_channel",
        entity=Entity.CUSTOMERS,
        label="Acquisition channel",
        field_type=FieldType.TEXT,
        aliases=("acquisition_channel", "signup_channel", "source", "utm_source", "referral_source"),
    ),
    # --- products ---
    FieldSpec(
        key="external_product_id",
        entity=Entity.PRODUCTS,
        label="Product ID",
        field_type=FieldType.ID,
        aliases=("product_id", "sku", "product_code", "item_id", "item_code"),
        help="If absent, the product name is used as the identifier.",
    ),
    FieldSpec(
        key="product_name",
        entity=Entity.PRODUCTS,
        label="Product name",
        field_type=FieldType.TEXT,
        required=True,
        aliases=("product", "product_name", "item", "item_name", "title"),
    ),
    FieldSpec(
        key="category",
        entity=Entity.PRODUCTS,
        label="Category",
        field_type=FieldType.TEXT,
        aliases=("category", "product_category", "department"),
    ),
    FieldSpec(
        key="subcategory",
        entity=Entity.PRODUCTS,
        label="Subcategory",
        field_type=FieldType.TEXT,
        aliases=("subcategory", "sub_category", "product_subcategory"),
    ),
    FieldSpec(
        key="unit_price",
        entity=Entity.PRODUCTS,
        label="Unit price",
        field_type=FieldType.MONEY,
        aliases=("unit_price", "price", "list_price", "mrp"),
    ),
    # --- transactions ---
    FieldSpec(
        key="external_order_id",
        entity=Entity.TRANSACTIONS,
        label="Order / transaction ID",
        field_type=FieldType.ID,
        required=True,
        aliases=("order_id", "orderid", "invoice_id", "transaction_id", "order_number", "invoice_no"),
    ),
    FieldSpec(
        key="customer_id",
        entity=Entity.TRANSACTIONS,
        label="Customer ID (in transaction)",
        field_type=FieldType.ID,
        required=True,
        aliases=("customer_id", "cust_id", "customerid", "client_id", "account_id", "member_id"),
        help="References the canonical customer identifier.",
    ),
    FieldSpec(
        key="product_id",
        entity=Entity.TRANSACTIONS,
        label="Product ID (in transaction)",
        field_type=FieldType.ID,
        aliases=("product_id", "sku", "item_id"),
    ),
    FieldSpec(
        key="order_date",
        entity=Entity.TRANSACTIONS,
        label="Order date",
        field_type=FieldType.DATE,
        required=True,
        aliases=(
            "order_date",
            "date",
            "purchase_date",
            "transaction_date",
            "order_datetime",
            "created_at",
            "invoice_date",
        ),
    ),
    FieldSpec(
        key="quantity",
        entity=Entity.TRANSACTIONS,
        label="Quantity",
        field_type=FieldType.INTEGER,
        aliases=("quantity", "qty", "qty_ordered", "units", "qty_sold"),
    ),
    FieldSpec(
        key="amount",
        entity=Entity.TRANSACTIONS,
        label="Amount",
        field_type=FieldType.MONEY,
        required=True,
        aliases=("amount", "revenue", "total", "order_amount", "sales", "amount_paid", "gross"),
    ),
    FieldSpec(
        key="discount",
        entity=Entity.TRANSACTIONS,
        label="Discount",
        field_type=FieldType.MONEY,
        aliases=("discount", "discount_amount", "discount_value"),
    ),
    FieldSpec(
        key="currency",
        entity=Entity.TRANSACTIONS,
        label="Currency",
        field_type=FieldType.TEXT,
        aliases=("currency", "ccy", "currency_code"),
    ),
    FieldSpec(
        key="channel",
        entity=Entity.TRANSACTIONS,
        label="Channel",
        field_type=FieldType.TEXT,
        aliases=("channel", "order_channel", "purchase_channel", "sales_channel", "platform"),
    ),
]

def normalize_alias(value: str) -> str:
    """Normalize a column name / alias for matching.

    'Order ID ' -> 'order_id'; 'Order-Date' -> 'order_date'; 'OrderDate' -> 'order_date'.
    """
    split_camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip())
    chars = [c if c.isalnum() else "_" for c in split_camel.lower()]
    return "_".join(part for part in "".join(chars).split("_") if part)


_by_key: dict[str, FieldSpec] = {}
for _spec in CANONICAL_FIELDS:
    _by_key[_spec.key] = _spec


def get_field(key: str) -> FieldSpec:
    return _by_key[key]


def fields_for_entity(entity: str) -> list[FieldSpec]:
    return [spec for spec in CANONICAL_FIELDS if spec.entity == entity]


def required_fields(entity: str) -> list[FieldSpec]:
    return [spec for spec in fields_for_entity(entity) if spec.required]


def _tokenize(value: str) -> frozenset[str]:
    return frozenset(part for part in normalize_alias(value).split("_") if part)


def _alias_score(column: str, spec: FieldSpec) -> float:
    """Token-aware similarity between a source column and a canonical field.

    exact token set     -> 1.0
    field tokens subset -> 0.85
    column tokens subset-> 0.6
    otherwise           -> 0.0
    """
    column_tokens = _tokenize(column)
    best = 0.0
    for candidate in (spec.key, *spec.aliases):
        alias_tokens = _tokenize(candidate)
        if not column_tokens or not alias_tokens:
            continue
        if alias_tokens == column_tokens:
            best = max(best, 1.0)
        elif alias_tokens <= column_tokens:
            best = max(best, 0.85)
        elif column_tokens <= alias_tokens:
            best = max(best, 0.6)
    return best


def infer_field(column: str) -> FieldSpec | None:
    """Best-effort single-column classification."""
    best_spec: FieldSpec | None = None
    best_score = 0.0
    for spec in CANONICAL_FIELDS:
        score = _alias_score(column, spec)
        if score > best_score:
            best_score, best_spec = score, spec
    return best_spec


def _infer_primary_entity(candidates: dict[str, list[FieldSpec]]) -> str | None:
    """Choose the entity best represented by the available columns.

    Entity score = 3 * required fields matched + optional fields matched.
    """
    entity_scores: dict[str, float] = {e: 0.0 for e in ENTITIES}
    for specs in candidates.values():
        for spec in specs:
            entity_scores[spec.entity] += 3.0 if spec.required else 1.0
    return max(entity_scores, key=entity_scores.get) if any(entity_scores.values()) else None


def infer_mapping(columns: list[str]) -> dict[str, dict[str, str]]:
    """Suggest a per-entity mapping for the given source columns.

    Returns {entity: {canonical_key: source_column}}. Each column is assigned to
    at most one field; ties are broken toward the best-represented entity.
    """
    suggestions: dict[str, dict[str, str]] = {e: {} for e in ENTITIES}

    candidates: dict[str, list[FieldSpec]] = {}
    scored_per_column: dict[str, list[tuple[float, FieldSpec]]] = {}
    for column in columns:
        scored: list[tuple[float, FieldSpec]] = []
        for spec in CANONICAL_FIELDS:
            score = _alias_score(column, spec)
            if score > 0.0:
                scored.append((score, spec))
        if not scored:
            continue
        candidates[column] = [spec for _, spec in scored]
        scored_per_column[column] = scored

    primary = _infer_primary_entity(candidates)

    assignments: list[tuple[float, str, FieldSpec]] = []
    for column, scored in scored_per_column.items():
        top_score = max(score for score, _ in scored)
        top_specs = [spec for score, spec in scored if score == top_score]
        spec = _pick_spec(top_specs, primary)
        assignments.append((top_score, column, spec))

    assignments.sort(key=lambda item: (-item[0], item[2].entity != primary))

    claimed: set[str] = set()
    for _score, column, spec in assignments:
        if column in claimed:
            continue
        suggestions[spec.entity][spec.key] = column
        claimed.add(column)
    return suggestions


def _pick_spec(top_specs: list[FieldSpec], primary: str | None) -> FieldSpec:
    if primary is None or not top_specs:
        return top_specs[0]
    for spec in top_specs:
        if spec.entity == primary and spec.required:
            return spec
    for spec in top_specs:
        if spec.entity == primary:
            return spec
    return top_specs[0]


def mapping_summary(
    entities: dict[str, dict[str, str]],
    *,
    columns: list[str] | None = None,
) -> dict:
    """Rich summary of an entity mapping (used by map-columns / validate responses)."""
    result: dict[str, object] = {
        "entities": {e: dict(mapping) for e, mapping in entities.items() if mapping},
        "required_missing": {e: list(mapping) for e, mapping in _missing_required(entities).items()},
    }
    if columns is not None:
        claimed = {col for mapping in entities.values() for col in mapping.values()}
        result["unmapped_columns"] = [col for col in columns if col not in claimed]
    return result


def _missing_required(entities: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for entity in ENTITIES:
        mapping = entities.get(entity, {})
        for spec in required_fields(entity):
            if spec.key not in mapping:
                missing.setdefault(entity, []).append(spec.key)
    return missing