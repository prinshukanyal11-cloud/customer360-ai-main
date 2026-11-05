"""Drift metric tests: PSI/KS behaviour, binning, and honest failure modes."""
from __future__ import annotations

import numpy as np
import pytest

from app.core.constants import DriftStatus
from app.services import drift_metrics as dm

THRESHOLDS = {"MODERATE": 0.10, "SIGNIFICANT": 0.25}


def _normal(size: int, loc: float = 0.0, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).normal(loc=loc, size=size)


# ------------------------------------------------------------------- PSI / KS


def test_identical_distributions_are_stable():
    reference = _normal(2000, seed=1)
    current = _normal(2000, seed=2)
    psi_value = dm.psi(reference, current, bins=10)
    assert psi_value == pytest.approx(0.0, abs=0.02)
    assert dm.classify(psi_value, THRESHOLDS) == DriftStatus.STABLE
    assert dm.ks_statistic(reference, current) < 0.06


def test_location_shift_is_significant():
    reference = _normal(2000, seed=3)
    current = _normal(2000, loc=1.5, seed=4)
    assert dm.psi(reference, current) >= 0.25
    assert dm.classify(dm.psi(reference, current), THRESHOLDS) == DriftStatus.SIGNIFICANT


def test_scale_change_is_detected():
    reference = _normal(2000, seed=5)
    current = _normal(2000, seed=6) * 3
    assert dm.psi(reference, current) >= 0.25


def test_psi_magnitude_is_roughly_direction_independent():
    """PSI is not exactly symmetric: bins come from the *reference* quantiles.

    Swapping the sides therefore re-bins, so only the magnitude (not the exact
    value) is expected to match. The verdict class must not depend on direction.
    """
    reference = _normal(2000, seed=7)
    current = _normal(2000, loc=1.0, seed=8)
    forward = dm.psi(reference, current)
    backward = dm.psi(current, reference)
    assert forward == pytest.approx(backward, rel=0.05)
    assert dm.classify(forward, THRESHOLDS) == dm.classify(backward, THRESHOLDS)


def test_psi_is_zero_for_identical_input():
    values = _normal(500, seed=9)
    assert dm.psi(values, values) == pytest.approx(0.0, abs=1e-9)
    assert dm.ks_statistic(values, values) == pytest.approx(0.0, abs=1e-9)


def test_ks_ranks_with_psi():
    reference = _normal(2000, seed=10)
    mild = _normal(2000, loc=0.4, seed=11)
    severe = _normal(2000, loc=1.5, seed=12)
    assert dm.ks_statistic(reference, mild) < dm.ks_statistic(reference, severe)
    assert dm.psi(reference, mild) < dm.psi(reference, severe)


# ------------------------------------------------------------------- failures


def test_psi_returns_none_when_either_side_is_empty():
    assert dm.psi([], [1.0, 2.0]) is None
    assert dm.psi([1.0, 2.0], []) is None
    assert dm.psi([], []) is None
    assert dm.ks_statistic([], [1.0]) is None


def test_psi_returns_none_for_constant_reference():
    assert dm.psi([2.0] * 20, [1.0, 2.0, 3.0]) is None
    assert dm.bin_edges(np.array([5.0] * 10)) is None


def test_psi_ignores_none_and_non_finite_values():
    reference = [1.0, None, 2.0, float("nan"), 3.0, float("inf")]
    current = [1.0, 2.0, 3.0]
    value = dm.psi(reference, current)
    assert value is not None and value < 0.05


def test_missingness_is_tracked_separately_from_distribution():
    current = [1.0] * 10 + [None] * 30
    summary = dm.summarize(current)
    assert summary["n"] == 10
    assert summary["n_total"] == 40
    assert summary["missing"] == 30
    assert summary["missing_rate"] == pytest.approx(0.75)


def test_summarize_handles_all_missing_and_empty():
    assert dm.summarize([])["missing_rate"] is None
    all_none = dm.summarize([None, None])
    assert all_none["n"] == 0
    assert all_none["mean"] is None
    assert all_none["missing_rate"] == 1.0


def test_summarize_reports_central_tendency():
    summary = dm.summarize([1.0, 2.0, 3.0, 4.0])
    assert summary["mean"] == pytest.approx(2.5)
    assert summary["p50"] == pytest.approx(2.5)
    assert summary["min"] == 1.0
    assert summary["max"] == 4.0


# -------------------------------------------------------------------- binning


def test_effective_bins_scales_down_for_small_samples():
    assert dm.effective_bins(10_000, max_bins=10, min_per_bin=20) == 10
    assert dm.effective_bins(120, max_bins=10, min_per_bin=20) == 6
    assert dm.effective_bins(40, max_bins=10, min_per_bin=20) == 2
    assert dm.effective_bins(1, max_bins=10, min_per_bin=20) == 2
    assert dm.effective_bins(0, max_bins=10, min_per_bin=20) == 10


