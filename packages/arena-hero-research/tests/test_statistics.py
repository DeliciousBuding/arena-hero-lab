import math
from pathlib import Path

import pytest

from arena_hero_research import arithmetic_mean
from arena_hero_research.statistics import (
    cliff_delta,
    golden_section_maximize,
    linear_interpolated_percentile,
    student_t_cdf,
    student_t_inv_cdf,
    weighted_mean,
    wilcoxon_signed_rank,
)


def test_arithmetic_mean() -> None:
    assert arithmetic_mean([1.0, 2.0, 6.0]) == 3.0


def test_arithmetic_mean_rejects_empty_samples() -> None:
    with pytest.raises(ValueError, match="at least one"):
        arithmetic_mean([])


@pytest.mark.parametrize(
    ("values", "weights", "expected"),
    [
        ([1.0, 2.0, 3.0], [1.0, 1.0, 1.0], 2.0),
        ([1.0, 2.0, 3.0], [3.0, 0.0, 1.0], 1.5),
        ([0.0, 10.0], [1.0, 0.0], 0.0),
    ],
)
def test_weighted_mean(values, weights, expected) -> None:
    assert weighted_mean(values, weights) == pytest.approx(expected)


def test_weighted_mean_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        weighted_mean([], [])
    with pytest.raises(ValueError, match="aligned"):
        weighted_mean([1.0], [1.0, 2.0])
    with pytest.raises(ValueError, match="non-negative"):
        weighted_mean([1.0], [-1.0])
    with pytest.raises(ValueError, match="positive total"):
        weighted_mean([1.0, 2.0], [0.0, 0.0])


@pytest.mark.parametrize(
    ("sample", "probability", "expected"),
    [
        ((1.0, 2.0, 3.0, 4.0, 5.0), 0.5, 3.0),
        ((1.0, 2.0, 3.0, 4.0, 5.0), 0.25, 2.0),
        ((1.0, 2.0, 3.0, 4.0, 5.0), 0.0, 1.0),
        ((1.0, 2.0, 3.0, 4.0, 5.0), 1.0, 5.0),
        ((1.0, 2.0, 3.0, 4.0), 0.25, 1.75),
        ((1.0, 2.0, 3.0, 4.0), 0.5, 2.5),
    ],
)
def test_linear_interpolated_percentile(sample, probability, expected) -> None:
    assert linear_interpolated_percentile(sample, probability) == pytest.approx(expected)


def test_percentile_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        linear_interpolated_percentile((), 0.5)
    with pytest.raises(ValueError, match="between zero and one"):
        linear_interpolated_percentile((1.0, 2.0), 1.5)


def test_golden_section_maximize_finds_unimodal_peak() -> None:
    argmax, value = golden_section_maximize(lambda x: -((x - 3.0) ** 2), -30.0, 30.0)
    assert argmax == pytest.approx(3.0, abs=1e-9)
    assert value == pytest.approx(0.0, abs=1e-12)


def test_golden_section_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="bounds"):
        golden_section_maximize(lambda x: x, 1.0, 1.0)
    with pytest.raises(ValueError, match="bounds"):
        golden_section_maximize(lambda x: x, 2.0, 1.0)


# Student-t reference values were generated offline with SciPy and hard-coded
# here; SciPy is never a runtime dependency.
_T_CDF_REFERENCES = [
    (0.0, 1, 0.5),
    (0.5, 1, 0.6475836176504333),
    (1.0, 1, 0.75),
    (1.5, 1, 0.8128329581890011),
    (2.0, 1, 0.8524163823495667),
    (3.0, 1, 0.8975836176504333),
    (5.0, 1, 0.9371670418109987),
    (0.5, 2, 0.6666666666666666),
    (1.0, 2, 0.7886751345948129),
    (1.5, 2, 0.8638034375544994),
    (2.0, 2, 0.908248290463863),
    (3.0, 2, 0.9522670168666454),
    (5.0, 2, 0.9811252243246882),
    (0.5, 5, 0.6808505641795355),
    (1.0, 5, 0.8183912661754386),
    (1.5, 5, 0.9030481598787634),
    (2.0, 5, 0.9490302605850708),
    (3.0, 5, 0.9849503760512687),
    (5.0, 5, 0.9979476420099733),
    (1.0, 9, 0.8282818019310432),
    (2.0, 9, 0.9617235881146495),
    (3.0, 9, 0.9925218180447929),
    (5.0, 9, 0.9996305160450983),
    (1.0, 30, 0.8373456922869849),
    (2.0, 30, 0.9726874775185085),
    (3.0, 30, 0.9973050179671741),
    (5.0, 30, 0.9999883516572665),
]


