import pytest

from arena_hero_research import arithmetic_mean


def test_arithmetic_mean() -> None:
    assert arithmetic_mean([1.0, 2.0, 6.0]) == 3.0


def test_arithmetic_mean_rejects_empty_samples() -> None:
    with pytest.raises(ValueError, match="at least one"):
        arithmetic_mean([])
