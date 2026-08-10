"""Bounded local process executor for benchmark shards.

The executor runs each bounded work item in a fresh child Python process
through an explicit backend allowlist. A backend executes only when its id,
engine version, protocol, and requested capabilities are declared by a
:class:`BackendProcessSpec`; unknown backends or capabilities are rejected
immediately and never silently fall back to in-process execution.

Design notes
------------
- Work and result payloads use versioned envelope schemas
  (``arena.process.work.v1`` / ``arena.process.result.v1``) exchanged as one
  canonical JSON line over the child's stdin/stdout.
- Requests are submitted in fixed plan order and results are reassembled in
  that same order, so worker count never changes shard or merged digests.
- Each child is bounded by ``max_workers`` concurrency and a per-task timeout.
  Crashes, non-zero exits, invalid payloads, and timeouts fail closed: the
  affected requests become failed results and the shard is never publishable.
- Cancellation terminates tracked child processes so blocked waits return.
- This is a reference adapter, not a security sandbox: children inherit the
  parent environment, run as the same user, and are not resource-isolated.
  No shell, network, secrets, dynamic imports, or production data are used.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from arena_hero_bench.orchestration import (
    ArtifactStore,
    ExecutionLedger,
    OrchestrationError,
    ShardPlan,
    ShardResult,
    build_shard_result,
)
from arena_hero_sim.contracts import (
    RulesetRef,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorConfig,
)
from arena_hero_sim.reference import (
    REFERENCE_BACKEND_ID,
    REFERENCE_ENGINE_VERSION,
    REFERENCE_FEATURES,
    REFERENCE_PROTOCOL_VERSION,
)
from arena_hero_sim.reference_contracts import ReferenceScenario
from arena_hero_sim.serialization import canonical_json_bytes

WORK_ENVELOPE_VERSION = "arena.process.work.v1"
RESULT_ENVELOPE_VERSION = "arena.process.result.v1"
_REAP_TIMEOUT = 5.0
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class ProcessExecutorError(OrchestrationError):
    """Base error for process execution."""


class UnknownProcessBackendError(ProcessExecutorError):
    """The shard requested a backend that is not in the executor allowlist."""


class ProcessCapabilityError(ProcessExecutorError):
    """The shard requested an engine, protocol, or feature outside the allowlist."""


class ProcessWorkerError(ProcessExecutorError):
    """A worker returned an invalid or unexpected payload."""


class ProcessExecutorClosedError(ProcessExecutorError):
    """The executor was closed before execution started."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProcessExecutorError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ProcessExecutorError(f"{field_name} must not be empty")
    return normalized


def _int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProcessExecutorError(f"{field_name} must be an integer")
    return value


def _identifier(value: object, field_name: str) -> str:
    normalized = _text(value, field_name)
    if not _IDENTIFIER.fullmatch(normalized):
        raise ProcessExecutorError(f"{field_name} must be a lowercase portable identifier")
    return normalized


