from __future__ import annotations

from arena_hero_research.contracts import (
    AnalysisPlan,
    ExperimentDesign,
    Factor,
    Hypothesis,
    HypothesisDirection,
    MissingDataPolicy,
    MultipleComparisonPolicy,
    Outcome,
    OutcomeRole,
    Preregistration,
    ReplicationPlan,
    ResearchQuestion,
)


def make_preregistration(
    *,
    multiple_outcomes: bool = True,
    missing_policy: MissingDataPolicy = MissingDataPolicy.FAIL,
) -> Preregistration:
    outcomes = [
        Outcome(
            name="score",
            metric="mean-score",
            role=OutcomeRole.PRIMARY,
            unit="points",
            higher_is_better=True,
            missing_data_policy=missing_policy,
        )
    ]
    hypotheses = [
        Hypothesis(
            hypothesis_id="h-score",
            question_id="q-strategy",
            outcome_name="score",
            direction=HypothesisDirection.GREATER,
            minimum_effect=0.2,
        )
    ]
    if multiple_outcomes:
        outcomes.append(
            Outcome(
                name="latency",
                metric="mean-tick-latency",
                role=OutcomeRole.SECONDARY,
                unit="milliseconds",
                higher_is_better=False,
                missing_data_policy=missing_policy,
            )
        )
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h-latency",
                question_id="q-strategy",
                outcome_name="latency",
                direction=HypothesisDirection.LESS,
                minimum_effect=0.1,
            )
        )
    plan = AnalysisPlan(
        estimator="paired-mean-difference",
        effect_size="cohen-dz",
        confidence_level=0.95,
        ci_method="paired-bootstrap-percentile",
        bootstrap_samples=200,
        alpha=0.05,
        multiple_comparison_policy=(
            MultipleComparisonPolicy.BENJAMINI_HOCHBERG
            if multiple_outcomes
            else MultipleComparisonPolicy.NONE
        ),
        comparison_family="confirmatory-outcomes",
        missing_data_policy=missing_policy,
        target_power=0.8,
        minimum_detectable_effect=0.5,
        planned_sample_size=4,
    )
    design = ExperimentDesign(
        design_id="design-1",
        factors=(
            Factor(
                name="strategy",
                levels=("control", "candidate"),
                randomized=True,
                assignment_unit="seed-seat-pair",
                blocking_keys=("scenario", "seed", "seat"),
            ),
        ),
        outcomes=tuple(outcomes),
        pairing_keys=("scenario", "seed", "seat"),
        randomization_unit="seed-seat-pair",
        seed_policy="fixed-preregistered-list",
        replication_plan=ReplicationPlan(
            replications=4,
            minimum_successful=4,
            seeds=(11, 22, 33, 44),
            independent_seeds=True,
            environments=("local-reference",),
        ),
        analysis_plan=plan,
    )
    return Preregistration.create(
        question=ResearchQuestion(
            question_id="q-strategy",
            statement="Does the candidate strategy improve score without unacceptable latency?",
            estimand="paired average treatment effect over preregistered scenario/seed/seat blocks",
        ),
        hypotheses=tuple(hypotheses),
        design=design,
        registered_at="2026-08-10T20:00:00+08:00",
    )
