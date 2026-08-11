from __future__ import annotations

import math

import pytest

from arena_hero_bench.kpi_differential import KpiDimension
from arena_hero_research.analysis import EffectEstimate
from arena_hero_research.attribution import (
    BEHAVIOR_ATTRIBUTION_SCHEMA,
    AttributionError,
    BehaviorAttribution,
    DimensionAttribution,
    attribute_behavior_effects,
)
from arena_hero_sim.serialization import quantized_content_sha256


def _estimate(
    name: str,
    mean: float,
    lower: float,
    upper: float,
    adjusted_p: float = 0.03,
) -> EffectEstimate:
    return EffectEstimate(
        outcome_name=name,
        hypothesis_id=f"h-{name}",
        sample_size=30,
        mean_difference=mean,
        standardized_effect=1.0,
        confidence_lower=lower,
        confidence_upper=upper,
        confidence_level=0.95,
        raw_p_value=adjusted_p,
        adjusted_p_value=adjusted_p,
        meets_minimum_effect=True,
        estimator="paired-mean-difference",
        effect_size_method="cohen-dz",
        ci_method="paired-bootstrap-percentile",
        p_value_method="paired-normal-approximation",
    )


def _three_estimates() -> tuple[EffectEstimate, ...]:
    return (
        _estimate("kill_rate", 2.0, 0.8, 3.2),
        _estimate("resources_per_tick", -1.0, -2.2, 0.2, 0.12),
        _estimate("population_peak", 0.0, -0.5, 0.5, 0.9),
    )


def _mapping() -> dict[str, str]:
    return {
        "kill_rate": "population_forces",
        "resources_per_tick": "resource_growth",
        "population_peak": "survival_terminal",
    }


def test_kpi_dimension_vocabulary_matches_p6_3_style() -> None:
    # The attribution vocabulary must stay aligned with the P6-3 KPI
    # differential: same six behavior dimension identifiers, nothing extra.
    expected = {item.value for item in KpiDimension}
    actual = {
        "tick_alignment",
        "resource_growth",
        "collection_delivery",
        "population_forces",
        "survival_terminal",
        "decision_distribution",
    }
    assert actual == expected
    assert len(actual) == 6


def test_known_answer_weights_and_directions() -> None:
    attribution = attribute_behavior_effects(
        estimates=_three_estimates(),
        outcome_dimensions=_mapping(),
    )
    assert attribution.schema_version == BEHAVIOR_ATTRIBUTION_SCHEMA
    assert attribution.total_absolute_effect == 3.0
    by_dimension = {item.dimension.value: item for item in attribution.dimensions}
    assert by_dimension["population_forces"].mean_difference == 2.0
    assert by_dimension["population_forces"].weight == pytest.approx(2.0 / 3.0)
    assert by_dimension["population_forces"].direction == "positive"
    assert by_dimension["resource_growth"].weight == pytest.approx(1.0 / 3.0)
    assert by_dimension["resource_growth"].direction == "negative"
    assert by_dimension["survival_terminal"].weight == 0.0
    assert by_dimension["survival_terminal"].direction == "zero"
    assert attribution.verify()


def test_canonical_ordering_is_by_dimension_id() -> None:
    scrambled = {
        "resources_per_tick": "resource_growth",
        "population_peak": "survival_terminal",
        "kill_rate": "population_forces",
    }
    attribution = attribute_behavior_effects(
        estimates=_three_estimates(),
        outcome_dimensions=scrambled,
    )
    ids = [item.dimension.value for item in attribution.dimensions]
    assert ids == sorted(ids)
    assert ids == ["population_forces", "resource_growth", "survival_terminal"]


def test_digest_is_stable_across_construction() -> None:
    first = attribute_behavior_effects(estimates=_three_estimates(), outcome_dimensions=_mapping())
    second = attribute_behavior_effects(
        estimates=tuple(reversed(_three_estimates())),
        outcome_dimensions={key: value for key, value in reversed(list(_mapping().items()))},
    )
    assert first.to_dict() == second.to_dict()
    assert first.canonical_sha256 == second.canonical_sha256
    assert quantized_content_sha256(first.payload()) == first.canonical_sha256


def test_round_trip_preserves_artifact() -> None:
    attribution = attribute_behavior_effects(
        estimates=_three_estimates(), outcome_dimensions=_mapping()
    )
    restored = BehaviorAttribution.from_dict(attribution.to_dict())
    assert restored == attribution
    assert restored.verify()


