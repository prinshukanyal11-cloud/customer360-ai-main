"""Experiment statistics for campaign A/B tests.

Deliberately dependency-free: the numbers reported to a business user must be
reproducible from the standard library alone (no hidden scipy/numpy versions).
Everything here is a textbook closed-form approximation, and each function
documents its own assumptions so the caveats travel with the result.
"""
from __future__ import annotations

import math

Z_95 = 1.959964
Z_90 = 1.644854
Z_99 = 2.575829


def normal_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def two_sided_p_value(z: float) -> float:
    """Two-tailed p-value for a z statistic."""
    if z != z:  # NaN
        return 1.0
    return max(0.0, min(1.0, 2.0 * (1.0 - normal_cdf(abs(z)))))


def critical_value(confidence: float) -> float:
    """Two-sided critical z for a confidence level (0.9 / 0.95 / 0.99)."""
    return {0.90: Z_90, 0.95: Z_95, 0.99: Z_99}.get(round(confidence, 2), Z_95)


def wilson_interval(successes: int, trials: int, *, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a proportion - behaves at 0% and 100%."""
    if trials <= 0:
        return (0.0, 0.0)
    z = critical_value(confidence)
    phat = successes / trials
    denominator = 1 + z * z / trials
    center = (phat + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * trials)) / trials) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def two_proportion_test(
    successes_a: int,
    trials_a: int,
    successes_b: int,
    trials_b: int,
    *,
    confidence: float = 0.95,
) -> dict:
    """Two-proportion z-test comparing arm A (control) with arm B (treatment).

    Assumes independent samples and normal approximation, which is appropriate
    once each arm has a few dozen observations - `required_sample_size` reports
    how many that would be, so a "not significant" verdict can be read for what
    it is: not enough data yet.
    """
    if trials_a <= 0 or trials_b <= 0:
        return {
            "test": "two_proportion_z",
            "significant": False,
            "p_value": 1.0,
            "z": None,
            "reason": "Both arms need observations before the arms can be compared.",
            "control": _arm(successes_a, trials_a, confidence),
            "treatment": _arm(successes_b, trials_b, confidence),
            "lift_absolute": None,
            "lift_relative": None,
            "confidence_level": confidence,
            "required_sample_size": None,
        }
    rate_a = successes_a / trials_a
    rate_b = successes_b / trials_b
    pooled = (successes_a + successes_b) / (trials_a + trials_b)
    standard_error = math.sqrt(pooled * (1 - pooled) * (1 / trials_a + 1 / trials_b))
    if standard_error == 0:
        z = None
        p_value = 1.0
    else:
        z = (rate_b - rate_a) / standard_error
        p_value = two_sided_p_value(z)
    z_crit = critical_value(confidence)
    half_width = z_crit * math.sqrt(
        rate_a * (1 - rate_a) / trials_a + rate_b * (1 - rate_b) / trials_b
    )
    difference = rate_b - rate_a
    lift_relative = (difference / rate_a) if rate_a > 0 else None
    return {
        "test": "two_proportion_z",
        "significant": p_value < (1 - confidence),
        "p_value": round(p_value, 4),
        "z": round(z, 4) if z is not None else None,
        "confidence_level": confidence,
        "confidence_interval": [
            round(difference - half_width, 4),
            round(difference + half_width, 4),
        ],
        "control": _arm(successes_a, trials_a, confidence),
        "treatment": _arm(successes_b, trials_b, confidence),
        "lift_absolute": round(difference, 4),
        "lift_relative": round(lift_relative, 4) if lift_relative is not None else None,
        "required_sample_size": required_sample_size(successes_a, trials_a, successes_b, trials_b, confidence),
        "verdict": _verdict(p_value, confidence, difference),
    }


def _arm(successes: int, trials: int, confidence: float) -> dict:
    low, high = wilson_interval(successes, trials, confidence=confidence)
    return {
        "successes": successes,
        "trials": trials,
        "rate": round(successes / trials, 4) if trials > 0 else 0.0,
        "ci_low": round(low, 4),
        "ci_high": round(high, 4),
    }


def _verdict(p_value: float, confidence: float, difference: float) -> str:
    if p_value >= (1 - confidence):
        return (
            f"No significant difference at {int(confidence * 100)}% confidence - "
            "treat the arms as equivalent (or wait for more data)."
        )
    if difference > 0:
        return f"Treatment wins by {(difference * 100):.2f}pp at {int(confidence * 100)}% confidence."
    return f"Control wins by {abs(difference) * 100:.2f}pp at {int(confidence * 100)}% confidence."


def required_sample_size(
    successes_a: int,
    trials_a: int,
    successes_b: int,
    trials_b: int,
    confidence: float = 0.95,
    power: float = 0.8,
) -> int | None:
    """Per-arm sample size needed to detect the observed effect at this power.

    Standard two-proportion power formula; returns None when the observed
    effect is zero (no finite sample size would detect it).
    """
    rate_a = successes_a / trials_a if trials_a else 0.0
    rate_b = successes_b / trials_b if trials_b else 0.0
    effect = abs(rate_b - rate_a)
    if effect == 0:
        return None
    pooled = (rate_a + rate_b) / 2
    z_alpha = critical_value(confidence)
    z_beta = {0.8: 0.8416, 0.9: 1.2816}.get(round(power, 2), 0.8416)
    numerator = (z_alpha * math.sqrt(2 * pooled * (1 - pooled)) + z_beta * math.sqrt(
        rate_a * (1 - rate_a) + rate_b * (1 - rate_b)
    )) ** 2
    return int(math.ceil(numerator / (effect * effect)))


def binomial_range(trials: int, rate: float, *, confidence: float = 0.95) -> tuple[int, int]:
    """Expected range of successes at a given rate - used for forecast bands."""
    if trials <= 0:
        return (0, 0)
    mean = trials * rate
    spread = critical_value(confidence) * math.sqrt(trials * rate * (1 - rate))
    return (max(0, int(mean - spread)), min(trials, int(mean + spread)))
