"""Behavior attribution over research outcome effects (P3-12).

Attributes paired treatment-minus-control differences to concrete behavior
dimensions using the same vocabulary as the P6-3 KPI differential
(``arena_hero_bench.kpi_differential.KpiDimension``): ``tick_alignment``,
``resource_growth``, ``collection_delivery``, ``population_forces``,
``survival_terminal``, and ``decision_distribution``.

The caller declares an explicit outcome -> dimension mapping. Every provided
effect estimate must be mapped to exactly one valid behavior dimension and
each dimension may receive at most one outcome; unmapped outcomes, unknown
dimensions, and duplicate mappings fail closed. Each dimension records the
effect estimate (mean difference, bootstrap confidence interval, adjusted
p-value) and its signed contribution weight
``|mean_difference| / sum(|mean_difference|)``. The artifact is canonical JSON
with a cross-platform quantized content digest, so the same estimates produce
the same bytes on every platform and the document can be consumed directly by
a leaderboard or report renderer.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from arena_hero_bench.kpi_differential import KpiDimension
from arena_hero_research.analysis import EffectEstimate
from arena_hero_research.validation import (
    require_float,
    require_identifier,
    require_sequence,
    require_sha256,
)
from arena_hero_sim.serialization import JsonValue, quantized_content_sha256

BEHAVIOR_ATTRIBUTION_SCHEMA: Final = "arena.research.behavior-attribution.v1"
ATTRIBUTION_GENERATOR_VERSION: Final = "0.1.0"

_VALID_DIMENSIONS: Final = frozenset(item.value for item in KpiDimension)
_DIRECTIONS: Final = frozenset({"positive", "negative", "zero"})


class AttributionError(ValueError):
    """Raised when behavior attribution cannot be computed or verified."""


def _direction(mean_difference: float) -> str:
    if mean_difference > 0:
        return "positive"
    if mean_difference < 0:
        return "negative"
    return "zero"


def _require_finite(value: float, field_name: str) -> None:
    if not math.isfinite(value):
        raise AttributionError(f"{field_name} must be finite")


@dataclass(frozen=True, slots=True)
class DimensionAttribution:
    """One behavior dimension's attributed contribution to the paired difference."""

    dimension: KpiDimension
    outcome_name: str
    mean_difference: float
    confidence_lower: float
    confidence_upper: float
    adjusted_p_value: float
    weight: float
    direction: str

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, KpiDimension):
            raise AttributionError("dimension must be a P6-3 KPI behavior dimension")
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        for name in (
            "mean_difference",
            "confidence_lower",
            "confidence_upper",
            "adjusted_p_value",
            "weight",
        ):
            _require_finite(getattr(self, name), name)
        if self.confidence_lower > self.confidence_upper:
            raise AttributionError("confidence interval must be ordered")
        if not 0 <= self.adjusted_p_value <= 1:
            raise AttributionError("adjusted p-value must be between zero and one")
        if not 0 <= self.weight <= 1:
            raise AttributionError("attribution weight must be between zero and one")
        if self.direction not in _DIRECTIONS:
            raise AttributionError("direction must be positive, negative, or zero")
        if self.direction != _direction(self.mean_difference):
            raise AttributionError("direction must match the attributed mean difference")
        if self.mean_difference == 0 and self.weight != 0:
            raise AttributionError("a zero effect must carry a zero weight")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "dimension": self.dimension.value,
            "outcome_name": self.outcome_name,
            "mean_difference": self.mean_difference,
            "confidence_lower": self.confidence_lower,
            "confidence_upper": self.confidence_upper,
            "adjusted_p_value": self.adjusted_p_value,
            "weight": self.weight,
            "direction": self.direction,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DimensionAttribution:
        dimension_value = value["dimension"]
        if dimension_value not in _VALID_DIMENSIONS:
            raise AttributionError(f"unsupported behavior dimension: {dimension_value!r}")
        return cls(
            dimension=KpiDimension(dimension_value),
            outcome_name=str(value["outcome_name"]),
            mean_difference=require_float(value["mean_difference"], "mean_difference"),
            confidence_lower=require_float(value["confidence_lower"], "confidence_lower"),
            confidence_upper=require_float(value["confidence_upper"], "confidence_upper"),
            adjusted_p_value=require_float(value["adjusted_p_value"], "adjusted_p_value"),
            weight=require_float(value["weight"], "weight"),
            direction=str(value["direction"]),
        )


