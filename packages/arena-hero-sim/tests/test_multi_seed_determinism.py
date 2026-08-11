"""P3-8 FFA/tournament/scenarios: multi-seed determinism acceptance.

The canonical reference workload is the multi-seed tournament surface of the
simulator: nine public scenarios bound to distinct seeds (101..109), including
two multi-player (multi-contestant) scenarios. These tests pin the P3-8
acceptance property "多 seed 确定性": the same multi-seed workload input always
produces the same run output (no RNG, no hidden ordering), every episode
preserves its case seed, distinct seeds yield distinct episode identity and
outcome, and the seed is a first-class binding of a case to its registered
scenario (a changed seed fails closed instead of being ignored).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from arena_hero_sim import (
    ReferenceWorkloadError,
    ReferenceWorkloadRunner,
    canonical_reference_scenario_registry,
    canonical_reference_workload_manifest,
    run_canonical_reference_workload,
)

MULTI_PLAYER_CASE_IDS = ("hostile-swap-rejection", "cross-player-contested-target")
EXPECTED_SEEDS = (101, 102, 103, 104, 105, 106, 107, 108, 109)


def test_multi_seed_run_is_deterministic_across_repeated_executions() -> None:
    first = run_canonical_reference_workload()
    second = run_canonical_reference_workload()

    assert first.sha256 == second.sha256
    assert first.episodes == second.episodes
    assert first.publishable
    assert first.issues == ()


def test_multi_seed_run_preserves_every_case_seed() -> None:
    manifest = canonical_reference_workload_manifest()
    run = run_canonical_reference_workload()

    assert tuple(case.seed for case in manifest.cases) == EXPECTED_SEEDS
    assert tuple(episode.seed for episode in run.episodes) == EXPECTED_SEEDS
    assert len({episode.seed for episode in run.episodes}) == len(EXPECTED_SEEDS)


def test_distinct_seeds_yield_distinct_episode_identity_and_outcome() -> None:
    run = run_canonical_reference_workload()

    episode_ids = tuple(episode.episode_id for episode in run.episodes)
    assert len(episode_ids) == len(set(episode_ids))

    final_worlds = tuple(episode.final_world_sha256 for episode in run.episodes)
    assert final_worlds == tuple(dict.fromkeys(final_worlds))

    assert {episode.status.value for episode in run.episodes} == {"complete"}
    assert all(episode.publishable for episode in run.episodes)
    assert all(not episode.errors for episode in run.episodes)


def test_multi_contestant_cases_execute_with_all_contestants() -> None:
    manifest = canonical_reference_workload_manifest()
    run = run_canonical_reference_workload()

    multi_player = {
        case.case_id: case.contestant_ids for case in manifest.cases if len(case.contestant_ids) > 1
    }
    assert set(multi_player) == set(MULTI_PLAYER_CASE_IDS)
    for case_id in MULTI_PLAYER_CASE_IDS:
        assert set(multi_player[case_id]) == {"alpha", "beta"}
        episode = next(item for item in run.episodes if item.case_id == case_id)
        assert episode.status.value == "complete"
        assert episode.publishable
        assert not episode.errors


def test_seed_is_a_first_class_binding_of_case_to_scenario() -> None:
    manifest = canonical_reference_workload_manifest()
    runner = ReferenceWorkloadRunner(canonical_reference_scenario_registry())
    case = replace(manifest.cases[0], seed=manifest.cases[0].seed + 1)
    wrong = replace(manifest, cases=(case, *manifest.cases[1:]))

    with pytest.raises(ReferenceWorkloadError, match="seed"):
        runner.run(wrong)
