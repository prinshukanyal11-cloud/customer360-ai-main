"""Drift statistics.

Deliberately dependency-free (numpy only): the backend must be able to report
drift for a live model without pulling in scipy, and every function returns
`None` rather than a fabricated number when the inputs cannot support one.

Two complementary measures are provided:

* `psi` - Population Stability Index, binned on the *reference* quantiles.
  This is the headline metric and its thresholds come from the standard
  interpretation scale (see `settings.drift_psi_thresholds`).
* `ks_statistic` - two-sample Kolmogorov-Smirnov statistic. Binning-free, so it
  confirms (or contradicts) PSI rather than sharing its binning assumptions.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np

from app.core.constants import DriftStatus

EPSILON = 1e-6


def _finite(values: Iterable[Any] | np.ndarray | None) -> np.ndarray:
    """Coerce an iterable of DB floats/None into a 1-D float array without NaN/inf."""
    if values is None:
        return np.empty(0, dtype=float)
    array = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, dtype=float)
    if array.ndim == 0:
        array = array.reshape(1)
    return array[np.isfinite(array)]


def quantiles(values: np.ndarray) -> dict[str, float | None]:
    """Standard quantile summary used by the monitoring payloads."""
    if values.size == 0:
        return {key: None for key in ("p05", "p25", "p50", "p75", "p95")}
    p05, p25, p50, p75, p95 = np.quantile(values, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        "p05": round(float(p05), 6),
        "p25": round(float(p25), 6),
        "p50": round(float(p50), 6),
        "p75": round(float(p75), 6),
        "p95": round(float(p95), 6),
    }


def summarize(values: Iterable[Any] | np.ndarray | None) -> dict:
    """Distribution summary for one column, tracking missingness explicitly."""
    raw = list(values) if not isinstance(values, np.ndarray) else values
    finite = _finite(raw)
    total = len(raw)
    summary: dict[str, Any] = {
        "n": int(finite.size),
        "n_total": int(total),
        "missing": int(total - finite.size),
        "missing_rate": round((total - finite.size) / total, 6) if total else None,
        "mean": None,
        "std": None,
        "min": None,
        "max": None,
    }
    summary.update(quantiles(finite))
    if finite.size:
        summary["mean"] = round(float(finite.mean()), 6)
        summary["std"] = round(float(finite.std(ddof=0)), 6)
        summary["min"] = round(float(finite.min()), 6)
        summary["max"] = round(float(finite.max()), 6)
    return summary


def bin_edges(reference: np.ndarray, bins: int = 10) -> np.ndarray | None:
    """Quantile bin edges from the reference sample, with +/-inf sentinels.

    `None` means the reference is degenerate (a single distinct value), which
    makes a binned PSI meaningless rather than merely noisy.
    """
    if reference.size < 2:
        return None
    edges = np.unique(np.quantile(reference, np.linspace(0.0, 1.0, max(int(bins), 2) + 1)))
    if edges.size < 2:
        return None
    # Sentinels keep out-of-range current values inside the outer bins.
    return np.concatenate(([-np.inf], edges[1:-1], [np.inf]))


def effective_bins(n_reference: int, *, max_bins: int = 10, min_per_bin: int = 20) -> int:
    """Bin count that keeps at least `min_per_bin` reference rows in each bin.

    PSI is biased upwards on small samples because sparse bins amplify
    sampling noise, so the bin count is reduced rather than trusting a fixed
    grid. Floored at 2 bins so the metric always has some resolution.
    """
    if n_reference <= 0 or min_per_bin <= 0:
        return max(int(max_bins), 2)
    return max(2, min(int(max_bins), int(n_reference) // int(min_per_bin)))


def histogram(values: Iterable[Any] | np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Proportion of finite values falling in each bin defined by `edges`.

    Used to persist a compact baseline that a later scan can still compare
    against, without storing the raw observations.
    """
    data = _finite(values)
    if data.size == 0:
        return np.zeros(edges.size - 1, dtype=float)
    counts, _ = np.histogram(data, bins=edges)
    return counts / data.size