@dataclass(frozen=True, slots=True)
class BehaviorAttribution:
    """Content-addressed behavior attribution over research outcome effects."""

    schema_version: str
    dimensions: tuple[DimensionAttribution, ...]
    total_absolute_effect: float
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != BEHAVIOR_ATTRIBUTION_SCHEMA:
            raise AttributionError("unsupported behavior attribution schema")
        dimensions = tuple(sorted(self.dimensions, key=lambda item: item.dimension.value))
        if not dimensions or len({item.dimension for item in dimensions}) != len(dimensions):
            raise AttributionError("attribution dimensions must be non-empty and unique")
        if len({item.outcome_name for item in dimensions}) != len(dimensions):
            raise AttributionError("attributed outcomes must be unique")
        _require_finite(self.total_absolute_effect, "total_absolute_effect")
        if self.total_absolute_effect < 0:
            raise AttributionError("total absolute effect must be non-negative")
        if self.total_absolute_effect > 0:
            total_weight = sum(item.weight for item in dimensions)
            if not math.isclose(total_weight, 1.0, rel_tol=0.0, abs_tol=1e-12):
                raise AttributionError("attribution weights must sum to one")
        elif any(item.weight != 0 for item in dimensions):
            raise AttributionError("a zero total requires a zero weight for every dimension")
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "generator_version": ATTRIBUTION_GENERATOR_VERSION,
            "dimensions": [item.to_dict() for item in self.dimensions],
            "total_absolute_effect": self.total_absolute_effect,
        }

    def verify(self) -> bool:
        return quantized_content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> BehaviorAttribution:
        dimensions = require_sequence(value["dimensions"], "dimensions")
        parsed: list[DimensionAttribution] = []
        for item in dimensions:
            if not isinstance(item, Mapping):
                raise TypeError("attribution dimension must be a mapping")
            parsed.append(DimensionAttribution.from_dict(item))
        return cls(
            schema_version=str(value["schema_version"]),
            dimensions=tuple(parsed),
            total_absolute_effect=require_float(
                value["total_absolute_effect"], "total_absolute_effect"
            ),
            canonical_sha256=str(value["canonical_sha256"]),
        )


def attribute_behavior_effects(
    *,
    estimates: Sequence[EffectEstimate],
    outcome_dimensions: Mapping[str, str],
) -> BehaviorAttribution:
    """Attribute paired research effects to P6-3 behavior dimensions.

    Every provided estimate must be mapped to exactly one valid behavior
    dimension; each dimension may receive at most one outcome. Unmapped
    estimates, unknown dimensions, and duplicate mappings fail closed.
    """
    if not estimates:
        raise AttributionError("at least one effect estimate is required")
    estimate_by_outcome = {item.outcome_name: item for item in estimates}
    if len(estimate_by_outcome) != len(estimates):
        raise AttributionError("effect estimates must have unique outcome names")
    if not outcome_dimensions:
        raise AttributionError("an outcome-to-dimension mapping is required")
    if set(outcome_dimensions) != set(estimate_by_outcome):
        unmapped = sorted(set(estimate_by_outcome) - set(outcome_dimensions))
        unknown = sorted(set(outcome_dimensions) - set(estimate_by_outcome))
        detail = ""
        if unmapped:
            detail += f"; unmapped outcomes: {', '.join(unmapped)}"
        if unknown:
            detail += f"; unknown outcomes: {', '.join(unknown)}"
        raise AttributionError(
            "outcome-to-dimension mapping must cover every estimate exactly once" + detail
        )
    for dimension_value in outcome_dimensions.values():
        if dimension_value not in _VALID_DIMENSIONS:
            raise AttributionError(f"unsupported behavior dimension: {dimension_value!r}")

    total_absolute_effect = sum(
        abs(estimate.mean_difference) for estimate in estimate_by_outcome.values()
    )
    dimensions: list[DimensionAttribution] = []
    for outcome_name, dimension_value in outcome_dimensions.items():
        estimate = estimate_by_outcome[outcome_name]
        for name in (
            "mean_difference",
            "confidence_lower",
            "confidence_upper",
            "adjusted_p_value",
        ):
            _require_finite(getattr(estimate, name), name)
        if estimate.confidence_lower > estimate.confidence_upper:
            raise AttributionError("effect confidence interval must be ordered")
        if not 0 <= estimate.adjusted_p_value <= 1:
            raise AttributionError("effect adjusted p-value must be between zero and one")
        weight = (
            0.0
            if total_absolute_effect == 0
            else abs(estimate.mean_difference) / total_absolute_effect
        )
        dimensions.append(
            DimensionAttribution(
                dimension=KpiDimension(dimension_value),
                outcome_name=outcome_name,
                mean_difference=estimate.mean_difference,
                confidence_lower=estimate.confidence_lower,
                confidence_upper=estimate.confidence_upper,
                adjusted_p_value=estimate.adjusted_p_value,
                weight=weight,
                direction=_direction(estimate.mean_difference),
            )
        )

    dimensions.sort(key=lambda item: item.dimension.value)
    payload = {
        "schema_version": BEHAVIOR_ATTRIBUTION_SCHEMA,
        "generator_version": ATTRIBUTION_GENERATOR_VERSION,
        "dimensions": [item.to_dict() for item in dimensions],
        "total_absolute_effect": total_absolute_effect,
    }
    return BehaviorAttribution(
        schema_version=BEHAVIOR_ATTRIBUTION_SCHEMA,
        dimensions=tuple(dimensions),
        total_absolute_effect=total_absolute_effect,
        canonical_sha256=quantized_content_sha256(payload),
    )