@pytest.mark.parametrize(("statistic", "degrees", "expected"), _T_CDF_REFERENCES)
def test_student_t_cdf_known_values(statistic, degrees, expected) -> None:
    assert student_t_cdf(statistic, degrees) == pytest.approx(expected, abs=1e-12)


_T_PPF_REFERENCES = [
    (0.9, 1, 3.0776835371752544),
    (0.9, 2, 1.8856180831641272),
    (0.9, 5, 1.4758840488244815),
    (0.9, 9, 1.3830287383966329),
    (0.9, 30, 1.3104150253913955),
    (0.95, 1, 6.313751514675037),
    (0.95, 2, 2.9199855803537242),
    (0.95, 5, 2.0150483733330233),
    (0.95, 9, 1.8331129326562368),
    (0.95, 30, 1.697260886593957),
    (0.975, 1, 12.706204736174694),
    (0.975, 2, 4.302652729749462),
    (0.975, 5, 2.5705818356363146),
    (0.975, 9, 2.262157162798205),
    (0.975, 30, 2.0422724563012378),
    (0.99, 1, 31.820515953773935),
    (0.99, 2, 6.9645567342832715),
    (0.99, 5, 3.3649299989072174),
    (0.99, 9, 2.821437925025809),
    (0.99, 30, 2.457261542400591),
    (0.999, 1, 318.30883898555015),
    (0.999, 2, 22.327124770119866),
    (0.999, 5, 5.893429531356009),
    (0.999, 9, 4.296805662729918),
    (0.999, 30, 3.3851848668293045),
]


@pytest.mark.parametrize(("probability", "degrees", "expected"), _T_PPF_REFERENCES)
def test_student_t_inv_cdf_known_values(probability, degrees, expected) -> None:
    assert student_t_inv_cdf(probability, degrees) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("degrees", [1, 2, 5, 9, 30])
def test_student_t_round_trip(degrees) -> None:
    for probability in (0.6, 0.9, 0.95, 0.975, 0.99, 0.999):
        quantile = student_t_inv_cdf(probability, degrees)
        assert student_t_cdf(quantile, degrees) == pytest.approx(probability, abs=1e-9)


@pytest.mark.parametrize("degrees", [1, 2, 5, 9, 30])
def test_student_t_symmetry(degrees) -> None:
    for statistic in (0.5, 1.0, 2.0, 3.0):
        assert student_t_cdf(-statistic, degrees) == pytest.approx(
            1.0 - student_t_cdf(statistic, degrees), abs=1e-12
        )
    assert student_t_inv_cdf(0.5, degrees) == 0.0
    assert math.isinf(student_t_inv_cdf(0.0, degrees))
    assert math.isinf(student_t_inv_cdf(1.0, degrees))


def test_student_t_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="at least one"):
        student_t_cdf(0.0, 0)
    with pytest.raises(ValueError, match="at least one"):
        student_t_inv_cdf(0.5, 0)
    with pytest.raises(ValueError, match="between zero and one"):
        student_t_inv_cdf(1.5, 5)
    with pytest.raises(ValueError, match="between zero and one"):
        student_t_inv_cdf(-0.1, 5)