def test_verify_rejects_tampered_payload() -> None:
    attribution = attribute_behavior_effects(
        estimates=_three_estimates(), outcome_dimensions=_mapping()
    )
    tampered = attribution.to_dict()
    dimensions = tampered["dimensions"]
    assert isinstance(dimensions, list)
    first = dict(dimensions[0])
    first["adjusted_p_value"] = 0.5
    tampered["dimensions"] = [first, *dimensions[1:]]
    restored = BehaviorAttribution.from_dict(tampered)
    assert not restored.verify()


def test_zero_total_uses_zero_weights() -> None:
    estimates = (
        _estimate("kill_rate", 0.0, -0.5, 0.5),
        _estimate("resources_per_tick", 0.0, -0.2, 0.2),
    )
    attribution = attribute_behavior_effects(
        estimates=estimates,
        outcome_dimensions={
            "kill_rate": "population_forces",
            "resources_per_tick": "resource_growth",
        },
    )
    assert attribution.total_absolute_effect == 0.0
    assert all(item.weight == 0.0 for item in attribution.dimensions)
    assert all(item.direction == "zero" for item in attribution.dimensions)
    assert attribution.verify()


@pytest.mark.parametrize(
    ("estimates", "mapping", "message"),
    [
        ((), {"a": "tick_alignment"}, "at least one"),
        (_three_estimates(), {}, "mapping is required"),
        (
            _three_estimates(),
            {"kill_rate": "population_forces", "resources_per_tick": "resource_growth"},
            "unmapped",
        ),
        (
            _three_estimates(),
            {
                "kill_rate": "not-a-dimension",
                "resources_per_tick": "resource_growth",
                "population_peak": "survival_terminal",
            },
            "unsupported",
        ),
        (
            (
                _estimate("kill_rate", 1.0, 0.5, 1.5),
                _estimate("kill_rate", 2.0, 1.0, 3.0),
            ),
            {"kill_rate": "population_forces"},
            "unique outcome names",
        ),
    ],
)
def test_fail_closed_on_invalid_inputs(
    estimates: tuple[EffectEstimate, ...],
    mapping: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(AttributionError, match=message):
        attribute_behavior_effects(estimates=estimates, outcome_dimensions=mapping)


def test_fail_closed_on_unknown_outcome_in_mapping() -> None:
    estimates = _three_estimates()
    mapping = {**_mapping(), "ghost": "tick_alignment"}
    with pytest.raises(AttributionError, match="unknown outcomes: ghost"):
        attribute_behavior_effects(estimates=estimates, outcome_dimensions=mapping)


def test_fail_closed_on_non_finite_effect() -> None:
    estimates = (_estimate("kill_rate", math.nan, 0.5, 1.5),)
    with pytest.raises(AttributionError, match="finite"):
        attribute_behavior_effects(
            estimates=estimates,
            outcome_dimensions={"kill_rate": "population_forces"},
        )


def test_fail_closed_on_inverted_ci() -> None:
    estimates = (_estimate("kill_rate", 1.0, 2.0, 0.5),)
    with pytest.raises(AttributionError, match="ordered"):
        attribute_behavior_effects(
            estimates=estimates,
            outcome_dimensions={"kill_rate": "population_forces"},
        )


def test_fail_closed_on_duplicate_dimension() -> None:
    estimates = (
        _estimate("kill_rate", 1.0, 0.5, 1.5),
        _estimate("resources_per_tick", 2.0, 1.0, 3.0),
    )
    with pytest.raises(AttributionError, match="non-empty and unique"):
        attribute_behavior_effects(
            estimates=estimates,
            outcome_dimensions={
                "kill_rate": "population_forces",
                "resources_per_tick": "population_forces",
            },
        )


def test_dimension_attribution_rejects_inconsistent_direction() -> None:
    with pytest.raises(AttributionError, match="direction"):
        DimensionAttribution(
            dimension=KpiDimension.POPULATION_FORCES,
            outcome_name="kill_rate",
            mean_difference=1.0,
            confidence_lower=0.5,
            confidence_upper=1.5,
            adjusted_p_value=0.03,
            weight=0.5,
            direction="negative",
        )


def test_dimension_attribution_rejects_zero_effect_with_weight() -> None:
    with pytest.raises(AttributionError, match="zero"):
        DimensionAttribution(
            dimension=KpiDimension.POPULATION_FORCES,
            outcome_name="kill_rate",
            mean_difference=0.0,
            confidence_lower=-0.5,
            confidence_upper=0.5,
            adjusted_p_value=0.9,
            weight=0.5,
            direction="zero",
        )
