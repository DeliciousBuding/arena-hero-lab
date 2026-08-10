from __future__ import annotations

from dataclasses import replace

import pytest

from arena_hero_research.assignment import AssignmentUnit, generate_assignments
from arena_hero_research.lifecycle import LifecycleError, ResearchLifecycle, ResearchPhase
from arena_hero_sim.serialization import content_sha256

from .research_fixtures import make_preregistration


def _assignment(preregistration, *, suffix: str = ""):
    units = tuple(
        AssignmentUnit(
            scenario_id=f"scenario-{letter}{suffix}",
            seat=seat,
            block_id=f"block-{letter}{suffix}",
        )
        for letter in ("a", "b")
        for seat in range(4)
    )
    return generate_assignments(preregistration, units, treatment_factor="strategy")


def test_lifecycle_advances_and_freezes_confirmatory_commitment() -> None:
    preregistration = make_preregistration()
    assignment = _assignment(preregistration)
    lifecycle = ResearchLifecycle.create(
        study_id="study-1",
        preregistration=preregistration,
        assignment=assignment,
    )
    exploratory = lifecycle.transition(
        ResearchPhase.EXPLORATORY,
        preregistration=preregistration,
        assignment=assignment,
    )
    confirmatory = exploratory.transition(
        ResearchPhase.CONFIRMATORY,
        preregistration=preregistration,
        assignment=assignment,
    )
    replication = confirmatory.transition(
        ResearchPhase.REPLICATION,
        preregistration=preregistration,
        assignment=assignment,
    )

    assert confirmatory.confirmatory_freeze_sha256 is not None
    assert replication.confirmatory_freeze_sha256 == confirmatory.confirmatory_freeze_sha256
    assert replication.verify()
    assert ResearchLifecycle.from_dict(replication.to_dict()) == replication


def test_lifecycle_rejects_skips_and_assignment_change_after_creation() -> None:
    preregistration = make_preregistration()
    assignment = _assignment(preregistration)
    lifecycle = ResearchLifecycle.create(
        study_id="study-1", preregistration=preregistration, assignment=assignment
    )
    with pytest.raises(LifecycleError, match="one step"):
        lifecycle.transition(
            ResearchPhase.CONFIRMATORY,
            preregistration=preregistration,
            assignment=assignment,
        )

    changed_assignment = _assignment(preregistration, suffix="-new")
    with pytest.raises(LifecycleError, match="assignment changed"):
        lifecycle.transition(
            ResearchPhase.EXPLORATORY,
            preregistration=preregistration,
            assignment=changed_assignment,
        )


def test_confirmatory_freeze_rejects_preregistration_or_plan_mutation() -> None:
    preregistration = make_preregistration()
    assignment = _assignment(preregistration)
    lifecycle = ResearchLifecycle.create(
        study_id="study-1", preregistration=preregistration, assignment=assignment
    ).transition(
        ResearchPhase.EXPLORATORY,
        preregistration=preregistration,
        assignment=assignment,
    )

    changed_plan = replace(
        preregistration.design.analysis_plan,
        alpha=0.04,
    )
    changed_design = replace(preregistration.design, analysis_plan=changed_plan)
    changed_preregistration = preregistration.create(
        question=preregistration.question,
        hypotheses=preregistration.hypotheses,
        design=changed_design,
        registered_at=preregistration.registered_at,
    )
    changed_assignment = _assignment(changed_preregistration)

    with pytest.raises(LifecycleError, match="preregistration changed"):
        lifecycle.transition(
            ResearchPhase.CONFIRMATORY,
            preregistration=changed_preregistration,
            assignment=changed_assignment,
        )


def test_verify_against_rejects_coherently_rehashed_false_freeze() -> None:
    preregistration = make_preregistration()
    assignment = _assignment(preregistration)
    confirmatory = (
        ResearchLifecycle.create(
            study_id="study-1", preregistration=preregistration, assignment=assignment
        )
        .transition(
            ResearchPhase.EXPLORATORY,
            preregistration=preregistration,
            assignment=assignment,
        )
        .transition(
            ResearchPhase.CONFIRMATORY,
            preregistration=preregistration,
            assignment=assignment,
        )
    )
    provisional = replace(
        confirmatory,
        confirmatory_freeze_sha256="f" * 64,
        canonical_sha256="0" * 64,
    )
    tampered = replace(provisional, canonical_sha256=content_sha256(provisional.payload()))

    assert tampered.verify()
    assert not tampered.verify_against(
        preregistration=preregistration,
        assignment=assignment,
    )


def test_lifecycle_create_hashes_normalized_study_identity() -> None:
    preregistration = make_preregistration()
    assignment = _assignment(preregistration)
    padded = ResearchLifecycle.create(
        study_id=" study-1 ",
        preregistration=preregistration,
        assignment=assignment,
    )
    canonical = ResearchLifecycle.create(
        study_id="study-1",
        preregistration=preregistration,
        assignment=assignment,
    )

    assert padded == canonical
    assert padded.verify()
