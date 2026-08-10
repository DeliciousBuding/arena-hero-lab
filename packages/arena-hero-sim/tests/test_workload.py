from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from arena_hero_sim import REFERENCE_RULESET
from arena_hero_sim.workload import WorkloadCase, WorkloadManifest

SCENARIO_A = "a" * 64
SCENARIO_B = "b" * 64
WORLD_A = "c" * 64
WORLD_B = "d" * 64


def workload() -> WorkloadManifest:
    return WorkloadManifest(
        workload_id="reference-movement-m4",
        workload_version="1.0.0",
        ruleset=REFERENCE_RULESET,
        cases=(
            WorkloadCase(
                case_id="dependency-chain",
                scenario_sha256=SCENARIO_A,
                initial_state_sha256=WORLD_A,
                seed=7,
                max_ticks=4,
                contestant_ids=("alpha",),
                repetitions=2,
                requested_features=frozenset({"reference-engine", "movement-dependency"}),
                parameters={"mode": "known-answer"},
                labels={"slice": "movement"},
            ),
            WorkloadCase(
                case_id="friendly-cycle",
                scenario_sha256=SCENARIO_B,
                initial_state_sha256=WORLD_B,
                seed=11,
                max_ticks=1,
                contestant_ids=("alpha",),
            ),
        ),
        metadata={"purpose": "reference workload", "production_claim": "false"},
    )


def test_workload_identity_is_canonical_and_roundtrips() -> None:
    original = workload()
    rebuilt = WorkloadManifest.from_dict(original.to_dict())

    assert rebuilt == original
    assert rebuilt.sha256 == original.sha256
    assert rebuilt.episode_count == 3
    assert len(original.sha256) == 64


def test_workload_identity_ignores_mapping_insertion_order() -> None:
    left = workload()
    first = left.cases[0]
    right = WorkloadManifest(
        workload_id=left.workload_id,
        workload_version=left.workload_version,
        ruleset=left.ruleset,
        cases=(
            WorkloadCase(
                case_id=first.case_id,
                scenario_sha256=first.scenario_sha256,
                initial_state_sha256=first.initial_state_sha256,
                seed=first.seed,
                max_ticks=first.max_ticks,
                contestant_ids=first.contestant_ids,
                repetitions=first.repetitions,
                requested_features=first.requested_features,
                parameters={"mode": "known-answer"},
                labels={"slice": "movement"},
            ),
            left.cases[1],
        ),
        metadata={"production_claim": "false", "purpose": "reference workload"},
    )

    assert right.sha256 == left.sha256


def test_workload_is_recursively_immutable_at_public_boundaries() -> None:
    manifest = workload()

    with pytest.raises(FrozenInstanceError):
        manifest.workload_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        manifest.metadata["purpose"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        manifest.cases[0].parameters["mode"] = "changed"  # type: ignore[index]


def test_duplicate_cases_and_invalid_counts_fail_closed() -> None:
    case = workload().cases[0]

    with pytest.raises(ValueError, match="case_id"):
        WorkloadManifest(
            workload_id="duplicate",
            workload_version="1",
            ruleset=REFERENCE_RULESET,
            cases=(case, case),
        )
    with pytest.raises(ValueError, match="repetitions"):
        WorkloadCase(
            case_id="invalid",
            scenario_sha256=SCENARIO_A,
            initial_state_sha256=WORLD_A,
            seed=0,
            max_ticks=1,
            contestant_ids=("alpha",),
            repetitions=0,
        )


def test_request_expansion_is_stable_and_backend_comparable() -> None:
    manifest = workload()
    reference = tuple(
        manifest.iter_requests(
            backend_id="reference-engine",
            engine_version="0.1.1-m4",
            protocol_version="arena.sim.protocol.v1",
        )
    )
    optimized = tuple(
        manifest.iter_requests(
            backend_id="optimized-python",
            engine_version="0.1.0",
            protocol_version="arena.sim.protocol.v1",
        )
    )

    assert [item.episode_id for item in reference] == [item.episode_id for item in optimized]
    assert [item.request_id for item in reference] != [item.request_id for item in optimized]
    assert len({item.episode_id for item in reference}) == 3
    assert all(item.episode_id.startswith("episode-") for item in reference)
    assert all(item.request_id.startswith("request-") for item in reference)
    assert reference[0].initial_state_sha256 == WORLD_A
    assert reference[0].input_artifact_sha256 == SCENARIO_A
    assert reference[0].labels["workload_sha256"] == manifest.sha256
    assert reference[0].config.ruleset == manifest.ruleset
    assert reference[0].config.requested_features == frozenset(
        {"reference-engine", "movement-dependency"}
    )


def test_maximum_length_portable_ids_expand_without_overflow() -> None:
    manifest = WorkloadManifest(
        workload_id="w" * 128,
        workload_version="1",
        ruleset=REFERENCE_RULESET,
        cases=(
            WorkloadCase(
                case_id="c" * 128,
                scenario_sha256=SCENARIO_A,
                initial_state_sha256=WORLD_A,
                seed=0,
                max_ticks=1,
                contestant_ids=("alpha",),
            ),
        ),
    )

    request = next(
        manifest.iter_requests(
            backend_id="b" * 128,
            engine_version="1",
            protocol_version="arena.sim.protocol.v1",
        )
    )

    assert len(request.episode_id) == 72
    assert len(request.request_id) == 72


def test_from_dict_rejects_non_object_case_and_boolean_integer() -> None:
    payload = workload().to_dict()
    payload["cases"] = ["not-an-object"]
    with pytest.raises(ValueError, match="every workload case"):
        WorkloadManifest.from_dict(payload)

    case = workload().cases[0].to_dict()
    case["seed"] = True
    with pytest.raises(ValueError, match="seed must be an integer"):
        WorkloadCase.from_dict(case)
