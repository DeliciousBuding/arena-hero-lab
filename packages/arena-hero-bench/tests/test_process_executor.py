"""Tests for the bounded local process executor reference adapter."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from arena_hero_bench.manifest import ArtifactManifest, ArtifactStatus
from arena_hero_bench.orchestration import (
    ArtifactStore,
    ExecutionLedger,
    ExperimentId,
    InMemoryArtifactStore,
    InMemoryExecutionLedger,
    LocalBatchExecutor,
    RunId,
    RunStatus,
    ShardId,
    ShardPlan,
    ShardResult,
)
from arena_hero_bench.process_executor import (
    WORK_ENVELOPE_VERSION,
    BackendProcessSpec,
    ProcessCapabilityError,
    ProcessExecutor,
    ProcessExecutorClosedError,
    ProcessExecutorError,
    UnknownProcessBackendError,
    _work_envelope,
    reference_engine_process_executor,
    request_from_json,
    request_to_json,
    result_from_json,
    result_to_json,
)
from arena_hero_bench.process_worker import _work_from_json, scenario_from_dict
from arena_hero_bench.storage import FilesystemArtifactStore
from arena_hero_sim import (
    REFERENCE_BACKEND_ID,
    REFERENCE_ENGINE_VERSION,
    REFERENCE_FEATURES,
    REFERENCE_PROTOCOL_VERSION,
    REFERENCE_RULES,
    REFERENCE_RULESET,
    BackendRegistry,
    ReferenceActionKind,
    ReferenceCommand,
    ReferenceCore,
    ReferenceDirection,
    ReferenceEngineBackend,
    ReferencePlayer,
    ReferenceScenario,
    ReferenceTerrain,
    ReferenceTurn,
    ReferenceUnit,
    ReferenceWorld,
    RulesetRef,
    SimulationRequest,
    SimulatorConfig,
)

CORE_1 = "10000000-0000-4000-8000-000000000001"
UNIT_IDS = [
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
    "00000000-0000-4000-8000-000000000004",
    "00000000-0000-4000-8000-000000000005",
]


def scenario_for(seed: int, unit_id: str, scenario_id: str) -> ReferenceScenario:
    world = ReferenceWorld(
        tick=1,
        resolved_tick_count=0,
        rules_sha256=REFERENCE_RULES.sha256,
        seed=seed,
        rng_stream_position=0,
        players=(
            ReferencePlayer(
                id="alpha",
                username="Alpha",
                resources=5,
                core=ReferenceCore(CORE_1, (0, 0)),
                units=(ReferenceUnit(unit_id, "alpha", (3, 0)),),
            ),
        ),
        terrain=ReferenceTerrain(resource_cells=frozenset({(3, 0)})),
    )
    return ReferenceScenario(
        scenario_id=scenario_id,
        initial_world=world,
        contestant_ids=("alpha",),
        turns=(
            ReferenceTurn(1, (ReferenceCommand(unit_id, ReferenceActionKind.HARVEST),)),
            ReferenceTurn(
                2,
                (ReferenceCommand(unit_id, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),),
            ),
            ReferenceTurn(
                3,
                (ReferenceCommand(unit_id, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),),
            ),
            ReferenceTurn(
                4,
                (ReferenceCommand(unit_id, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),),
            ),
            ReferenceTurn(5, (ReferenceCommand(unit_id, ReferenceActionKind.DEPOSIT),)),
        ),
    )


def make_scenarios(count: int = 3) -> list[ReferenceScenario]:
    return [
        scenario_for(seed=7 + index, unit_id=UNIT_IDS[index], scenario_id=f"scenario-{index}")
        for index in range(count)
    ]


def request_for(
    scenario: ReferenceScenario,
    *,
    request_id: str = "request-1",
    episode_id: str = "episode-1",
    max_ticks: int | None = None,
    backend_id: str = REFERENCE_BACKEND_ID,
    engine_version: str = REFERENCE_ENGINE_VERSION,
    protocol_version: str = REFERENCE_PROTOCOL_VERSION,
    ruleset: RulesetRef = REFERENCE_RULESET,
    requested_features: frozenset[str] = frozenset(),
) -> SimulationRequest:
    return SimulationRequest(
        request_id=request_id,
        episode_id=episode_id,
        config=SimulatorConfig(
            backend_id=backend_id,
            engine_version=engine_version,
            ruleset=ruleset,
            seed=scenario.initial_world.seed,
            max_ticks=len(scenario.turns) if max_ticks is None else max_ticks,
            protocol_version=protocol_version,
            requested_features=requested_features,
        ),
        initial_state_sha256=scenario.initial_world.sha256,
        contestant_ids=scenario.contestant_ids,
        input_artifact_sha256=scenario.sha256,
    )


def plan_for(
    requests: list[SimulationRequest],
    *,
    operation_id: str = "operation-1",
    shard_id: str = "shard-a",
) -> ShardPlan:
    return ShardPlan.create(
        operation_id=operation_id,
        experiment_id=ExperimentId("experiment-1"),
        run_id=RunId("run-1"),
        shard_id=ShardId(shard_id),
        requests=requests,
    )


def make_executor(
    scenarios: list[ReferenceScenario],
    *,
    store: ArtifactStore | None = None,
    ledger: ExecutionLedger | None = None,
    max_workers: int = 1,
    per_task_timeout: float = 60.0,
) -> ProcessExecutor:
    artifact_store = store if store is not None else InMemoryArtifactStore()
    execution_ledger = ledger if ledger is not None else InMemoryExecutionLedger()
    return reference_engine_process_executor(
        scenarios,
        artifact_store,
        execution_ledger,
        max_workers=max_workers,
        per_task_timeout=per_task_timeout,
    )


def reference_spec(*, worker_script: str | None = None) -> BackendProcessSpec:
    return BackendProcessSpec(
        backend_id=REFERENCE_BACKEND_ID,
        engine_version=REFERENCE_ENGINE_VERSION,
        protocol_versions=frozenset({REFERENCE_PROTOCOL_VERSION}),
        supported_features=REFERENCE_FEATURES,
        worker_script=worker_script,
        worker_module=None if worker_script is not None else "arena_hero_bench.process_worker",
    )


def scenario_provider(
    scenarios: list[ReferenceScenario],
) -> Callable[[str], ReferenceScenario | None]:
    by_digest = {scenario.sha256: scenario for scenario in scenarios}
    return lambda digest: by_digest.get(digest)


def test_spec_requires_exactly_one_worker_target() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        BackendProcessSpec(
            backend_id="reference-engine",
            engine_version="0.1.0",
            protocol_versions=frozenset({"arena.sim.v1"}),
            supported_features=frozenset(),
        )
    with pytest.raises(ValueError, match="exactly one"):
        BackendProcessSpec(
            backend_id="reference-engine",
            engine_version="0.1.0",
            protocol_versions=frozenset({"arena.sim.v1"}),
            supported_features=frozenset(),
            worker_module="arena_hero_bench.process_worker",
            worker_script="worker.py",
        )


def test_spec_command_never_uses_a_shell() -> None:
    spec = reference_spec()
    assert spec.command() == (sys.executable, "-m", "arena_hero_bench.process_worker")


def test_unknown_backend_rejected_without_fallback() -> None:
    executor = make_executor(make_scenarios(1))
    scenario = make_scenarios(1)[0]
    request = request_for(scenario, backend_id="mystery-engine", engine_version="9.9.9")
    with pytest.raises(UnknownProcessBackendError, match="no in-process fallback"):
        executor.execute(plan_for([request]))


def test_engine_version_mismatch_rejected() -> None:
    executor = make_executor(make_scenarios(1))
    scenario = make_scenarios(1)[0]
    request = request_for(scenario, engine_version="0.0.1")
    with pytest.raises(ProcessCapabilityError, match="engine_version"):
        executor.execute(plan_for([request]))


def test_protocol_version_mismatch_rejected() -> None:
    executor = make_executor(make_scenarios(1))
    scenario = make_scenarios(1)[0]
    request = request_for(scenario, protocol_version="arena.sim.other")
    with pytest.raises(ProcessCapabilityError, match="protocol version"):
        executor.execute(plan_for([request]))


def test_requested_feature_rejected() -> None:
    executor = make_executor(make_scenarios(1))
    scenario = make_scenarios(1)[0]
    request = request_for(scenario, requested_features=frozenset({"quantum-acceleration"}))
    with pytest.raises(ProcessCapabilityError, match="unsupported capabilities"):
        executor.execute(plan_for([request]))


def test_mixed_backends_rejected() -> None:
    scenarios = make_scenarios(2)
    executor = make_executor(scenarios)
    requests = [
        request_for(scenarios[0], request_id="request-1"),
        request_for(scenarios[1], request_id="request-2", backend_id="other-engine"),
    ]
    with pytest.raises(ProcessExecutorError, match="cannot mix backend"):
        executor.execute(plan_for(requests))


def test_unregistered_scenario_rejected() -> None:
    executor = make_executor(make_scenarios(1))
    scenario = make_scenarios(1)[0]
    request = replace(request_for(scenario), input_artifact_sha256="f" * 64)
    with pytest.raises(ProcessExecutorError, match="not registered"):
        executor.execute(plan_for([request]))


def test_duplicate_scenario_digest_rejected() -> None:
    scenarios = make_scenarios(1)
    with pytest.raises(ValueError, match="duplicate"):
        reference_engine_process_executor(
            [scenarios[0], scenarios[0]], InMemoryArtifactStore(), InMemoryExecutionLedger()
        )


def test_happy_path_spawns_child_and_matches_in_process_digest() -> None:
    scenario = make_scenarios(1)[0]
    request = request_for(scenario, request_id="request-1", episode_id="episode-1")
    plan = plan_for([request])
    store = InMemoryArtifactStore()
    ledger = InMemoryExecutionLedger()
    executor = make_executor([scenario], store=store, ledger=ledger, max_workers=2)

    result = executor.execute(plan)

    assert result.status is RunStatus.COMPLETE
    assert result.publishable is True
    assert store.get(result.content_sha256)  # artifact round-trips

    registry = BackendRegistry()
    registry.register(ReferenceEngineBackend((scenario,)))
    expected = LocalBatchExecutor(
        registry, InMemoryArtifactStore(), InMemoryExecutionLedger()
    ).execute(plan)
    assert result.content_sha256 == expected.content_sha256
    assert result.artifact_ref == expected.artifact_ref
    assert result.request_ids == expected.request_ids
    assert result.errors == expected.errors


def test_max_workers_does_not_change_digest_or_order() -> None:
    scenarios = make_scenarios(5)
    requests = [
        request_for(scenario, request_id=f"request-{index}", episode_id=f"episode-{index}")
        for index, scenario in enumerate(scenarios)
    ]
    plan = plan_for(requests)

    store = InMemoryArtifactStore()
    single = make_executor(scenarios, store=store, max_workers=1).execute(plan)
    multi = make_executor(scenarios, max_workers=4).execute(plan)

    assert single.content_sha256 == multi.content_sha256
    assert tuple(single.request_ids) == tuple(request.request_id for request in requests)
    payload = json.loads(store.get(single.content_sha256))
    assert [item["request_id"] for item in payload["results"]] == [
        request.request_id for request in requests
    ]


def test_partial_shard_is_not_publishable() -> None:
    scenario = make_scenarios(1)[0]
    request = request_for(scenario, max_ticks=1)
    result = make_executor([scenario]).execute(plan_for([request]))
    assert result.status is RunStatus.PARTIAL
    assert result.publishable is False


def test_child_crash_fails_closed(tmp_path: Path) -> None:
    script = tmp_path / "crash.py"
    script.write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n", encoding="utf-8")
    scenarios = make_scenarios(1)
    store = InMemoryArtifactStore()
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=str(script))},
        artifact_store=store,
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )
    result = executor.execute(plan_for([request_for(scenarios[0])]))
    assert result.status is RunStatus.FAILED
    assert result.publishable is False
    assert any("exited with code 3" in error for error in result.errors)


def test_child_timeout_fails_closed(tmp_path: Path) -> None:
    script = tmp_path / "stall.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=str(script))},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=0.5,
        scenario_provider=scenario_provider(scenarios),
    )
    result = executor.execute(plan_for([request_for(scenarios[0])]))
    assert result.status is RunStatus.FAILED
    assert result.publishable is False
    assert any("timeout" in error for error in result.errors)


def test_child_invalid_payload_fails_closed(tmp_path: Path) -> None:
    script = tmp_path / "garbage.py"
    script.write_text("print('not-json')\n", encoding="utf-8")
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=str(script))},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )
    result = executor.execute(plan_for([request_for(scenarios[0])]))
    assert result.status is RunStatus.FAILED
    assert result.publishable is False
    assert any("invalid worker payload" in error for error in result.errors)


def test_child_wrong_envelope_fails_closed(tmp_path: Path) -> None:
    script = tmp_path / "wrong.py"
    script.write_text(
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'schema_version': 'arena.process.other.v1'}) + '\\n')\n",
        encoding="utf-8",
    )
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=str(script))},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )
    result = executor.execute(plan_for([request_for(scenarios[0])]))
    assert result.status is RunStatus.FAILED
    assert result.publishable is False


def test_resume_returns_same_result() -> None:
    scenario = make_scenarios(1)[0]
    plan = plan_for([request_for(scenario)])
    ledger = InMemoryExecutionLedger()
    executor = make_executor([scenario], ledger=ledger)
    first = executor.execute(plan)
    second = executor.execute(plan)
    assert first is second
    assert ledger.resume(plan.operation_id, plan.plan_sha256) is first


def test_filesystem_store_publishable_boundaries(tmp_path: Path) -> None:
    scenario = make_scenarios(1)[0]
    store = FilesystemArtifactStore(tmp_path / "store")
    executor = make_executor([scenario], store=store)

    complete_plan = plan_for([request_for(scenario)], operation_id="operation-complete")
    complete = executor.execute(complete_plan)
    assert complete.status is RunStatus.COMPLETE
    payload = store.get(complete.content_sha256)
    complete_manifest = ArtifactManifest.for_content(
        content=payload,
        schema_version="arena.lab.artifact.v1",
        generator_version="0.2.0",
        provenance={"source": "tests/process-executor.json"},
        source_build_sha256="a" * 64,
        status=ArtifactStatus.COMPLETE,
        publishable=True,
    )
    store.store_artifact(complete_manifest, payload)
    assert store.is_publishable(complete.content_sha256)

    partial_plan = plan_for([request_for(scenario, max_ticks=1)], operation_id="operation-partial")
    partial = executor.execute(partial_plan)
    assert partial.status is RunStatus.PARTIAL
    partial_payload = store.get(partial.content_sha256)
    partial_manifest = ArtifactManifest.for_content(
        content=partial_payload,
        schema_version="arena.lab.artifact.v1",
        generator_version="0.2.0",
        provenance={"source": "tests/process-executor.json"},
        source_build_sha256="a" * 64,
        status=ArtifactStatus.PARTIAL,
        publishable=False,
    )
    store.store_artifact(partial_manifest, partial_payload)
    assert not store.is_publishable(partial.content_sha256)


def test_cancel_terminates_active_children(tmp_path: Path) -> None:
    script = tmp_path / "stall-long.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=str(script))},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=60.0,
        scenario_provider=scenario_provider(scenarios),
    )
    plan = plan_for([request_for(scenarios[0])])

    def run() -> None:
        executor.execute(plan)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 15.0
    tracked = None
    while time.monotonic() < deadline:
        with executor._lock:
            active = list(executor._active)
        if active:
            tracked = active[0]
            break
        time.sleep(0.05)
    assert tracked is not None
    executor.close()
    thread.join(timeout=20.0)
    assert not thread.is_alive()
    assert tracked.proc.poll() is not None
    with executor._lock:
        assert not executor._active


def test_closed_executor_rejects_execution() -> None:
    scenario = make_scenarios(1)[0]
    executor = make_executor([scenario])
    executor.close()
    with pytest.raises(ProcessExecutorClosedError):
        executor.execute(plan_for([request_for(scenario)]))


def test_request_json_roundtrip() -> None:
    scenario = make_scenarios(1)[0]
    request = request_for(scenario, request_id="request-9", episode_id="episode-9")
    assert request_from_json(request_to_json(request)) == request


def test_result_json_roundtrip() -> None:
    scenario = make_scenarios(1)[0]
    backend = ReferenceEngineBackend((scenario,))
    result = backend.simulate(request_for(scenario))
    assert result_from_json(result_to_json(result)) == result


def test_scenario_roundtrip_preserves_digest() -> None:
    scenario = make_scenarios(1)[0]
    reconstructed = scenario_from_dict(scenario.to_dict())
    assert reconstructed.sha256 == scenario.sha256
    assert reconstructed == scenario


def test_grandchild_pipe_hold_timeout_bounded(tmp_path: Path) -> None:
    script = tmp_path / "grandchild.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=str(script))},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=0.5,
        scenario_provider=scenario_provider(scenarios),
    )
    started = time.monotonic()
    result = executor.execute(plan_for([request_for(scenarios[0])]))
    elapsed = time.monotonic() - started
    assert result.status is RunStatus.FAILED
    assert result.publishable is False
    assert any("timeout" in error for error in result.errors)
    assert elapsed < 15.0
    with executor._lock:
        assert not executor._active


def test_close_race_no_new_child_after_close(tmp_path: Path) -> None:
    script = tmp_path / "stall-long.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=str(script))},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=60.0,
        scenario_provider=scenario_provider(scenarios),
    )
    plan = plan_for([request_for(scenarios[0])])
    outcomes: list[object] = []

    def run() -> None:
        try:
            outcomes.append(executor.execute(plan))
        except Exception as exc:
            outcomes.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.05)
    executor.close()
    thread.join(timeout=20.0)
    assert not thread.is_alive()
    assert len(outcomes) == 1
    outcome = outcomes[0]
    if isinstance(outcome, ProcessExecutorClosedError):
        return
    assert isinstance(outcome, ShardResult), type(outcome).__name__
    assert outcome.status is RunStatus.FAILED
    with executor._lock:
        assert not executor._active


def test_oversized_output_fails_closed(tmp_path: Path) -> None:
    script = tmp_path / "noisy.py"
    script.write_text("import sys\nsys.stdout.write('x' * (2 * 1024 * 1024))\n", encoding="utf-8")
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=str(script))},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        max_output_bytes=1024,
        scenario_provider=scenario_provider(scenarios),
    )
    result = executor.execute(plan_for([request_for(scenarios[0])]))
    assert result.status is RunStatus.FAILED
    assert result.publishable is False
    assert any("output exceeded" in error for error in result.errors)


def test_envelope_dedup_scenarios() -> None:
    scenarios = make_scenarios(2)
    requests = [
        request_for(scenarios[0], request_id="request-1"),
        request_for(scenarios[0], request_id="request-2"),
        request_for(scenarios[1], request_id="request-3"),
    ]
    plan = plan_for(requests)
    spec = reference_spec()
    envelope = _work_envelope(spec, plan, requests, scenario_provider(scenarios))
    scenarios_map = cast(Mapping[str, object], envelope["scenarios"])
    request_entries = cast(Sequence[Mapping[str, object]], envelope["requests"])
    assert set(scenarios_map) == {scenarios[0].sha256, scenarios[1].sha256}
    refs = [cast(str, entry["scenario_sha256"]) for entry in request_entries]
    assert refs == [scenarios[0].sha256, scenarios[0].sha256, scenarios[1].sha256]
    assert all("scenario" not in entry for entry in request_entries)
    assert envelope["schema_version"] == WORK_ENVELOPE_VERSION


def test_worker_rejects_missing_scenario_ref() -> None:
    scenario = make_scenarios(1)[0]
    entry = request_to_json(request_for(scenario))
    entry["scenario_sha256"] = "f" * 64
    payload: dict[str, object] = {
        "schema_version": WORK_ENVELOPE_VERSION,
        "operation_id": "operation-1",
        "shard_id": "shard-a",
        "plan_sha256": "a" * 64,
        "backend_id": REFERENCE_BACKEND_ID,
        "engine_version": REFERENCE_ENGINE_VERSION,
        "protocol_version": REFERENCE_PROTOCOL_VERSION,
        "scenarios": {},
        "requests": [entry],
    }
    with pytest.raises(ProcessExecutorError, match="missing"):
        _work_from_json(payload)


def test_worker_rejects_scenario_digest_mismatch() -> None:
    scenario = make_scenarios(1)[0]
    entry = request_to_json(request_for(scenario))
    entry["scenario_sha256"] = "f" * 64
    payload: dict[str, object] = {
        "schema_version": WORK_ENVELOPE_VERSION,
        "operation_id": "operation-1",
        "shard_id": "shard-a",
        "plan_sha256": "a" * 64,
        "backend_id": REFERENCE_BACKEND_ID,
        "engine_version": REFERENCE_ENGINE_VERSION,
        "protocol_version": REFERENCE_PROTOCOL_VERSION,
        "scenarios": {"f" * 64: scenario.to_dict()},
        "requests": [entry],
    }
    with pytest.raises(ProcessExecutorError, match="digest"):
        _work_from_json(payload)
