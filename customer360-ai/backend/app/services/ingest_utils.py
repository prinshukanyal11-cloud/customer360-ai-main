"""Shared parsing/coercion helpers for ingestion (no DB or web dependencies).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MONEY_STRIP_RE = re.compile(r"[^\d.\-]")


def strip_text(value: object) -> object:
    if pd.isna(value):
        return None
    cleaned = str(value).strip()
    return cleaned or None


def valid_email_mask(series: pd.Series) -> pd.Series:
    return series.astype("string").map(lambda v: bool(EMAIL_RE.match(v)) if pd.notna(v) and v else False)


def parse_money_value(value: object) -> float | None:
    """Parse 'Rs 1,299.00', '₹1499', '$12.5', 1299.0 -> float. Unparseable -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if not pd.isna(value) else None
    cleaned = _MONEY_STRIP_RE.sub("", str(value))
    if not cleaned or cleaned.count(".") > 1 or cleaned in ("-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def coerce_money(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (parsed float series, invalid mask) where invalid = non-null input we could not parse."""
    parsed = series.map(parse_money_value)
    invalid = series.notna() & parsed.isna()
    return parsed, invalid


def coerce_date(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (datetime series, invalid mask). Datetimes are timezone-naive."""
    parsed = pd.to_datetime(series, errors="coerce")
    invalid = series.notna() & parsed.isna()
    return parsed, invalid


def coerce_int(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (integer series as best-effort, invalid mask where a non-null value isn't integral)."""
    numeric = pd.to_numeric(series, errors="coerce")
    integral = np.isclose(numeric % 1, 0, rtol=0, atol=1e-9) | pd.isna(numeric)
    invalid = series.notna() & (~integral)
    coerced = np.where(pd.isna(numeric) | (~integral), np.nan, numeric)
    return pd.Series(coerced, index=series.index, dtype="Int64"), invalid


def integer_or_none(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isclose(as_float % 1, 0, rtol=0, atol=1e-9):
        return None
    if np.isnan(as_float) or np.isinf(as_float):
        return None
    return int(as_float)