"""Random-intercept hierarchical model tests: gates, dual-path, bridge, digest."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from arena_hero_research.execution import PairedObservation
from arena_hero_research.hierarchical import (
    ClusterIdentifiabilityError,
    ClusterMissingPolicy,
    ClusterObservation,
    HierarchicalFitError,
    RandomInterceptFit,
    SingularFitError,
    cross_validate_random_intercept,
    fit_random_intercept,
    paired_to_cluster_observations,
)

BALANCED = [
    ("c1", 1.0, 2.2),
    ("c2", 2.0, 3.6),
    ("c3", 1.5, 2.8),
    ("c4", 2.5, 4.1),
]


def clustered(
    spec,
    *,
    outcome: str = "score",
    control: str = "control",
    treatment: str = "treatment",
) -> tuple[ClusterObservation, ...]:
    observations: list[ClusterObservation] = []
    for cluster_id, controls, treatments in spec:
        for index, value in enumerate(controls):
            observations.append(
                ClusterObservation(outcome, cluster_id, f"c{index}", control, value)
            )
        for index, value in enumerate(treatments):
            observations.append(
                ClusterObservation(outcome, cluster_id, f"t{index}", treatment, value)
            )
    return tuple(observations)


def balanced_observations() -> tuple[ClusterObservation, ...]:
    return clustered(
        [(cluster_id, (control,), (treatment,)) for cluster_id, control, treatment in BALANCED]
    )


# --------------------------------------------------------------------------- contracts


def test_cluster_observation_round_trip_and_validation() -> None:
    item = ClusterObservation("score", "c1", "c1.t", "treatment", 2.5)
    assert ClusterObservation.from_dict(item.to_dict()) == item
    with pytest.raises(ValueError, match="identifier"):
        ClusterObservation("Score", "c1", "c1.t", "treatment", 2.5)
    with pytest.raises(ValueError, match="finite"):
        ClusterObservation("score", "c1", "c1.t", "treatment", math.inf)
    with pytest.raises(ValueError, match="must not be empty"):
        ClusterObservation("score", "c1", "c1.t", "", 2.5)


# --------------------------------------------------------------------------- fit


def test_balanced_fit_known_answer_and_ci() -> None:
    observations = balanced_observations()
    fit = fit_random_intercept(outcome_name="score", observations=observations)
    assert fit.verify()
    assert not fit.singular
    assert fit.cluster_count == 4
    assert fit.observation_count == 8
    assert fit.degrees_of_freedom == 3
    assert fit.treatment_effect == pytest.approx(1.425)
    assert fit.between_variance == pytest.approx(0.5416666686415601, rel=1e-9)
    assert fit.error_variance == pytest.approx(0.021249999925448886, rel=1e-9)
    assert fit.icc == pytest.approx(
        fit.between_variance / (fit.between_variance + fit.error_variance)
    )
    assert fit.hierarchical_effect == pytest.approx(
        fit.treatment_effect / math.sqrt(fit.between_variance + fit.error_variance), rel=1e-12
    )
    assert fit.standard_error is not None
    assert fit.confidence_lower is not None
    assert fit.confidence_upper is not None
    assert fit.confidence_lower <= fit.treatment_effect <= fit.confidence_upper
    assert fit.confidence_lower < fit.confidence_upper
    assert fit.standard_error > 0
    assert fit.effect_size_method == "hierarchical-d-v1"
    assert fit.ci_method == "between-cluster-t"
    assert fit.estimator == "random-intercept-reml"


def test_known_answer_integer_fixture() -> None:
    # Hand-computable: four clusters, each with control 0 and treatment 2*k,
    # so the within-cluster contrast is exactly 2*k and REML beta is the mean
    # of the pair differences.
    spec = [
        ("c1", (0.0,), (2.0,)),
        ("c2", (0.0,), (4.0,)),
        ("c3", (0.0,), (6.0,)),
        ("c4", (0.0,), (8.0,)),
    ]
    fit = fit_random_intercept(outcome_name="score", observations=clustered(spec))
    assert fit.treatment_effect == pytest.approx(5.0, abs=1e-9)
    assert fit.verify()


def test_input_order_does_not_change_fit_or_digest() -> None:
    observations = balanced_observations()
    first = fit_random_intercept(outcome_name="score", observations=observations)
    shuffled = tuple(reversed(observations))
    second = fit_random_intercept(outcome_name="score", observations=shuffled)
    assert first.treatment_effect == second.treatment_effect
    assert first.canonical_sha256 == second.canonical_sha256


def test_fit_round_trip_and_tamper_detection() -> None:
    fit = fit_random_intercept(outcome_name="score", observations=balanced_observations())
    restored = RandomInterceptFit.from_dict(fit.to_dict())
    assert restored == fit
    assert restored.verify()
    tampered = replace(restored, treatment_effect=restored.treatment_effect + 1.0)
    assert not tampered.verify()


def test_fit_rejects_unknown_schema_and_method_labels() -> None:
    fit = fit_random_intercept(outcome_name="score", observations=balanced_observations())
    with pytest.raises(HierarchicalFitError, match="schema"):
        RandomInterceptFit.from_dict({**fit.to_dict(), "schema_version": "other.v1"})
    with pytest.raises(HierarchicalFitError, match="estimator"):
        RandomInterceptFit.from_dict({**fit.to_dict(), "estimator": "ols"})
    with pytest.raises(HierarchicalFitError, match="effect-size"):
        RandomInterceptFit.from_dict({**fit.to_dict(), "effect_size_method": "cohen-dz"})
    with pytest.raises(HierarchicalFitError, match="interval"):
        RandomInterceptFit.from_dict({**fit.to_dict(), "ci_method": "kr"})
    with pytest.raises(HierarchicalFitError, match="estimand"):
        RandomInterceptFit.from_dict({**fit.to_dict(), "estimand": "marginal effect"})


# --------------------------------------------------------------------------- cross-validation


def test_balanced_cross_validation_within_tolerance() -> None:
    report = cross_validate_random_intercept(
        outcome_name="score", observations=balanced_observations()
    )
    assert report.balanced
    assert report.authoritative == "random-intercept-reml"
    assert report.passed
    assert report.effect_absolute_difference <= 1e-8
    assert report.variance_absolute_difference <= 1e-7
    assert report.path_a_effect == pytest.approx(report.path_b_effect, abs=1e-8)


def test_mildly_unbalanced_cross_validation_within_loose_tolerance() -> None:
    observations = clustered(
        [
            ("c1", (1.0, 1.5), (2.2, 2.6)),
            ("c2", (2.0,), (3.6, 3.9)),
            ("c3", (1.5, 1.8, 1.2), (2.8, 3.1, 2.5)),
            ("c4", (2.5, 2.8), (4.1, 4.4)),
        ]
    )
    report = cross_validate_random_intercept(outcome_name="score", observations=observations)
    assert not report.balanced
    assert report.passed
    assert report.effect_absolute_difference <= report.effect_tolerance


def test_adversarial_unbalanced_cross_validation() -> None:
    # Small clusters with large opposite-magnitude contrasts plus large weak
    # clusters: both independent paths must still agree within the declared
    # loose tolerance and the fit must remain deterministic and non-singular.
    observations = clustered(
        [
            ("c1", (1.0,), (5.0,)),
            ("c2", (2.0,), (6.0,)),
            ("c3", (1.5,), (5.5,)),
            ("c4", (3.0, 3.1, 3.2), (3.3, 3.4, 3.5)),
            ("c5", (4.0, 4.1, 4.2, 4.3), (4.4, 4.5, 4.6, 4.7)),
        ]
    )
    fit = fit_random_intercept(outcome_name="score", observations=observations)
    assert not fit.singular
    assert fit.verify()
    report = cross_validate_random_intercept(outcome_name="score", observations=observations)
    assert not report.balanced
    assert report.passed
    assert report.effect_absolute_difference <= report.effect_tolerance


# --------------------------------------------------------------------------- paired bridge


def test_paired_bridge_reproduces_paired_mean_difference() -> None:
    pairs = tuple(
        PairedObservation("score", cluster_id, control, treatment)
        for cluster_id, control, treatment in BALANCED
    )
    observations = paired_to_cluster_observations(pairs)
    fit = fit_random_intercept(outcome_name="score", observations=observations)
    differences = [treatment - control for _, control, treatment in BALANCED]
    paired_mean = sum(differences) / len(differences)
    assert fit.treatment_effect == pytest.approx(paired_mean, abs=1e-9)


def test_paired_bridge_rejects_incomplete_pairs() -> None:
    pairs = (PairedObservation("score", "c1", 1.0, None),)
    with pytest.raises(HierarchicalFitError, match="complete pairs"):
        paired_to_cluster_observations(pairs)


def test_paired_bridge_rejects_same_levels() -> None:
    pairs = tuple(
        PairedObservation("score", cluster_id, control, treatment)
        for cluster_id, control, treatment in BALANCED
    )
    with pytest.raises(HierarchicalFitError, match="must differ"):
        paired_to_cluster_observations(pairs, control_level="x", treatment_level="x")


# --------------------------------------------------------------------------- negative gates


def test_fewer_than_two_clusters_is_rejected() -> None:
    observations = clustered([("c1", (1.0,), (2.0,))])
    with pytest.raises(ClusterIdentifiabilityError, match="two complete clusters"):
        fit_random_intercept(outcome_name="score", observations=observations)


def test_cluster_randomized_design_is_rejected() -> None:
    # Cluster-randomized assignment: each cluster receives exactly one
    # treatment level, so no within-cluster contrast exists anywhere and the
    # within-cluster estimand is unidentifiable. The fit must fail closed.
    observations = clustered(
        [
            ("c1", (1.0, 1.1), ()),
            ("c2", (1.5, 1.6), ()),
            ("c3", (), (2.0, 2.1)),
            ("c4", (), (2.5, 2.6)),
        ]
    )
    with pytest.raises(ClusterIdentifiabilityError, match="missing treatment level"):
        fit_random_intercept(outcome_name="score", observations=observations)


def test_non_finite_values_are_rejected() -> None:
    # Non-finite values fail closed at construction, before any fit can run.
    with pytest.raises(ValueError, match="finite"):
        ClusterObservation("score", "c1", "c1.t", "treatment", math.nan)
    with pytest.raises(ValueError, match="finite"):
        ClusterObservation("score", "c1", "c1.t", "treatment", math.inf)
    with pytest.raises(ValueError, match="finite"):
        ClusterObservation("score", "c1", "c1.t", "treatment", -math.inf)


def test_more_than_two_treatment_levels_is_rejected() -> None:
    observations = clustered(
        [
            ("c1", (1.0,), (2.0,)),
            ("c2", (1.5,), (2.5,)),
            ("c3", (2.0,), (3.0,)),
        ],
        control="low",
        treatment="high",
    )
    extra = ClusterObservation("score", "c1", "c1.x", "mid", 1.7)
    with pytest.raises(HierarchicalFitError, match="exactly two treatment levels"):
        fit_random_intercept(outcome_name="score", observations=(*observations, extra))


def test_missing_policy_fail_rejects_incomplete_cluster() -> None:
    observations = [
        *balanced_observations(),
        ClusterObservation("score", "c5", "c5.c", "control", 1.0),
    ]
    with pytest.raises(ClusterIdentifiabilityError, match="missing treatment level"):
        fit_random_intercept(outcome_name="score", observations=observations)


def test_missing_policy_drop_cluster_drops_and_warns() -> None:
    observations = [
        *balanced_observations(),
        ClusterObservation("score", "c5", "c5.c", "control", 1.0),
    ]
    fit = fit_random_intercept(
        outcome_name="score",
        observations=observations,
        missing_policy=ClusterMissingPolicy.DROP_CLUSTER,
    )
    assert fit.dropped_clusters == 1
    assert fit.cluster_count == 4
    assert any("dropped" in warning for warning in fit.warnings)
    assert fit.verify()


def test_fully_degenerate_data_is_rejected() -> None:
    # Every observation identical: neither error nor between variance exists.
    observations = clustered(
        [("c1", (1.0,), (2.0,)), ("c2", (1.0,), (2.0,)), ("c3", (1.0,), (2.0,))]
    )
    with pytest.raises(SingularFitError, match="non-positive residual"):
        fit_random_intercept(outcome_name="score", observations=observations)


def test_near_singular_between_variance_is_reported_not_claimed() -> None:
    # Cluster means are almost identical while within-cluster error is real:
    # between variance sits at the boundary and CI/effect claims are disabled.
    observations = clustered(
        [
            ("c1", (1.0,), (3.0, 3.4, 2.6)),
            ("c2", (1.05,), (3.05, 3.45, 2.65)),
            ("c3", (0.95,), (2.95, 3.35, 2.55)),
            ("c4", (1.02,), (3.02, 3.42, 2.62)),
        ]
    )
    fit = fit_random_intercept(outcome_name="score", observations=observations)
    assert fit.singular
    assert fit.boundary_lambda
    assert fit.confidence_lower is None
    assert fit.confidence_upper is None
    assert fit.standard_error is None
    assert fit.hierarchical_effect is None
    assert fit.warnings
    assert fit.verify()


def test_cross_validation_requires_three_clusters() -> None:
    observations = clustered([("c1", (1.0,), (2.0,)), ("c2", (1.5,), (2.5,))])
    with pytest.raises(HierarchicalFitError, match="at least three clusters"):
        cross_validate_random_intercept(outcome_name="score", observations=observations)


def test_extreme_values_stay_finite_and_deterministic() -> None:
    observations = clustered(
        [
            ("c1", (1e12,), (1e12 + 2.0,)),
            ("c2", (1e12 + 1.0,), (1e12 + 3.0,)),
            ("c3", (1e12 + 0.5,), (1e12 + 2.5,)),
            ("c4", (1e12 + 1.5,), (1e12 + 3.5,)),
        ]
    )
    fit = fit_random_intercept(outcome_name="score", observations=observations)
    assert fit.verify()
    assert math.isfinite(fit.treatment_effect)
    assert fit.treatment_effect == pytest.approx(2.0, abs=1e-6)


def test_long_and_reordered_identifiers_are_deterministic() -> None:
    long_id = "x" * 127
    observations = clustered(
        [
            (long_id, (1.0,), (2.0,)),
            ("c2", (1.5,), (2.5,)),
            ("c3", (2.0,), (3.0,)),
            ("c4", (2.5,), (3.5,)),
        ]
    )
    first = fit_random_intercept(outcome_name="score", observations=observations)
    second = fit_random_intercept(outcome_name="score", observations=tuple(reversed(observations)))
    assert first.canonical_sha256 == second.canonical_sha256
    with pytest.raises(ValueError, match="identifier"):
        ClusterObservation("score", "x" * 129, "obs.1", "treatment", 1.0)


def test_hierarchical_module_has_no_heavy_dependencies() -> None:
    import arena_hero_research.hierarchical as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "import numpy" not in source
    assert "import scipy" not in source
    assert "import pandas" not in source
    assert "import statsmodels" not in source


from pathlib import Path  # noqa: E402
