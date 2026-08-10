from __future__ import annotations

from dataclasses import replace

import pytest

from arena_hero_sim import (
    CANONICAL_REFERENCE_WORKLOAD_SHA256,
    KnownAnswer,
    ReferenceScenarioRegistry,
    ReferenceWorkloadError,
    ReferenceWorkloadRunner,
    SimulationStatus,
    VerifiedReferenceScenario,
    canonical_reference_scenario_registry,
    canonical_reference_workload_manifest,
    compare_workload_runs,
    run_canonical_reference_workload,
)

EXPECTED_FINAL_WORLD_DIGESTS = {
    "cross-player-contested-target": "0bf8b8990edf48745ef5840f771a1f3bd1449e2dd5ae23d82d254d6f2d5117f3",
    "failed-occupant-blocks-dependent": "9cb6a95d829b11f502c03262bbf59aeafcae97b30a6188e041b291d96b1feb2d",
    "friendly-swap": "a75efca161508ba60d7ee52c4a36f4b8cca3f5082284f45bc85e7ef11b5541a9",
    "friendly-three-unit-cycle": "f4b74d2007d3c6113d00ce301b6bb7369293a0a88f47af67936ea3e024841216",
    "harvest-deposit-golden": "51b0c8138eb0aaa3ebf80ebc4e1c812ebc74a439164f85267fb986ca22ae3a8e",
    "hostile-swap-rejection": "0a5fb032b8966d26cc5051d8e55a18d8ed7afa4f69b90ed870dc89eab8fff6b7",
    "independent-moves": "0ef6598b12e7e98cc4de3012e50d2dcccbc11c5259105b1e64ad9b23d6757abc",
    "linear-dependency-chain": "5819747db093915a16c57bd045bdcefd7bf3c77df46cd3a7864db0dfbc32e552",
    "uuid-raw-byte-tie-break": "b370e18df55d13fca4776406dad9ee9db5d879aa5674d0b569f2f9aa96deb420",
}
EXPECTED_CASE_ORDER = (
    "independent-moves",
    "linear-dependency-chain",
    "friendly-swap",
    "friendly-three-unit-cycle",
    "hostile-swap-rejection",
    "cross-player-contested-target",
    "failed-occupant-blocks-dependent",
    "uuid-raw-byte-tie-break",
    "harvest-deposit-golden",
)


def test_canonical_manifest_and_known_answers_are_frozen() -> None:
    manifest = canonical_reference_workload_manifest()
    run = run_canonical_reference_workload()

    assert manifest.sha256 == CANONICAL_REFERENCE_WORKLOAD_SHA256
    assert manifest.episode_count == 9
    assert tuple(case.case_id for case in manifest.cases) == EXPECTED_CASE_ORDER
    assert {episode.case_id: episode.final_world_sha256 for episode in run.episodes} == (
        EXPECTED_FINAL_WORLD_DIGESTS
    )
    assert all(episode.status is SimulationStatus.COMPLETE for episode in run.episodes)
    assert all(episode.publishable for episode in run.episodes)


def test_manifest_digest_is_stable_across_mapping_reordering() -> None:
    manifest = canonical_reference_workload_manifest()
    rebuilt = replace(
        manifest,
        cases=tuple(
            replace(
                case,
                parameters=dict(reversed(tuple(case.parameters.items()))),
                labels=dict(reversed(tuple(case.labels.items()))),
            )
            for case in manifest.cases
        ),
        metadata=dict(reversed(tuple(manifest.metadata.items()))),
    )

    assert rebuilt.sha256 == manifest.sha256
    assert rebuilt.to_dict() == manifest.to_dict()


def test_runner_is_batch_size_invariant() -> None:
    one = run_canonical_reference_workload(batch_size=1)
    two = run_canonical_reference_workload(batch_size=2)
    maximum = run_canonical_reference_workload(batch_size=1024)

    assert one.sha256 == two.sha256 == maximum.sha256
    assert one.episodes == two.episodes == maximum.episodes


