"""Dependency-light statistical primitives used by research adapters.

Everything here uses only the Python standard library. The Student-t routines
are independent numerical implementations: the CDF is computed from the
regularized incomplete beta function (continued-fraction evaluation) and the
inverse is solved by bisection on the transformed variable ``x = df / (df +
t**2)``. Reference values were generated offline with SciPy and are hard-coded
as fixture constants in the test suite; SciPy is never a runtime dependency.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence


def arithmetic_mean(values: Iterable[float]) -> float:
    """Return an arithmetic mean and reject an empty sample."""

    sample = tuple(values)
    if not sample:
        raise ValueError("at least one observation is required")
    return sum(sample) / len(sample)


def weighted_mean(values: Iterable[float], weights: Iterable[float]) -> float:
    """Return the weighted arithmetic mean.

    Weights must be finite and non-negative with a positive sum.
    """

    sample = tuple(values)
    weight_tuple = tuple(weights)
    if not sample or len(sample) != len(weight_tuple):
        raise ValueError("values and weights must be non-empty and aligned")
    total = 0.0
    weight_sum = 0.0
    for value, weight in zip(sample, weight_tuple, strict=True):
        if not math.isfinite(value) or not math.isfinite(weight) or weight < 0:
            raise ValueError("values and weights must be finite and non-negative")
        total += weight * value
        weight_sum += weight
    if weight_sum <= 0:
        raise ValueError("weighted mean requires a positive total weight")
    return total / weight_sum


def linear_interpolated_percentile(ordered_values: Sequence[float], probability: float) -> float:
    """Linear-interpolated quantile on an already-sorted sample.

    This matches the default (``linear``) interpolation used by common numeric
    libraries: ``position = (n - 1) * p`` with linear interpolation between the
    surrounding order statistics.
    """

    if not ordered_values:
        raise ValueError("percentile requires a non-empty sample")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be between zero and one")
    position = (len(ordered_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered_values[lower]
    weight = position - lower
    return ordered_values[lower] * (1 - weight) + ordered_values[upper] * weight


def golden_section_maximize(
    function: Callable[[float], float],
    lower: float,
    upper: float,
    *,
    tolerance: float = 1e-12,
    max_iterations: int = 200,
) -> tuple[float, float]:
    """Maximize a unimodal function with golden-section search.

    Returns ``(argmax, max_value)``. ``lower`` must be strictly below
    ``upper``; the caller is responsible for a unimodal objective.
    """

    if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
        raise ValueError("golden-section bounds must be finite with lower < upper")
    if tolerance <= 0 or max_iterations < 1:
        raise ValueError("tolerance and iteration count must be positive")
    inv_phi = (math.sqrt(5.0) - 1.0) / 2.0
    left = lower
    right = upper
    mid_left = right - inv_phi * (right - left)
    mid_right = left + inv_phi * (right - left)
    left_value = function(mid_left)
    right_value = function(mid_right)
    for _ in range(max_iterations):
        if right - left <= tolerance * max(1.0, abs(left), abs(right)):
            break
        if left_value > right_value:
            # peak lies in [left, mid_right]
            right = mid_right
            mid_right = mid_left
            right_value = left_value
            mid_left = right - inv_phi * (right - left)
            left_value = function(mid_left)
        else:
            # peak lies in [mid_left, right]
            left = mid_left
            mid_left = mid_right
            left_value = right_value
            mid_right = left + inv_phi * (right - left)
            right_value = function(mid_right)
    if left_value >= right_value:
        return mid_left, left_value
    return mid_right, right_value


def _regularized_incomplete_beta(alpha: float, beta: float, value: float) -> float:
    """Evaluate the regularized incomplete beta function I_x(a, b)."""

    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    log_beta = math.lgamma(alpha) + math.lgamma(beta) - math.lgamma(alpha + beta)
    front = math.exp(alpha * math.log(value) + beta * math.log1p(-value) - log_beta)
    if value < (alpha + 1.0) / (alpha + beta + 2.0):
        return front * _beta_continued_fraction(alpha, beta, value) / alpha
    return 1.0 - front * _beta_continued_fraction(beta, alpha, 1.0 - value) / beta


def _beta_continued_fraction(
    alpha: float,
    beta: float,
    value: float,
    *,
    epsilon: float = 3e-15,
    max_iterations: int = 500,
) -> float:
    """Evaluate the continued-fraction expansion of the incomplete beta ratio."""

    qab = alpha + beta
    qap = alpha + 1.0
    qam = alpha - 1.0
    coefficient = 1.0
    denominator = 1.0 - qab * value / qap
    if abs(denominator) < 1e-30:
        denominator = 1e-30
    denominator = 1.0 / denominator
    total = denominator
    for iteration in range(1, max_iterations + 1):
        doubled = 2 * iteration
        term = iteration * (beta - iteration) * value / ((qam + doubled) * (alpha + doubled))
        denominator = 1.0 + term * denominator
        if abs(denominator) < 1e-30:
            denominator = 1e-30
        coefficient = 1.0 + term / coefficient
        if abs(coefficient) < 1e-30:
            coefficient = 1e-30
        denominator = 1.0 / denominator
        total *= denominator * coefficient
        term = (
            -(alpha + iteration) * (qab + iteration) * value / ((alpha + doubled) * (qap + doubled))
        )
        denominator = 1.0 + term * denominator
        if abs(denominator) < 1e-30:
            denominator = 1e-30
        coefficient = 1.0 + term / coefficient
        if abs(coefficient) < 1e-30:
            coefficient = 1e-30
        denominator = 1.0 / denominator
        delta = denominator * coefficient
        total *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return total


def student_t_cdf(statistic: float, degrees_of_freedom: int) -> float:
    """Cumulative distribution function of Student's t with integer df >= 1."""

    if degrees_of_freedom < 1:
        raise ValueError("degrees_of_freedom must be at least one")
    if not math.isfinite(statistic):
        return 1.0 if statistic > 0 else 0.0
    transformed = degrees_of_freedom / (degrees_of_freedom + statistic * statistic)
    half = _regularized_incomplete_beta(degrees_of_freedom / 2.0, 0.5, transformed)
    if statistic > 0:
        return 1.0 - 0.5 * half
    if statistic < 0:
        return 0.5 * half
    return 0.5


