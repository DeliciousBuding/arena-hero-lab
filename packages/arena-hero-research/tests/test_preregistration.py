from dataclasses import FrozenInstanceError, replace

import pytest

from arena_hero_research.contracts import (
    ExperimentDesign,
    Factor,
    MultipleComparisonPolicy,
    Preregistration,
    ReplicationPlan,
)

from .research_fixtures import make_preregistration


def test_preregistration_hash_is_stable_and_verifiable() -> None:
    first = make_preregistration()
    second = make_preregistration()
    assert first.canonical_sha256 == second.canonical_sha256
    assert first.verify()
    assert second.verify()


def test_preregistration_is_immutable_and_tampering_is_detected() -> None:
    preregistration = make_preregistration()
    with pytest.raises(FrozenInstanceError):
        preregistration.registered_at = "2026-08-10T21:00:00+08:00"  # ty: ignore[invalid-assignment]
    tampered = replace(preregistration, registered_at="2026-08-10T21:00:00+08:00")
    assert not tampered.verify()


def test_preregistration_requires_timestamp_offset() -> None:
    preregistration = make_preregistration()
    with pytest.raises(ValueError, match="UTC offset"):
        Preregistration.create(
            question=preregistration.question,
            hypotheses=preregistration.hypotheses,
            design=preregistration.design,
            registered_at="2026-08-10T20:00:00",
        )


def test_replication_plan_rejects_duplicate_independent_seeds() -> None:
    with pytest.raises(ValueError, match="unique"):
        ReplicationPlan(
            replications=2,
            minimum_successful=2,
            seeds=(7, 7),
            independent_seeds=True,
        )


def test_design_rejects_insufficient_planned_observations() -> None:
    preregistration = make_preregistration(multiple_outcomes=False)
    plan = replace(
        preregistration.design.replication_plan,
        minimum_successful=2,
        observations_per_replication=1,
    )
    with pytest.raises(ValueError, match="planned observation count"):
        replace(preregistration.design, replication_plan=plan)


def test_design_rejects_multiple_confirmatory_outcomes_without_correction() -> None:
    preregistration = make_preregistration()
    analysis_plan = replace(
        preregistration.design.analysis_plan,
        multiple_comparison_policy=MultipleComparisonPolicy.NONE,
    )
    with pytest.raises(ValueError, match="comparison policy"):
        replace(preregistration.design, analysis_plan=analysis_plan)


def test_design_rejects_duplicate_factor_names_and_pairing_keys() -> None:
    preregistration = make_preregistration(multiple_outcomes=False)
    factor = preregistration.design.factors[0]
    with pytest.raises(ValueError, match="factor names"):
        replace(preregistration.design, factors=(factor, factor))
    with pytest.raises(ValueError, match="pairing keys"):
        replace(preregistration.design, pairing_keys=("seed", "seed"))


def test_analysis_plan_rejects_unimplemented_method_labels() -> None:
    preregistration = make_preregistration(multiple_outcomes=False)
    with pytest.raises(ValueError, match="currently implemented"):
        replace(preregistration.design.analysis_plan, estimator="post-hoc-best-result")


def test_preregistration_requires_exact_confirmatory_hypothesis_coverage() -> None:
    preregistration = make_preregistration()
    with pytest.raises(ValueError, match="cover every confirmatory outcome"):
        replace(preregistration, hypotheses=preregistration.hypotheses[:1])


def test_factor_requires_unique_levels() -> None:
    with pytest.raises(ValueError, match="unique levels"):
        Factor(
            name="strategy",
            levels=("control", "control"),
            randomized=True,
            assignment_unit="pair",
        )


def test_single_confirmatory_outcome_allows_no_multiplicity_correction() -> None:
    preregistration = make_preregistration(multiple_outcomes=False)
    assert isinstance(preregistration.design, ExperimentDesign)
    assert (
        preregistration.design.analysis_plan.multiple_comparison_policy
        is MultipleComparisonPolicy.NONE
    )