def test_runner_rejects_missing_corrupt_and_misbound_scenarios() -> None:
    manifest = canonical_reference_workload_manifest()
    complete = run_canonical_reference_workload()
    scenario = canonical_reference_scenario_registry().scenarios[0]
    episode = complete.episodes[0]
    answer = KnownAnswer(
        scenario_id=scenario.scenario_id,
        scenario_sha256=scenario.sha256,
        initial_world_sha256=scenario.initial_world.sha256,
        status=episode.status,
        ticks_completed=episode.ticks_completed,
        final_world_sha256=str(episode.final_world_sha256),
        metrics=episode.metrics,
        required_artifact_refs=episode.artifact_refs,
    )
    record = VerifiedReferenceScenario(
        scenario_sha256=scenario.sha256,
        initial_world_sha256=scenario.initial_world.sha256,
        scenario=scenario,
        known_answer=answer,
    )
    runner = ReferenceWorkloadRunner(ReferenceScenarioRegistry((record,)))

    with pytest.raises(ReferenceWorkloadError, match="missing registered scenario"):
        runner.run(manifest)
    with pytest.raises(ValueError, match="scenario bytes"):
        VerifiedReferenceScenario(
            scenario_sha256="f" * 64,
            initial_world_sha256=scenario.initial_world.sha256,
            scenario=scenario,
            known_answer=answer,
        )

    wrong_world = replace(
        manifest.cases[0],
        initial_state_sha256=manifest.cases[0].scenario_sha256,
    )
    misbound = replace(manifest, cases=(wrong_world, *manifest.cases[1:]))
    with pytest.raises(ReferenceWorkloadError, match="initial world digest"):
        ReferenceWorkloadRunner(canonical_reference_scenario_registry()).run(misbound)


def test_runner_rejects_order_repetition_and_partial_budget_drift() -> None:
    manifest = canonical_reference_workload_manifest()
    runner = ReferenceWorkloadRunner(canonical_reference_scenario_registry())

    reordered = replace(manifest, cases=tuple(reversed(manifest.cases)))
    assert reordered.sha256 != manifest.sha256
    with pytest.raises(ReferenceWorkloadError, match="known-answer mismatch"):
        runner.run(reordered)

    repeated_first = replace(manifest.cases[0], repetitions=2)
    repeated = replace(manifest, cases=(repeated_first, *manifest.cases[1:]))
    assert repeated.sha256 != manifest.sha256
    with pytest.raises(ReferenceWorkloadError, match="known-answer mismatch"):
        runner.run(repeated)

    harvest = replace(manifest.cases[-1], max_ticks=1)
    partial = replace(manifest, cases=(*manifest.cases[:-1], harvest))
    with pytest.raises(ReferenceWorkloadError, match="tick budget"):
        runner.run(partial)


def _candidate_with_different_backend():
    reference = run_canonical_reference_workload()
    backend = replace(
        reference.backend,
        backend_id="optimized-candidate",
        engine_version="9.0-candidate",
    )
    episodes = tuple(
        replace(
            episode,
            request_id=f"candidate-request-{index}",
            backend_id=backend.backend_id,
            engine_version=backend.engine_version,
        )
        for index, episode in enumerate(reference.episodes)
    )
    return reference, replace(reference, backend=backend, episodes=episodes)


def test_comparator_accepts_semantics_with_different_backend_and_request_ids() -> None:
    reference, candidate = _candidate_with_different_backend()

    report = compare_workload_runs(reference, candidate)

    assert report.passed
    assert report.publishable
    assert report.mismatches == ()
    assert report.reference_run_sha256 != report.candidate_run_sha256


@pytest.mark.parametrize(
    ("field_name", "new_value"),
    [
        ("case_id", "tampered-case"),
        ("repetition", 7),
        ("status", SimulationStatus.PARTIAL),
        ("publishable", False),
        ("rules_sha256", "f" * 64),
        ("seed", 999),
        ("ticks_completed", 999),
        ("final_world_sha256", "e" * 64),
        ("artifact_refs", ("replay-sha256:" + "d" * 64,)),
    ],
)
def test_comparator_rejects_each_semantic_field(field_name: str, new_value: object) -> None:
    reference, candidate = _candidate_with_different_backend()
    tampered = replace(candidate.episodes[0], **{field_name: new_value})
    candidate = replace(candidate, episodes=(tampered, *candidate.episodes[1:]))

    report = compare_workload_runs(reference, candidate)

    assert not report.passed
    assert field_name in {mismatch.field for mismatch in report.mismatches}


