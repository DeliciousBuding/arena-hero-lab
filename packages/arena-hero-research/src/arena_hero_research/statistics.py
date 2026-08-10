"""Dependency-light statistical primitives used by research adapters."""

from collections.abc import Iterable


def arithmetic_mean(values: Iterable[float]) -> float:
    """Return an arithmetic mean and reject an empty sample."""

    sample = tuple(values)
    if not sample:
        raise ValueError("at least one observation is required")
    return sum(sample) / len(sample)
