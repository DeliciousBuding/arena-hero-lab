from __future__ import annotations

import pytest

from arena_hero_research.assignment import AssignmentUnit, generate_assignments
from arena_hero_research.lifecycle import ResearchLifecycle, ResearchPhase
from arena_hero_research.planning import (
    MonteCarloPowerResult,
    PowerPlanningError,
    simulate_monte_carlo_power,
)

from .research_fixtures import make_preregistration


def _lifecycle():
    preregistration = make_preregistration()
    assignment = generate_assignments(
        preregistration,
        tuple(AssignmentUnit("scenario-a", seat, "block-a") for seat in range(4)),
        treatment_factor="strategy",
    )
    lifecycle = ResearchLifecycle.create(
        study_id="study-1", preregistration=preregistration, assignment=assignment
    )
    return preregistration, assignment, lifecycle


def test_monte_carlo_power_is_deterministic_and_round_trippable() -> None:
    preregistration, _, lifecycle = _lifecycle()
    first = simulate_monte_carlo_power(
        lifecycle=lifecycle,
        preregistration=preregistration,
        outcome_name="score",
        assumed_effect=1.0,
        assumed_standard_deviation=0.5,
        simulations=4000,
        simulation_seed=20260810,
    )
    second = simulate_monte_carlo_power(
        lifecycle=lifecycle,
        preregistration=preregistration,
        outcome_name="score",
        assumed_effect=1.0,
        assumed_standard_deviation=0.5,
        simulations=4000,
        simulation_seed=20260810,
    )

    assert first == second
    assert first.verify()
    assert first.estimated_power > 0.9
    assert "not an exact" in " ".join(first.limitations)
    assert MonteCarloPowerResult.from_dict(first.to_dict()) == first


def test_monte_carlo_known_answer_null_and_effect_cases() -> None:
    preregistration, _, lifecycle = _lifecycle()
    null = simulate_monte_carlo_power(
        lifecycle=lifecycle,
        preregistration=preregistration,
        outcome_name="score",
        assumed_effect=0.0,
        assumed_standard_deviation=1.0,
        simulations=6000,
        simulation_seed=19,
    )
    effect = simulate_monte_carlo_power(
        lifecycle=lifecycle,
        preregistration=preregistration,
        outcome_name="score",
        assumed_effect=2.0,
        assumed_standard_deviation=0.5,
        simulations=2000,
        simulation_seed=19,
    )

    assert 0.02 <= null.estimated_power <= 0.09
    assert effect.estimated_power > 0.99


def test_power_planning_is_forbidden_after_confirmatory_freeze() -> None:
    preregistration, assignment, lifecycle = _lifecycle()
    confirmatory = lifecycle.transition(
        ResearchPhase.EXPLORATORY,
        preregistration=preregistration,
        assignment=assignment,
    ).transition(
        ResearchPhase.CONFIRMATORY,
        preregistration=preregistration,
        assignment=assignment,
    )
    with pytest.raises(PowerPlanningError, match="forbidden"):
        simulate_monte_carlo_power(
            lifecycle=confirmatory,
            preregistration=preregistration,
            outcome_name="score",
            assumed_effect=1.0,
            assumed_standard_deviation=1.0,
            simulations=1000,
            simulation_seed=1,
        )
