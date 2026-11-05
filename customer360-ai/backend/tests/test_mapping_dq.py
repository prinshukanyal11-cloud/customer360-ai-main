"""Schema registry + DQ engine unit tests (no DB)."""
from __future__ import annotations

import pandas as pd

from app.services.dq_engine import run_quality_checks
from app.services.schema_registry import infer_field, infer_mapping, normalize_alias


def test_normalize_alias():
    assert normalize_alias("Order ID ") == "order_id"
    assert normalize_alias("Order-Date") == "order_date"


def test_infer_mapping_customers_file():
    columns = ["customer_id", "name", "email", "city", "age", "signup_date", "acquisition_channel"]
    mapping = infer_mapping(columns)
    assert mapping["customers"]["external_customer_id"] == "customer_id"
    assert mapping["customers"]["location"] == "city"
    assert mapping["customers"]["age"] == "age"
    assert not mapping["transactions"]


def test_infer_mapping_transactions_file():
    columns = ["order_id", "customer_id", "order_date", "product_name", "category", "quantity", "amount", "discount", "channel"]
    mapping = infer_mapping(columns)
    assert mapping["transactions"]["external_order_id"] == "order_id"
    assert mapping["transactions"]["customer_id"] == "customer_id"
    assert mapping["transactions"]["order_date"] == "order_date"
    assert mapping["transactions"]["amount"] == "amount"
    # Ambiguous 'channel' resolves to transactions, not customers.acquisition_channel.
    assert mapping["transactions"]["channel"] == "channel"
    assert "acquisition_channel" not in mapping.get("customers", {})
    # Products entity is also recognized (product_name + category).
    assert mapping["products"]["product_name"] == "product_name"
    assert mapping["products"]["category"] == "category"
    # 'category' must NOT be swallowed by subcategory via substring matching.
    assert "subcategory" not in mapping["products"]


def test_infer_field():
    assert infer_field("CUST_ID") is not None
    assert infer_field("OrderDate") is not None
    assert infer_field("superfluous_column_xyz") is None


def test_dq_clean_data_scores_high():
    df = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C3"],
            "name": ["Ann", "Ben", "Cal"],
            "signup_date": ["2024-01-01", "2024-02-01", "2024-03-01"],
        }
    )
    mapping = {"customers": {"external_customer_id": "customer_id", "name": "name", "signup_date": "signup_date"}}
    report = run_quality_checks(df, mapping)
    assert report["blocked"] is False
    assert report["score"] >= 90


def test_dq_detects_missing_required():
    df = pd.DataFrame({"name": ["Ann"], "signup_date": ["2024-01-01"]})
    mapping = {"customers": {"name": "name", "signup_date": "signup_date"}}
    report = run_quality_checks(df, mapping)
    assert any(c["severity"] == "ERROR" and c["status"] == "FAIL" for c in report["checks"])
    assert report["blocked"] is True


def test_dq_detects_unparseable_and_duplicates():
    df = pd.DataFrame(
        {
            "order_id": ["O1", "O1", "O2"],
            "customer_id": ["C1", "C2", None],
            "order_date": ["2024-06-01", "not-a-date", "2024-07-01"],
            "amount": ["100.00", "₹99.50", "not-money"],
        }
    )
    mapping = {
        "transactions": {
            "external_order_id": "order_id",
            "customer_id": "customer_id",
            "order_date": "order_date",
            "amount": "amount",
        }
    }
    report = run_quality_checks(df, mapping)
    by_check = {c["check"]: c for c in report["checks"]}
    assert by_check["transactions.unique_key.duplicates"]["status"] == "WARN"
    assert by_check["transactions.order_date.format_date"]["status"] == "FAIL"
    assert by_check["transactions.amount.format_money"]["status"] == "FAIL"
    assert report["blocked"] is True
    # score reflects the failures (degraded well below a clean 100)
    assert report["score"] < 65


def test_dq_money_parse_with_symbols():
    from app.services.ingest_utils import coerce_money

    parsed, invalid = coerce_money(pd.Series(["₹1,299.00", "$12.50", "abc"]))
    assert parsed.tolist()[:2] == [1299.0, 12.5]
    assert invalid.tolist() == [False, False, True]