def test_adaptive_binning_reduces_small_sample_noise():
    rng = np.random.default_rng(13)
    reference = rng.normal(size=120)
    naive = [dm.psi(reference, rng.normal(size=120), bins=10) for _ in range(20)]
    adaptive = [
        dm.psi(reference, rng.normal(size=120), bins=dm.effective_bins(120, max_bins=10, min_per_bin=20))
        for _ in range(20)
    ]
    assert np.mean(adaptive) < np.mean(naive)


def test_bin_edges_use_reference_quantiles_with_open_sentinels():
    edges = dm.bin_edges(np.arange(100.0))
    assert edges[0] == -np.inf
    assert edges[-1] == np.inf
    assert np.all(np.diff(edges) > 0)


# ------------------------------------------------------- histogram round-trip


def test_histogram_baseline_reproduces_raw_psi_exactly():
    reference = _normal(600, seed=14)
    current = _normal(600, loc=0.8, seed=15)
    baseline = dm.serialize_histogram(reference, bins=10)
    assert dm.psi_against_histogram(current, baseline) == dm.psi(reference, current)


def test_serialize_histogram_shape_and_edge_cases():
    baseline = dm.serialize_histogram(_normal(400, seed=16), bins=10)
    assert len(baseline["proportions"]) == 10
    assert len(baseline["edges"]) == 9
    assert baseline["n"] == 400
    assert dm.serialize_histogram([1.0]) is None
    assert dm.serialize_histogram([7.0] * 50) is None


def test_psi_against_missing_or_broken_baseline_is_none():
    assert dm.psi_against_histogram([1.0, 2.0], None) is None
    assert dm.psi_against_histogram([1.0, 2.0], {}) is None
    assert dm.psi_against_histogram([1.0, 2.0], {"edges": [], "proportions": []}) is None


def test_psi_from_proportions_requires_matching_shapes():
    assert dm.psi_from_proportions([0.5, 0.5], [0.5, 0.5]) == pytest.approx(0.0, abs=1e-9)
    assert dm.psi_from_proportions([0.5, 0.5], [1.0]) is None
    assert dm.psi_from_proportions([], []) is None


def test_histogram_of_empty_values_is_flat():
    edges = np.array([-np.inf, 0.0, np.inf])
    assert list(dm.histogram([], edges)) == [0.0, 0.0]


# ------------------------------------------------------------------ verdicts


def test_classify_thresholds_are_inclusive_lower_bounds():
    assert dm.classify(0.0, THRESHOLDS) == DriftStatus.STABLE
    assert dm.classify(0.099, THRESHOLDS) == DriftStatus.STABLE
    assert dm.classify(0.10, THRESHOLDS) == DriftStatus.MODERATE
    assert dm.classify(0.249, THRESHOLDS) == DriftStatus.MODERATE
    assert dm.classify(0.25, THRESHOLDS) == DriftStatus.SIGNIFICANT
    assert dm.classify(5.0, THRESHOLDS) == DriftStatus.SIGNIFICANT


def test_classify_none_is_unknown():
    assert dm.classify(None, THRESHOLDS) == DriftStatus.UNKNOWN


def test_classify_respects_custom_thresholds():
    strict = {"MODERATE": 0.01, "SIGNIFICANT": 0.02}
    assert dm.classify(0.05, strict) == DriftStatus.SIGNIFICANT


def test_worst_rolls_up_to_most_severe():
    assert dm.worst([]) == DriftStatus.NO_DATA
    assert dm.worst([DriftStatus.STABLE, DriftStatus.MODERATE]) == DriftStatus.MODERATE
    assert dm.worst([DriftStatus.SIGNIFICANT, DriftStatus.STABLE]) == DriftStatus.SIGNIFICANT
    # Uncertainty never masks a real finding, and never outranks a clean verdict.
    assert dm.worst([DriftStatus.UNKNOWN, DriftStatus.STABLE]) == DriftStatus.STABLE
    assert dm.worst([DriftStatus.NO_DATA, DriftStatus.SIGNIFICANT]) == DriftStatus.SIGNIFICANT


def test_is_drifted_flags_moderate_and_above():
    assert dm.is_drifted(DriftStatus.MODERATE) is True
    assert dm.is_drifted(DriftStatus.SIGNIFICANT) is True
    assert dm.is_drifted(DriftStatus.STABLE) is False
    assert dm.is_drifted(DriftStatus.UNKNOWN) is False
    assert dm.is_drifted(DriftStatus.NO_DATA) is False