def _mapping(value: object, field_name: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise ProcessExecutorError(f"{field_name} must be an object")
    return value


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProcessExecutorError(f"{field_name} must be a number")
    return float(value)


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProcessExecutorError(f"{field_name} must be an array")
    return value


@dataclass(frozen=True, slots=True)
class BackendProcessSpec:
    """Static allowlist entry describing how one backend executes in a child.

    Exactly one of ``worker_module`` (preferred, invoked as
    ``python -m <module>``) or ``worker_script`` (explicit absolute script path,
    invoked as ``python <path>``) must be set. ``worker_script`` exists for
    tests and embedded experiments; it is not a dynamic import.
    """

    backend_id: str
    engine_version: str
    protocol_versions: frozenset[str]
    supported_features: frozenset[str]
    worker_module: str | None = None
    worker_script: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend_id", _identifier(self.backend_id, "backend_id"))
        engine = self.engine_version.strip()
        if not engine:
            raise ValueError("engine_version must not be empty")
        object.__setattr__(self, "engine_version", engine)
        protocols = frozenset(
            _identifier(item, "protocol_version") for item in self.protocol_versions
        )
        if not protocols:
            raise ValueError("at least one protocol version is required")
        object.__setattr__(self, "protocol_versions", protocols)
        object.__setattr__(self, "supported_features", frozenset(self.supported_features))
        if (self.worker_module is None) == (self.worker_script is None):
            raise ValueError("exactly one of worker_module or worker_script is required")
        if self.worker_module is not None:
            object.__setattr__(
                self, "worker_module", _identifier(self.worker_module, "worker_module")
            )
        else:
            script = self.worker_script
            assert script is not None
            object.__setattr__(self, "worker_script", str(Path(script).resolve()))

    def command(self) -> tuple[str, ...]:
        """Return the explicit argv used to spawn a child, never a shell."""
        if self.worker_module is not None:
            return (sys.executable, "-m", self.worker_module)
        assert self.worker_script is not None
        return (sys.executable, self.worker_script)


def reference_engine_process_executor(
    scenarios: Sequence[ReferenceScenario],
    artifact_store: ArtifactStore,
    ledger: ExecutionLedger,
    *,
    max_workers: int = 1,
    per_task_timeout: float = 60.0,
) -> ProcessExecutor:
    """Build a process executor whose only allowed backend is the reference engine.

    Scenarios are indexed by their canonical SHA-256 digest; the executor
    embeds each required scenario in the work envelope so a stateless child
    can reconstruct it and run the registered reference slice.
    """
    by_digest: dict[str, ReferenceScenario] = {}
    for scenario in scenarios:
        if scenario.sha256 in by_digest:
            raise ValueError(f"duplicate reference scenario digest: {scenario.sha256}")
        by_digest[scenario.sha256] = scenario
    specs = {
        REFERENCE_BACKEND_ID: BackendProcessSpec(
            backend_id=REFERENCE_BACKEND_ID,
            engine_version=REFERENCE_ENGINE_VERSION,
            protocol_versions=frozenset({REFERENCE_PROTOCOL_VERSION}),
            supported_features=REFERENCE_FEATURES,
            worker_module="arena_hero_bench.process_worker",
        )
    }
    return ProcessExecutor(
        backend_specs=specs,
        artifact_store=artifact_store,
        ledger=ledger,
        max_workers=max_workers,
        per_task_timeout=per_task_timeout,
        scenario_provider=lambda digest: by_digest.get(digest),
    )


def request_to_json(request: SimulationRequest) -> dict[str, object]:
    """Serialize one request into the portable work envelope shape."""
    return {
        "request_id": request.request_id,
        "episode_id": request.episode_id,
        "config": {
            "backend_id": request.config.backend_id,
            "engine_version": request.config.engine_version,
            "ruleset": {
                "name": request.config.ruleset.name,
                "version": request.config.ruleset.version,
                "rules_sha256": request.config.ruleset.rules_sha256,
            },
            "seed": request.config.seed,
            "max_ticks": request.config.max_ticks,
            "protocol_version": request.config.protocol_version,
            "deterministic": request.config.deterministic,
            "requested_features": sorted(request.config.requested_features),
            "parameters": dict(request.config.parameters),
        },
        "initial_state_sha256": request.initial_state_sha256,
        "contestant_ids": list(request.contestant_ids),
        "input_artifact_sha256": request.input_artifact_sha256,
        "labels": dict(request.labels),
    }


def request_from_json(payload: Mapping[str, object]) -> SimulationRequest:
    """Reconstruct one request, failing closed on any malformed field."""
    config = _mapping(payload.get("config"), "request config")
    ruleset = _mapping(config.get("ruleset"), "request ruleset")
    contestants = _sequence(payload.get("contestant_ids"), "contestant_ids")
    try:
        return SimulationRequest(
            request_id=_text(payload.get("request_id"), "request_id"),
            episode_id=_text(payload.get("episode_id"), "episode_id"),
            config=SimulatorConfig(
                backend_id=_text(config.get("backend_id"), "backend_id"),
                engine_version=_text(config.get("engine_version"), "engine_version"),
                ruleset=RulesetRef(
                    name=_text(ruleset.get("name"), "ruleset name"),
                    version=_text(ruleset.get("version"), "ruleset version"),
                    rules_sha256=_text(ruleset.get("rules_sha256"), "rules_sha256"),
                ),
                seed=_int(config.get("seed"), "seed"),
                max_ticks=_int(config.get("max_ticks"), "max_ticks"),
                protocol_version=_text(config.get("protocol_version"), "protocol_version"),
                deterministic=config.get("deterministic", True) is True,
                requested_features=frozenset(
                    _text(item, "requested feature")
                    for item in _sequence(
                        config.get("requested_features", ()), "requested_features"
                    )
                ),
                parameters={
                    str(key): str(item)
                    for key, item in _mapping(config.get("parameters", {}), "parameters").items()
                },
            ),
            initial_state_sha256=_text(payload.get("initial_state_sha256"), "initial_state_sha256"),
            contestant_ids=tuple(_text(item, "contestant_id") for item in contestants),
            input_artifact_sha256=(
                None
                if payload.get("input_artifact_sha256") is None
                else _text(payload.get("input_artifact_sha256"), "input_artifact_sha256")
            ),
            labels={
                str(key): str(item)
                for key, item in _mapping(payload.get("labels", {}), "labels").items()
            },
        )
    except (TypeError, ValueError) as exc:
        raise ProcessExecutorError(f"invalid request envelope: {exc}") from exc


def result_to_json(result: SimulationResult) -> dict[str, object]:
    """Serialize one result into the portable result envelope shape."""
    return {
        "request_id": result.request_id,
        "episode_id": result.episode_id,
        "backend_id": result.backend_id,
        "engine_version": result.engine_version,
        "rules_sha256": result.rules_sha256,
        "seed": result.seed,
        "status": result.status.value,
        "publishable": result.publishable,
        "ticks_completed": result.ticks_completed,
        "final_world_sha256": result.final_world_sha256,
        "metrics": dict(result.metrics),
        "artifact_refs": list(result.artifact_refs),
        "errors": list(result.errors),
    }


def result_from_json(payload: Mapping[str, object]) -> SimulationResult:
    """Reconstruct one result, failing closed on any malformed field."""
    try:
        return SimulationResult(
            request_id=_text(payload.get("request_id"), "request_id"),
            episode_id=_text(payload.get("episode_id"), "episode_id"),
            backend_id=_text(payload.get("backend_id"), "backend_id"),
            engine_version=_text(payload.get("engine_version"), "engine_version"),
            rules_sha256=_text(payload.get("rules_sha256"), "rules_sha256"),
            seed=_int(payload.get("seed"), "seed"),
            status=SimulationStatus(_text(payload.get("status"), "status")),
            publishable=payload.get("publishable") is True,
            ticks_completed=_int(payload.get("ticks_completed"), "ticks_completed"),
            final_world_sha256=(
                None
                if payload.get("final_world_sha256") is None
                else _text(payload.get("final_world_sha256"), "final_world_sha256")
            ),
            metrics={
                str(key): _number(item, f"metrics.{key}")
                for key, item in _mapping(payload.get("metrics", {}), "metrics").items()
            },
            artifact_refs=tuple(
                str(item) for item in _sequence(payload.get("artifact_refs", ()), "artifact_refs")
            ),
            errors=tuple(str(item) for item in _sequence(payload.get("errors", ()), "errors")),
        )
    except (TypeError, ValueError) as exc:
        raise ProcessExecutorError(f"invalid result envelope: {exc}") from exc


@dataclass(frozen=True, slots=True)
class ChunkOutcome:
    """Ordered engine results for one bounded work item."""

    results: tuple[SimulationResult, ...]


def _ordered_chunks(
    requests: Sequence[SimulationRequest], worker_count: int
) -> tuple[tuple[SimulationRequest, ...], ...]:
    count = max(1, min(worker_count, len(requests)))
    chunk_size = math.ceil(len(requests) / count)
    return tuple(
        tuple(requests[offset : offset + chunk_size])
        for offset in range(0, len(requests), chunk_size)
    )


def _failed_results(
    chunk: Sequence[SimulationRequest], message: str
) -> tuple[SimulationResult, ...]:
    return tuple(
        SimulationResult(
            request_id=request.request_id,
            episode_id=request.episode_id,
            backend_id=request.config.backend_id,
            engine_version=request.config.engine_version,
            rules_sha256=request.config.ruleset.rules_sha256,
            seed=request.config.seed,
            status=SimulationStatus.FAILED,
            publishable=False,
            ticks_completed=0,
            errors=(message,),
        )
        for request in chunk
    )


def _decode_stderr(payload: bytes) -> str:
    message = payload.decode("utf-8", errors="replace").strip()
    return message[:500] or "no diagnostics provided"


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()


def _work_envelope(
    spec: BackendProcessSpec,
    plan: ShardPlan,
    chunk: Sequence[SimulationRequest],
    scenario_provider: Callable[[str], ReferenceScenario | None] | None,
) -> dict[str, object]:
    requests: list[dict[str, object]] = []
    for request in chunk:
        entry = request_to_json(request)
        if request.input_artifact_sha256 is not None and scenario_provider is not None:
            scenario = scenario_provider(request.input_artifact_sha256)
            if scenario is not None:
                entry["scenario"] = scenario.to_dict()
        requests.append(entry)
    return {
        "schema_version": WORK_ENVELOPE_VERSION,
        "operation_id": plan.operation_id,
        "shard_id": plan.shard_id.value,
        "plan_sha256": plan.plan_sha256,
        "backend_id": spec.backend_id,
        "engine_version": spec.engine_version,
        "protocol_version": next(iter(spec.protocol_versions)),
        "requests": requests,
    }


def _parse_result_envelope(
    stdout: bytes, envelope: Mapping[str, object], chunk: Sequence[SimulationRequest]
) -> tuple[SimulationResult, ...]:
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProcessExecutorError("worker stdout is not valid UTF-8") from exc
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ProcessExecutorError("worker stdout must contain exactly one envelope line")
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ProcessExecutorError("worker result is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ProcessExecutorError("worker result must be an object")
    if payload.get("schema_version") != RESULT_ENVELOPE_VERSION:
        raise ProcessExecutorError("worker result has an unsupported schema_version")
    for key in (
        "operation_id",
        "shard_id",
        "plan_sha256",
        "backend_id",
        "engine_version",
        "protocol_version",
    ):
        if payload.get(key) != envelope.get(key):
            raise ProcessExecutorError(
                f"worker result identity field {key} does not match the work item"
            )
    results: list[SimulationResult] = []
    for item in _sequence(payload.get("results"), "results"):
        if not isinstance(item, Mapping):
            raise ProcessExecutorError("worker result entries must be objects")
        results.append(result_from_json(item))
    if len(results) != len(chunk):
        raise ProcessExecutorError("worker result cardinality does not match the work item")
    for request, result in zip(chunk, results, strict=True):
        identity = (
            result.request_id,
            result.episode_id,
            result.backend_id,
            result.engine_version,
            result.rules_sha256,
            result.seed,
        )
        expected = (
            request.request_id,
            request.episode_id,
            request.config.backend_id,
            request.config.engine_version,
            request.config.ruleset.rules_sha256,
            request.config.seed,
        )
        if identity != expected:
            raise ProcessExecutorError("worker result identity does not match the request")
    return tuple(results)


class ProcessExecutor:
    """Bounded local process executor with an explicit backend allowlist."""

    def __init__(
        self,
        *,
        backend_specs: Mapping[str, BackendProcessSpec],
        artifact_store: ArtifactStore,
        ledger: ExecutionLedger,
        max_workers: int = 1,
        per_task_timeout: float = 60.0,
        scenario_provider: Callable[[str], ReferenceScenario | None] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if per_task_timeout <= 0:
            raise ValueError("per_task_timeout must be positive")
        specs = dict(backend_specs)
        if not specs:
            raise ValueError("at least one backend spec is required")
        self._specs = specs
        self.artifact_store = artifact_store
        self.ledger = ledger
        self.max_workers = max_workers
        self.per_task_timeout = per_task_timeout
        self._scenario_provider = scenario_provider
        self._active: set[subprocess.Popen[bytes]] = set()
        self._lock = threading.Lock()
        self._closed = False

    def execute(self, plan: ShardPlan) -> ShardResult:
        """Execute one shard plan and return a content-addressed shard result."""
        if self._closed:
            raise ProcessExecutorClosedError("process executor is closed")
        resumed = self.ledger.resume(plan.operation_id, plan.plan_sha256)
        if resumed is not None:
            return resumed
        spec = self._validate_plan(plan)
        chunks = _ordered_chunks(plan.requests, min(self.max_workers, len(plan.requests)))
        outcomes: list[ChunkOutcome | None] = [None] * len(chunks)
        with ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="arena-process"
        ) as pool:
            futures = [
                pool.submit(self._run_chunk, spec, plan, chunk, index)
                for index, chunk in enumerate(chunks)
            ]
            for future in as_completed(futures):
                index, outcome = future.result()
                outcomes[index] = outcome
        results: list[SimulationResult] = []
        for _chunk, outcome in zip(chunks, outcomes, strict=True):
            if outcome is None:
                raise ProcessExecutorError("process executor did not complete all work items")
            results.extend(outcome.results)
        result = build_shard_result(plan, results, self.artifact_store)
        self.ledger.record(plan.operation_id, plan.plan_sha256, result)
        return result

    def close(self) -> None:
        """Terminate any live children so blocked waits return promptly."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            processes = list(self._active)
        for proc in processes:
            _terminate(proc)

    def __enter__(self) -> ProcessExecutor:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _validate_plan(self, plan: ShardPlan) -> BackendProcessSpec:
        backend_ids = {request.config.backend_id for request in plan.requests}
        if len(backend_ids) != 1:
            raise ProcessExecutorError("one shard cannot mix backend ids in process execution")
        protocols = {request.config.protocol_version for request in plan.requests}
        if len(protocols) != 1:
            raise ProcessExecutorError(
                "one shard cannot mix protocol versions in process execution"
            )
        backend_id = next(iter(backend_ids))
        spec = self._specs.get(backend_id)
        if spec is None:
            raise UnknownProcessBackendError(
                f"unknown backend for process execution: {backend_id}; no in-process fallback"
            )
        for request in plan.requests:
            config = request.config
            if config.engine_version != spec.engine_version:
                raise ProcessCapabilityError(
                    "requested engine_version "
                    f"{config.engine_version!r} does not match the process spec "
                    f"{spec.engine_version!r}"
                )
            if config.protocol_version not in spec.protocol_versions:
                raise ProcessCapabilityError(
                    f"unsupported protocol version for process execution: {config.protocol_version}"
                )
            missing = config.requested_features - spec.supported_features
            if missing:
                raise ProcessCapabilityError(
                    "unsupported capabilities for process execution: " + ", ".join(sorted(missing))
                )
            if request.input_artifact_sha256 is not None:
                scenario = (
                    None
                    if self._scenario_provider is None
                    else self._scenario_provider(request.input_artifact_sha256)
                )
                if scenario is None:
                    raise ProcessExecutorError(
                        "request input scenario is not registered with the "
                        f"process executor: {request.input_artifact_sha256}"
                    )
        return spec

    def _run_chunk(
        self,
        spec: BackendProcessSpec,
        plan: ShardPlan,
        chunk: Sequence[SimulationRequest],
        index: int,
    ) -> tuple[int, ChunkOutcome]:
        try:
            outcome = self._execute_chunk(spec, plan, chunk)
        except Exception as exc:
            outcome = ChunkOutcome(_failed_results(chunk, f"process executor error: {exc}"))
        return index, outcome

    def _execute_chunk(
        self, spec: BackendProcessSpec, plan: ShardPlan, chunk: Sequence[SimulationRequest]
    ) -> ChunkOutcome:
        envelope = _work_envelope(spec, plan, chunk, self._scenario_provider)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        proc = subprocess.Popen(
            spec.command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            creationflags=creationflags,
        )
        with self._lock:
            self._active.add(proc)
        try:
            try:
                stdout, stderr = proc.communicate(
                    canonical_json_bytes(envelope) + b"\n", timeout=self.per_task_timeout
                )
            except subprocess.TimeoutExpired:
                _terminate(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=_REAP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                return ChunkOutcome(
                    _failed_results(
                        chunk,
                        f"worker exceeded per-task timeout of {self.per_task_timeout:g} seconds",
                    )
                )
            if proc.returncode != 0:
                return ChunkOutcome(
                    _failed_results(
                        chunk,
                        f"worker exited with code {proc.returncode}: {_decode_stderr(stderr)}",
                    )
                )
            try:
                results = _parse_result_envelope(stdout, envelope, chunk)
            except ProcessExecutorError as exc:
                return ChunkOutcome(_failed_results(chunk, f"invalid worker payload: {exc}"))
            return ChunkOutcome(results)
        finally:
            with self._lock:
                self._active.discard(proc)