def test_comparator_rejects_metric_nan_reordering_duplicates_and_missing_episode() -> None:
    reference, candidate = _candidate_with_different_backend()
    metric_tamper = replace(candidate.episodes[0], metrics={"events": float("nan")})
    nan_report = compare_workload_runs(
        reference,
        replace(candidate, episodes=(metric_tamper, *candidate.episodes[1:])),
    )
    assert {mismatch.field for mismatch in nan_report.mismatches} >= {
        "metrics.non_finite",
        "metrics",
    }

    reordered = replace(candidate, episodes=tuple(reversed(candidate.episodes)))
    assert "episode_alignment" in {
        mismatch.field for mismatch in compare_workload_runs(reference, reordered).mismatches
    }

    duplicated = replace(candidate, episodes=(candidate.episodes[0], *candidate.episodes))
    duplicate_fields = {
        mismatch.field for mismatch in compare_workload_runs(reference, duplicated).mismatches
    }
    assert {"episode_alignment", "candidate_episode_duplicates"} <= duplicate_fields

    missing = replace(candidate, episodes=candidate.episodes[:-1])
    missing_fields = {
        mismatch.field for mismatch in compare_workload_runs(reference, missing).mismatches
    }
    assert {"episode_alignment", "missing_episode"} <= missing_fields


def test_comparator_rejects_non_complete_and_unpublishable_candidate_run() -> None:
    reference, candidate = _candidate_with_different_backend()

    partial_status = replace(candidate.episodes[0], status=SimulationStatus.PARTIAL)
    non_complete = replace(candidate, episodes=(partial_status, *candidate.episodes[1:]))
    fields = {
        mismatch.field for mismatch in compare_workload_runs(reference, non_complete).mismatches
    }
    assert "status" in fields

    unpublishable = replace(
        candidate,
        publishable=False,
        issues=("episode 0 is not publishable",),
    )
    fields = {
        mismatch.field for mismatch in compare_workload_runs(reference, unpublishable).mismatches
    }
    assert {"candidate_run.publishable", "candidate_run.issues"} <= fields


def test_comparator_rejects_unsupported_backend_partial_coverage() -> None:
    reference, candidate = _candidate_with_different_backend()
    partial = replace(
        candidate,
        episodes=candidate.episodes[:3],
        publishable=False,
        issues=("backend lacks capability to execute the full workload",),
    )

    fields = {mismatch.field for mismatch in compare_workload_runs(reference, partial).mismatches}
    assert {"episode_alignment", "missing_episode", "candidate_run.publishable"} <= fields


def test_comparator_rejects_wrong_ruleset_and_workload_identity() -> None:
    reference, candidate = _candidate_with_different_backend()

    wrong_rules = replace(reference.ruleset, rules_sha256="e" * 64)
    wrong_ruleset_run = replace(candidate, ruleset=wrong_rules)
    fields = {
        mismatch.field
        for mismatch in compare_workload_runs(reference, wrong_ruleset_run).mismatches
    }
    assert "ruleset" in fields

    wrong_workload = replace(candidate, workload_id="unrelated-workload")
    fields = {
        mismatch.field for mismatch in compare_workload_runs(reference, wrong_workload).mismatches
    }
    assert "workload_id" in fields


def test_comparator_accepts_different_protocol_and_backend_capabilities() -> None:
    reference, candidate = _candidate_with_different_backend()
    backend = replace(
        candidate.backend,
        protocol_version="999.0",
        max_batch_size=1,
        supports_batch=False,
        supports_zero_copy=False,
    )
    candidate = replace(candidate, backend=backend)

    report = compare_workload_runs(reference, candidate)

    assert report.passed
