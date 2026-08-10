from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace

import pytest

from arena_hero_research.assignment import (
    AssignmentError,
    AssignmentManifest,
    AssignmentUnit,
    generate_assignments,
)

from .research_fixtures import make_preregistration


def _units() -> tuple[AssignmentUnit, ...]:
    return tuple(
        AssignmentUnit(scenario_id=scenario, seat=seat, block_id=f"block-{scenario[-1]}")
        for scenario in ("scenario-a", "scenario-b")
        for seat in range(4)
    )


def test_assignment_is_deterministic_round_trippable_and_preregistration_bound() -> None:
    preregistration = make_preregistration()
    first = generate_assignments(preregistration, _units(), treatment_factor="strategy")
    second = generate_assignments(
        preregistration, tuple(reversed(_units())), treatment_factor="strategy"
    )

    assert first == second
    assert first.verify()
    assert AssignmentManifest.from_dict(first.to_dict()) == first
    assert first.preregistration_sha256 == preregistration.canonical_sha256
    assert first.analysis_plan_sha256 == preregistration.design.analysis_plan.canonical_sha256()


def test_assignment_balances_every_replication_and_block() -> None:
    manifest = generate_assignments(make_preregistration(), _units(), treatment_factor="strategy")
    counts: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    for record in manifest.records:
        counts[(record.replication_index, record.unit.block_id)][record.treatment] += 1

    assert counts
    for treatment_counts in counts.values():
        assert treatment_counts == Counter({"control": 2, "candidate": 2})


def test_assignment_rejects_duplicates_tampering_and_nonrandomized_factor() -> None:
    preregistration = make_preregistration()
    with pytest.raises(AssignmentError, match="unique"):
        generate_assignments(
            preregistration,
            (_units()[0], _units()[0]),
            treatment_factor="strategy",
        )

    manifest = generate_assignments(preregistration, _units(), treatment_factor="strategy")
    assert not replace(manifest, canonical_sha256="f" * 64).verify()

    with pytest.raises(AssignmentError, match="not declared"):
        generate_assignments(preregistration, _units(), treatment_factor="unknown")
