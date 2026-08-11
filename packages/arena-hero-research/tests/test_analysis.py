from dataclasses import replace

import pytest

from arena_hero_research.analysis import (
    MissingObservationError,
    ResearchAnalysisError,
    UndeclaredOutcomeError,
    analyze_preregistered_paired_outcomes,
    benjamini_hochberg,
    normal_approx_paired_sample_size,
    paired_effect_with_bootstrap_ci,
    paired_rank_comparisons,
)
from arena_hero_research.contracts import MissingDataPolicy

from .research_fixtures import make_preregistration

OBSERVATIONS = {
    "score": ((1.0, 2.0, 3.0, 4.0), (1.5, 2.25, 4.0, 5.25)),
    "latency": ((10.0, 11.0, 12.0, 13.0), (9.0, 10.0, 10.5, 12.0)),
}


def test_preregistered_analysis_is_deterministic_and_ordered() -> None:
    preregistration = make_preregistration()
    first_estimates, first_quality = analyze_preregistered_paired_outcomes(
        preregistration, OBSERVATIONS, bootstrap_seed=91
    )
    second_estimates, second_quality = analyze_preregistered_paired_outcomes(
        preregistration, OBSERVATIONS, bootstrap_seed=91
    )
    assert first_estimates == second_estimates
    assert first_quality == second_quality
    assert [item.outcome_name for item in first_estimates] == ["score", "latency"]
    assert first_estimates[0].mean_difference == pytest.approx(0.75)
    assert first_estimates[0].standardized_effect is not None
    assert first_estimates[0].confidence_lower <= first_estimates[0].mean_difference
    assert first_estimates[0].confidence_upper >= first_estimates[0].mean_difference
    assert first_estimates[0].meets_minimum_effect


def test_benjamini_hochberg_known_values_and_validation() -> None:
    adjusted = benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.9})
    assert adjusted == pytest.approx({"a": 0.03, "b": 0.06, "c": 0.9})
    with pytest.raises(ValueError, match="between zero and one"):
        benjamini_hochberg({"bad": 1.1})


def test_normal_approximate_sample_size_has_known_value() -> None:
    assert normal_approx_paired_sample_size(effect_size=0.5) == 32
    with pytest.raises(ValueError):
        normal_approx_paired_sample_size(effect_size=0)


def test_missing_data_fail_policy_rejects_incomplete_pair() -> None:
    preregistration = make_preregistration(multiple_outcomes=False)
    with pytest.raises(MissingObservationError, match="missing pair"):
        analyze_preregistered_paired_outcomes(
            preregistration,
            {"score": ((1.0, None, 3.0, 4.0), (1.5, 2.0, 3.5, 4.5))},
            bootstrap_seed=1,
        )


def test_drop_pair_policy_reports_quality_and_enforces_planned_size() -> None:
    preregistration = make_preregistration(
        multiple_outcomes=False, missing_policy=MissingDataPolicy.DROP_PAIR
    )
    outcome = preregistration.design.outcomes[0]
    hypothesis = preregistration.hypotheses[0]
    relaxed_plan = replace(preregistration.design.analysis_plan, planned_sample_size=3)
    estimate, report = paired_effect_with_bootstrap_ci(
        outcome=outcome,
        hypothesis=hypothesis,
        control=(1.0, None, 3.0, 4.0),
        treatment=(1.5, 2.0, 4.0, 5.0),
        plan=relaxed_plan,
        bootstrap_seed=3,
    )
    assert estimate.sample_size == 3
    assert report.missing_pairs == report.dropped_pairs == 1
    assert report.warnings
    with pytest.raises(MissingObservationError, match="requires 4"):
        paired_effect_with_bootstrap_ci(
            outcome=outcome,
            hypothesis=hypothesis,
            control=(1.0, None, 3.0, 4.0),
            treatment=(1.5, 2.0, 4.0, 5.0),
            plan=preregistration.design.analysis_plan,
            bootstrap_seed=3,
        )


