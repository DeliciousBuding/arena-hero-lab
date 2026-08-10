"""Conformance and property tests for the bounded process executor.

This module locks the fail-closed guarantees that the dark review can break:
worker result identity/cardinality/order, operation-id idempotency, spawn and
module failures, and process-vs-in-process digest invariance under varying
worker counts and chunking. Worker probe scripts are written to the pytest
temp directory (outside the repository) and never committed; they run as
``sys.executable <script>`` with no grandchildren, so they are Windows
spawn-safe and leave no descendant processes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from arena_hero_bench.orchestration import (
    ExperimentId,
    IdempotencyConflictError,
    InMemoryArtifactStore,
    InMemoryExecutionLedger,
    LocalBatchExecutor,
    RunId,
    RunStatus,
    ShardId,
    ShardPlan,
)
from arena_hero_bench.process_executor import (
    BackendProcessSpec,
    ProcessExecutor,
    reference_engine_process_executor,
)
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
    scenario: ReferenceScenario, *, request_id: str, episode_id: str
) -> SimulationRequest:
    return SimulationRequest(
        request_id=request_id,
        episode_id=episode_id,
        config=SimulatorConfig(
            backend_id=REFERENCE_BACKEND_ID,
            engine_version=REFERENCE_ENGINE_VERSION,
            ruleset=REFERENCE_RULESET,
            seed=scenario.initial_world.seed,
            max_ticks=len(scenario.turns),
            protocol_version=REFERENCE_PROTOCOL_VERSION,
        ),
        initial_state_sha256=scenario.initial_world.sha256,
        contestant_ids=scenario.contestant_ids,
        input_artifact_sha256=scenario.sha256,
    )


def plan_for(requests: list[SimulationRequest], *, operation_id: str = "operation-1") -> ShardPlan:
    return ShardPlan.create(
        operation_id=operation_id,
        experiment_id=ExperimentId("experiment-1"),
        run_id=RunId("run-1"),
        shard_id=ShardId("shard-a"),
        requests=requests,
    )


def make_executor(
    scenarios: list[ReferenceScenario],
    *,
    max_workers: int = 1,
    per_task_timeout: float = 10.0,
) -> ProcessExecutor:
    return reference_engine_process_executor(
        scenarios,
        InMemoryArtifactStore(),
        InMemoryExecutionLedger(),
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


def write_script(tmp_path: Path, name: str, lines: Sequence[str]) -> str:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


_WORKER_BOILERPLATE = [
    "import json, sys",
    "from arena_hero_bench.process_executor import RESULT_ENVELOPE_VERSION, result_to_json",
    "from arena_hero_bench.process_worker import _build_backend, _work_from_json",
    "line = sys.stdin.buffer.readline()",
    "payload = json.loads(line)",
    "envelope, requests, scenarios = _work_from_json(payload)",
    "backend = _build_backend(envelope, requests, scenarios)",
    "results = [result_to_json(backend.simulate(request)) for request in requests]",
]

_OUTPUT_SUFFIX = [
    "output = {",
    "    'schema_version': RESULT_ENVELOPE_VERSION,",
    "    'operation_id': envelope.get('operation_id'),",
    "    'shard_id': envelope.get('shard_id'),",
    "    'plan_sha256': envelope.get('plan_sha256'),",
    "    'backend_id': envelope.get('backend_id'),",
    "    'engine_version': envelope.get('engine_version'),",
    "    'protocol_version': envelope.get('protocol_version'),",
    "    'results': results,",
    "    'errors': [],",
    "}",
    "sys.stdout.buffer.write(json.dumps(output).encode('utf-8') + b'\\n')",
]


def _simulate_worker(tmp_path: Path, name: str, *, transform: str) -> str:
    """Build a worker that simulates requests, then applies `transform` to `results`."""
    return write_script(
        tmp_path,
        name,
        [*_WORKER_BOILERPLATE, f"results = {transform}", *_OUTPUT_SUFFIX],
    )


def _tamper_identity_worker(tmp_path: Path) -> str:
    """Build a worker that reports a different operation_id than the work item."""
    lines = [
        *_WORKER_BOILERPLATE,
        "output = {",
        "    'schema_version': RESULT_ENVELOPE_VERSION,",
        "    'operation_id': 'operation-tampered',",
        "    'shard_id': envelope.get('shard_id'),",
        "    'plan_sha256': envelope.get('plan_sha256'),",
        "    'backend_id': envelope.get('backend_id'),",
        "    'engine_version': envelope.get('engine_version'),",
        "    'protocol_version': envelope.get('protocol_version'),",
        "    'results': results,",
        "    'errors': [],",
        "}",
        "sys.stdout.buffer.write(json.dumps(output).encode('utf-8') + b'\\n')",
    ]
    return write_script(tmp_path, "tamper-identity.py", lines)


def _assert_failed(result, *, error_match: str) -> None:
    assert result.status is RunStatus.FAILED
    assert result.publishable is False
    assert any(error_match in error for error in result.errors), result.errors


# --- P0: worker result identity / cardinality ---------------------------------


def test_worker_reversed_result_order_fails_closed(tmp_path: Path) -> None:
    scenarios = make_scenarios(2)
    requests = [
        request_for(scenarios[0], request_id="request-1", episode_id="episode-1"),
        request_for(scenarios[1], request_id="request-2", episode_id="episode-2"),
    ]
    script = _simulate_worker(tmp_path, "reverse-results.py", transform="list(reversed(results))")
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=script)},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )

    result = executor.execute(plan_for(requests))

    _assert_failed(result, error_match="identity does not match")
    with executor._lock:
        assert not executor._active


def test_worker_result_cardinality_mismatch_fails_closed(tmp_path: Path) -> None:
    scenarios = make_scenarios(2)
    requests = [
        request_for(scenarios[0], request_id="request-1", episode_id="episode-1"),
        request_for(scenarios[1], request_id="request-2", episode_id="episode-2"),
    ]
    script = _simulate_worker(tmp_path, "drop-result.py", transform="results[:1]")
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=script)},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )

    result = executor.execute(plan_for(requests))

    _assert_failed(result, error_match="cardinality")
    with executor._lock:
        assert not executor._active


# --- P0: operation-id idempotency ----------------------------------------------


def test_ledger_rejects_operation_id_reuse_with_different_plan() -> None:
    scenarios = make_scenarios(2)
    store = InMemoryArtifactStore()
    ledger = InMemoryExecutionLedger()
    executor = reference_engine_process_executor(
        scenarios, store, ledger, max_workers=1, per_task_timeout=10.0
    )
    plan_a = plan_for(
        [request_for(scenarios[0], request_id="request-a", episode_id="episode-a")],
        operation_id="operation-shared",
    )
    plan_b = plan_for(
        [request_for(scenarios[1], request_id="request-b", episode_id="episode-b")],
        operation_id="operation-shared",
    )

    first = executor.execute(plan_a)
    assert first.status is RunStatus.COMPLETE

    with pytest.raises(IdempotencyConflictError, match="different plan"):
        executor.execute(plan_b)

    assert len(store._objects) == 1


# --- P0: spawn / module failure fail closed ------------------------------------


def test_missing_worker_script_fails_closed(tmp_path: Path) -> None:
    scenarios = make_scenarios(1)
    script = str(tmp_path / "does-not-exist.py")
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=script)},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )

    result = executor.execute(
        plan_for([request_for(scenarios[0], request_id="request-1", episode_id="episode-1")])
    )

    # The child python exists, so Popen succeeds and the interpreter fails to
    # open the script and exits with code 2; the executor still fails closed
    # with the interpreter diagnostics.
    _assert_failed(result, error_match="exited with code 2")
    with executor._lock:
        assert not executor._active


def test_unimportable_worker_module_fails_closed() -> None:
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={
            REFERENCE_BACKEND_ID: BackendProcessSpec(
                backend_id=REFERENCE_BACKEND_ID,
                engine_version=REFERENCE_ENGINE_VERSION,
                protocol_versions=frozenset({REFERENCE_PROTOCOL_VERSION}),
                supported_features=REFERENCE_FEATURES,
                worker_module="arena_hero_bench.no_such_worker",
            )
        },
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )

    result = executor.execute(
        plan_for([request_for(scenarios[0], request_id="request-1", episode_id="episode-1")])
    )

    _assert_failed(result, error_match="exited with code")
    with executor._lock:
        assert not executor._active


# --- P0: process vs in-process digest / order / max_workers matrix --------------


@pytest.mark.parametrize("max_workers", [1, 2, 7])
@pytest.mark.parametrize("request_count", [1, 3])
def test_process_matches_in_process_digest_property(request_count: int, max_workers: int) -> None:
    scenarios = make_scenarios(request_count)
    requests = [
        request_for(scenario, request_id=f"request-{index}", episode_id=f"episode-{index}")
        for index, scenario in enumerate(scenarios)
    ]
    plan = plan_for(requests)

    process = make_executor(scenarios, max_workers=max_workers).execute(plan)

    registry = BackendRegistry()
    registry.register(ReferenceEngineBackend(tuple(scenarios)))
    in_process = LocalBatchExecutor(
        registry, InMemoryArtifactStore(), InMemoryExecutionLedger()
    ).execute(plan)

    assert process.content_sha256 == in_process.content_sha256
    assert process.status is in_process.status
    assert process.publishable is in_process.publishable
    assert process.request_ids == in_process.request_ids
    assert process.errors == in_process.errors


def test_repeated_scenario_across_chunks_matches_in_process() -> None:
    scenario = make_scenarios(1)[0]
    requests = [
        request_for(scenario, request_id=f"request-{index}", episode_id=f"episode-{index}")
        for index in range(5)
    ]
    plan = plan_for(requests)

    process = make_executor([scenario], max_workers=3).execute(plan)

    registry = BackendRegistry()
    registry.register(ReferenceEngineBackend((scenario,)))
    in_process = LocalBatchExecutor(
        registry, InMemoryArtifactStore(), InMemoryExecutionLedger()
    ).execute(plan)

    assert process.content_sha256 == in_process.content_sha256
    assert tuple(process.request_ids) == tuple(request.request_id for request in requests)
    assert process.status is in_process.status


# --- P1: output and envelope conformance ----------------------------------------


def test_stderr_overflow_fails_closed(tmp_path: Path) -> None:
    script = write_script(
        tmp_path,
        "noisy-stderr.py",
        ["import sys", "sys.stderr.write('e' * (2 * 1024 * 1024))"],
    )
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=script)},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        max_output_bytes=1024,
        scenario_provider=scenario_provider(scenarios),
    )

    result = executor.execute(
        plan_for([request_for(scenarios[0], request_id="request-1", episode_id="episode-1")])
    )

    _assert_failed(result, error_match="output exceeded")
    with executor._lock:
        assert not executor._active


@pytest.mark.parametrize("body", [["import sys"], ["print('line1')", "print('line2')"]])
def test_worker_empty_or_multiline_stdout_fails_closed(tmp_path: Path, body: list[str]) -> None:
    script = write_script(tmp_path, "bad-stdout.py", body)
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=script)},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )

    result = executor.execute(
        plan_for([request_for(scenarios[0], request_id="request-1", episode_id="episode-1")])
    )

    _assert_failed(result, error_match="exactly one envelope line")
    with executor._lock:
        assert not executor._active


def test_worker_non_utf8_stdout_fails_closed(tmp_path: Path) -> None:
    script = write_script(
        tmp_path,
        "non-utf8.py",
        ["import sys", "sys.stdout.buffer.write(b'\\xff\\xfe\\x00')"],
    )
    scenarios = make_scenarios(1)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=script)},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )

    result = executor.execute(
        plan_for([request_for(scenarios[0], request_id="request-1", episode_id="episode-1")])
    )

    _assert_failed(result, error_match="not valid UTF-8")
    with executor._lock:
        assert not executor._active


def test_worker_result_identity_field_mismatch_fails_closed(tmp_path: Path) -> None:
    scenarios = make_scenarios(1)
    script = _tamper_identity_worker(tmp_path)
    executor = ProcessExecutor(
        backend_specs={REFERENCE_BACKEND_ID: reference_spec(worker_script=script)},
        artifact_store=InMemoryArtifactStore(),
        ledger=InMemoryExecutionLedger(),
        max_workers=1,
        per_task_timeout=10.0,
        scenario_provider=scenario_provider(scenarios),
    )

    result = executor.execute(
        plan_for([request_for(scenarios[0], request_id="request-1", episode_id="episode-1")])
    )

    _assert_failed(result, error_match="identity field operation_id")
    with executor._lock:
        assert not executor._active