def student_t_inv_cdf(probability: float, degrees_of_freedom: int) -> float:
    """Inverse CDF of Student's t with integer df >= 1.

    The probability must lie in the closed interval [0, 1]; the endpoints map
    to negative and positive infinity respectively.
    """

    if degrees_of_freedom < 1:
        raise ValueError("degrees_of_freedom must be at least one")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be between zero and one")
    if probability == 0.0:
        return -math.inf
    if probability == 1.0:
        return math.inf
    if probability == 0.5:
        return 0.0
    if probability > 0.5:
        return _student_t_positive_quantile(probability, degrees_of_freedom)
    return -_student_t_positive_quantile(1.0 - probability, degrees_of_freedom)


def _student_t_positive_quantile(probability: float, degrees_of_freedom: int) -> float:
    """Solve for t >= 0 with CDF(t) == probability in the transformed x-space."""

    target = 2.0 * (1.0 - probability)  # I_x(df/2, 1/2) target in (0, 1)
    low = 0.0
    high = 1.0
    half_df = degrees_of_freedom / 2.0
    for _ in range(90):
        middle = 0.5 * (low + high)
        current = _regularized_incomplete_beta(half_df, 0.5, middle)
        if current < target:
            low = middle
        else:
            high = middle
    transformed = 0.5 * (low + high)
    return math.sqrt(degrees_of_freedom * (1.0 - transformed) / transformed)


def _abramowitz_stegun_normal_cdf(statistic: float) -> float:
    """Standard normal CDF via the Abramowitz-Stegun 26.2.17 approximation.

    This mirrors the TypeScript ``bench-stats.mts`` oracle (documented error
    < 7.5e-8) exactly, including the sign-dependent tail branch, so paired
    Wilcoxon p-values stay bit-close to the legacy reference implementation.
    """

    t = 1.0 / (1.0 + 0.2316419 * abs(statistic))
    density = 0.3989422804014327 * math.exp((-statistic * statistic) / 2.0)
    polynomial = 0.31938153 + t * (
        -0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))
    )
    probability = density * t * polynomial
    if statistic > 0:
        return 1.0 - probability
    return probability


def wilcoxon_signed_rank(differences: Sequence[float]) -> tuple[float, float, int]:
    """Two-sided paired Wilcoxon signed-rank test aligned to the TS oracle.

    Semantics mirror ``bench-stats.mts``: zero differences are dropped before
    ranking; ties in absolute differences receive average ranks; if fewer than
    10 nonzero differences remain the test is conservative and returns
    ``p_value == 1`` with ``w_plus == 0`` (the oracle declines inference); for
    n >= 10 a normal approximation with tie correction is used and the
    p-value is capped at 1. An empty (or all-zero) input follows the same
    rule and returns ``(1.0, 0.0, 0)`` rather than raising.

    Returns ``(p_value, w_plus, n)`` where ``n`` is the number of nonzero
    differences used by the test.
    """

    nonzero = tuple(difference for difference in differences if difference != 0)
    n = len(nonzero)
    if n == 0:
        return (1.0, 0.0, 0)
    if n < 10:
        return (1.0, 0.0, n)
    ordered = sorted(abs(difference) for difference in nonzero)
    ranks: dict[float, float] = {}
    index = 0
    while index < n:
        end = index + 1
        while end < n and ordered[end] == ordered[index]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        for position in range(index, end):
            ranks[ordered[position]] = average_rank
        index = end
    w_plus = sum(ranks[abs(difference)] for difference in nonzero if difference > 0)
    mean_w = n * (n + 1) / 4.0
    tie_counts: dict[float, int] = {}
    for value in ordered:
        tie_counts[value] = tie_counts.get(value, 0) + 1
    tie_correction = sum((count**3 - count) / 48.0 for count in tie_counts.values())
    variance_w = n * (n + 1) * (2 * n + 1) / 24.0 - tie_correction
    z_score = (w_plus - mean_w) / math.sqrt(variance_w) if variance_w > 0 else 0.0
    p_value = 2.0 * (1.0 - _abramowitz_stegun_normal_cdf(abs(z_score)))
    return (min(1.0, p_value), w_plus, n)


def cliff_delta(ranks_a: Sequence[float], ranks_b: Sequence[float]) -> float:
    """Cliff's delta between two rank samples (smaller ranks are better).

    Returns ``P(A beats B) - P(B beats A)`` where ``a < b`` counts as a win
    for A; tied rank values contribute to neither side. Both samples must be
    non-empty.
    """

    if not ranks_a or not ranks_b:
        raise ValueError("both rank samples are required")
    wins = 0
    losses = 0
    for left in ranks_a:
        for right in ranks_b:
            if left < right:
                wins += 1
            elif left > right:
                losses += 1
    return (wins - losses) / (len(ranks_a) * len(ranks_b))