def psi_from_proportions(
    expected: Sequence[float], actual: Sequence[float], *, epsilon: float = EPSILON
) -> float | None:
    """PSI between two already-binned distributions sharing the same edges."""
    exp = np.asarray(expected, dtype=float)
    act = np.asarray(actual, dtype=float)
    if exp.size == 0 or exp.size != act.size:
        return None
    exp_share = np.clip(exp, epsilon, None)
    act_share = np.clip(act, epsilon, None)
    value = float(np.sum((act_share - exp_share) * np.log(act_share / exp_share)))
    return round(value, 6)


def serialize_histogram(values: Iterable[Any] | np.ndarray, bins: int = 10) -> dict | None:
    """Compact, JSON-safe baseline histogram. `None` if the sample is degenerate."""
    data = _finite(values)
    if data.size < 2:
        return None
    edges = bin_edges(data, bins)
    if edges is None:
        return None
    # Store only the interior edges; the +/-inf sentinels are implied.
    return {
        "edges": [float(edge) for edge in edges[1:-1]],
        "proportions": [round(float(p), 6) for p in histogram(data, edges)],
        "n": int(data.size),
    }


def psi_against_histogram(
    values: Iterable[Any] | np.ndarray, baseline: dict | None, *, bins: int = 10
) -> float | None:
    """PSI of `values` against a stored `serialize_histogram` baseline."""
    if not baseline:
        return None
    edges = np.asarray([-np.inf, *[float(e) for e in baseline.get("edges", [])], np.inf], dtype=float)
    expected = baseline.get("proportions")
    if not expected:
        return None
    return psi_from_proportions(expected, histogram(values, edges))


def psi(
    reference: Iterable[Any] | np.ndarray,
    current: Iterable[Any] | np.ndarray,
    *,
    bins: int = 10,
    epsilon: float = EPSILON,
) -> float | None:
    """Population Stability Index of `current` against `reference`.

    Returns `None` when either side has no usable values or the reference is
    constant, so callers can distinguish "no drift" from "cannot measure".
    """
    ref = _finite(reference)
    cur = _finite(current)
    if ref.size == 0 or cur.size == 0:
        return None
    edges = bin_edges(ref, bins)
    if edges is None:
        return None
    return psi_from_proportions(histogram(ref, edges), histogram(cur, edges), epsilon=epsilon)



def ks_statistic(reference: Iterable[Any] | np.ndarray, current: Iterable[Any] | np.ndarray) -> float | None:
    """Two-sample KS statistic (max absolute CDF gap). `None` if either side is empty."""
    ref = np.sort(_finite(reference))
    cur = np.sort(_finite(current))
    if ref.size == 0 or cur.size == 0:
        return None
    points = np.concatenate([ref, cur])
    ref_cdf = np.searchsorted(ref, points, side="right") / ref.size
    cur_cdf = np.searchsorted(cur, points, side="right") / cur.size
    return round(float(np.max(np.abs(ref_cdf - cur_cdf))), 6)


def classify(psi_value: float | None, thresholds: dict[str, float]) -> str:
    """Map a PSI value onto a DriftStatus using the configured thresholds."""
    if psi_value is None:
        return DriftStatus.UNKNOWN
    significant = float(thresholds.get("SIGNIFICANT", 0.25))
    moderate = float(thresholds.get("MODERATE", 0.10))
    if psi_value >= significant:
        return DriftStatus.SIGNIFICANT
    if psi_value >= moderate:
        return DriftStatus.MODERATE
    return DriftStatus.STABLE


# Severity ordering for rolling per-feature verdicts up into one model verdict.
_SEVERITY = {
    DriftStatus.NO_DATA: 0,
    DriftStatus.UNKNOWN: 1,
    DriftStatus.STABLE: 2,
    DriftStatus.MODERATE: 3,
    DriftStatus.SIGNIFICANT: 4,
}


def worst(statuses: Sequence[str]) -> str:
    """Roll a set of per-feature verdicts up into the most severe one."""
    if not statuses:
        return DriftStatus.NO_DATA
    return max(statuses, key=lambda status: _SEVERITY.get(status, 0))


def is_drifted(status: str) -> bool:
    """True when a verdict is worth surfacing to a user (moderate or worse)."""
    return status in (DriftStatus.MODERATE, DriftStatus.SIGNIFICANT)