def test_statistics_module_has_no_heavy_dependencies() -> None:
    import arena_hero_research.statistics as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "import numpy" not in source
    assert "import scipy" not in source
    assert "import pandas" not in source
    assert "import statsmodels" not in source


# Wilcoxon reference values were generated offline with SciPy and hard-coded
# here; SciPy is never a runtime dependency. wPlus uses scipy.stats.rankdata
# (average-rank ties, the TS oracle semantics); the p-value reference uses
# scipy.stats.wilcoxon(method="approx"). SciPy's p-value uses an exact normal
# CDF while the TS-aligned implementation uses the Abramowitz-Stegun 26.2.17
# approximation (error < 7.5e-8), so p is asserted with abs=1e-6.
_WILCOXON_NORMAL_APPROX_REFERENCES = [
    # (differences, n, w_plus, scipy_p_approx)
    ((1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0), 10, 55.0, 0.005062032126267865),
    ((1.0, -1.0, 2.0, -2.0, 2.0, 3.0, -3.0, 3.0, 4.0, 5.0), 10, 42.5, 0.12405936180556316),
    ((-1.0, -2.0, -3.0, -4.0, -5.0, -6.0, -7.0, -8.0, -9.0, -10.0), 10, 0.0, 0.005062032126267865),
]


@pytest.mark.parametrize(
    ("differences", "n", "w_plus", "scipy_p_approx"),
    _WILCOXON_NORMAL_APPROX_REFERENCES,
)
def test_wilcoxon_signed_rank_normal_approximation(differences, n, w_plus, scipy_p_approx) -> None:
    p_value, actual_w_plus, actual_n = wilcoxon_signed_rank(differences)
    assert actual_n == n
    assert actual_w_plus == pytest.approx(w_plus, abs=1e-12)
    assert p_value == pytest.approx(scipy_p_approx, abs=1e-6)


# Conservative TS semantics: fewer than 10 nonzero differences (including
# empty / all-zero input) returns p=1 and wPlus=0 without inference.
_WILCOXON_CONSERVATIVE_CASES = [
    ((3.0, -1.0, 2.0), 3),
    ((1.0, -1.0, 2.0, 2.0, -2.0, 3.0), 6),
    ((0.0, 0.0, 0.0, 0.0), 0),
    ((), 0),
    ((1.0, 0.0, -2.0, 0.0, 3.0, 0.0, -1.0), 4),
    ((1.0, 0.0, 2.0, 0.0, 3.0, 0.0, -1.0, 0.0, -2.0, 0.0, 4.0, 5.0, 0.0, -3.0), 8),
]


@pytest.mark.parametrize(("differences", "n"), _WILCOXON_CONSERVATIVE_CASES)
def test_wilcoxon_signed_rank_conservative_small_samples(differences, n) -> None:
    assert wilcoxon_signed_rank(differences) == (1.0, 0.0, n)


# Cliff's delta reference values are hand-verified combinatorial counts
# (wins - losses) / (len(a) * len(b)); tied rank values count as neither.
_CLIFF_DELTA_CASES = [
    ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0), 1.0),
    ((4.0, 5.0, 6.0), (1.0, 2.0, 3.0), -1.0),
    ((1.0, 2.0, 3.0), (1.0, 2.0, 3.0), 0.0),
    ((1.0, 2.0, 3.0), (2.0, 3.0, 4.0), 5.0 / 9.0),
    ((1.0, 1.0, 2.0), (1.0, 3.0), 1.0 / 3.0),
]


@pytest.mark.parametrize(("ranks_a", "ranks_b", "expected"), _CLIFF_DELTA_CASES)
def test_cliff_delta_known_values(ranks_a, ranks_b, expected) -> None:
    assert cliff_delta(ranks_a, ranks_b) == pytest.approx(expected)


def test_cliff_delta_rejects_empty_samples() -> None:
    with pytest.raises(ValueError, match="rank samples"):
        cliff_delta((), (1.0, 2.0))
    with pytest.raises(ValueError, match="rank samples"):
        cliff_delta((1.0, 2.0), ())