def test_analysis_rejects_unknown_or_missing_confirmatory_outcomes() -> None:
    preregistration = make_preregistration()
    with pytest.raises(UndeclaredOutcomeError, match="undeclared"):
        analyze_preregistered_paired_outcomes(
            preregistration, {**OBSERVATIONS, "surprise": ((1.0,), (2.0,))}, bootstrap_seed=4
        )
    with pytest.raises(UndeclaredOutcomeError, match="required"):
        analyze_preregistered_paired_outcomes(
            preregistration, {"score": OBSERVATIONS["score"]}, bootstrap_seed=4
        )


def test_analysis_rejects_tampered_preregistration() -> None:
    preregistration = make_preregistration(multiple_outcomes=False)
    tampered = replace(preregistration, registered_at="2026-08-10T22:00:00+08:00")
    with pytest.raises(ResearchAnalysisError, match="digest"):
        analyze_preregistered_paired_outcomes(
            tampered, {"score": OBSERVATIONS["score"]}, bootstrap_seed=5
        )


def test_zero_variance_effect_is_explicitly_flagged() -> None:
    preregistration = make_preregistration(multiple_outcomes=False)
    estimate, _ = paired_effect_with_bootstrap_ci(
        outcome=preregistration.design.outcomes[0],
        hypothesis=preregistration.hypotheses[0],
        control=(1.0, 2.0, 3.0, 4.0),
        treatment=(2.0, 3.0, 4.0, 5.0),
        plan=preregistration.design.analysis_plan,
        bootstrap_seed=6,
    )
    assert estimate.standardized_effect is None
    assert estimate.warnings


def test_paired_rank_comparisons_are_deterministic_and_ordered() -> None:
    matches = [
        {"scenario": "s1", "seed": 1, "rank": {"alpha-s1": 1, "beta-s2": 2}},
        {"scenario": "s1", "seed": 1, "rank": {"alpha-s1": 2, "beta-s2": 1}},
        {"scenario": "s2", "seed": 2, "rank": {"alpha-s1": 1, "beta-s2": 2}},
        {"scenario": "s2", "seed": 2, "rank": {"alpha-s1": 2, "beta-s2": 1}},
    ]
    first_contestants, first_pairs = paired_rank_comparisons(matches)
    second_contestants, second_pairs = paired_rank_comparisons(matches)
    assert first_contestants == second_contestants == ("alpha", "beta")
    assert first_pairs == second_pairs
    pair = first_pairs[0]
    assert (pair.a, pair.b) == ("alpha", "beta")
    assert pair.n == 4
    # Four nonzero differences -> conservative Wilcoxon path.
    assert pair.w_plus == 0.0
    assert pair.p_value == 1.0
    assert pair.cliff_delta == pytest.approx(0.0)
    assert pair.mean_rank_diff == pytest.approx(0.0)
    assert pair.q_value == pytest.approx(1.0)


def test_paired_rank_comparisons_map_player_suffixes_to_contestants() -> None:
    matches = [
        {
            "scenario": "s1",
            "seed": 1,
            "rank": {"waaiging-s7": 1, "ts-aggressive": 2, "waaiging-s9": 3},
        },
        {
            "scenario": "s1",
            "seed": 1,
            "rank": {"waaiging-s7": 2, "ts-aggressive": 1, "waaiging-s9": 4},
        },
    ]
    contestants, pairs = paired_rank_comparisons(matches)
    assert contestants == ("ts-aggressive", "waaiging")
    assert len(pairs) == 1
    pair = pairs[0]
    # waaiging ranks accumulate across both -s7/-s9 player ids (4 ranks);
    # the paired test uses n=min(4, 2)=2 while Cliff's delta uses all ranks.
    assert pair.n == 2
    assert pair.cliff_delta == pytest.approx(0.5)


def test_paired_rank_comparisons_reject_matches_without_rank_mapping() -> None:
    with pytest.raises(ValueError, match="rank mapping"):
        paired_rank_comparisons([{"scenario": "s1", "seed": 1}])
