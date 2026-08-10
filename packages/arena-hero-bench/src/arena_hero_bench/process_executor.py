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
  canonical JSON line over the child's stdin/stdout. Scenarios are carried once
  per envelope in a top-level map keyed by their content SHA-256; request
  entries only reference the digest.
- Requests are submitted in fixed plan order and results are reassembled in
  that same order, so worker count never changes shard or merged digests.
- Each child is bounded by ``max_workers`` concurrency and a per-task timeout.
  On timeout the whole process tree is reaped: POSIX uses a fresh session plus
  ``killpg``; Windows assigns each child to a Job Object (stdlib ``ctypes``)
  and terminates the job, falling back to terminate/kill with a bounded drain
  and pipe close when job assignment is unavailable. ``execute`` always returns
  a FAILED shard within a finite window, even when a worker spawns a
  grandchild that inherits the output pipes.
- ``stdout``/``stderr`` are streamed through bounded reader threads with a
  configurable hard byte cap (``max_output_bytes``); exceeding the cap fails
  closed instead of buffering unbounded output.
- Cancellation terminates tracked children (and their trees) so blocked waits
  return promptly; the spawn bookkeeping is lock-serialized so no new child can
  be spawned after ``close``.
- The allowlist constrains request routing only. Constructing a
  ``BackendProcessSpec`` grants the code-execution authority of the child
  process, so callers must treat specs as trusted configuration. This is a
  reference adapter, not a security sandbox: children inherit the parent
  environment, run as the same user, and are not resource-isolated. No shell,
  network, secrets, dynamic imports, or production data are used.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
_DEFAULT_MAX_OUTPUT_BYTES = 1_048_576
_READ_CHUNK = 65536
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

# --- Windows Job Object (stdlib ctypes; best effort) -------------------------
# The structures mirror JOBOBJECT_EXTENDED_LIMIT_INFORMATION on x64; on 32-bit
# Windows the layout differs, so job creation may fail and execution falls back
# to terminate/kill plus a bounded drain. KILL_ON_JOB_CLOSE terminates the whole
# process tree when the job handle is closed.


class _JobIoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _JobIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

_kernel32: Any = None
if os.name == "nt":  # pragma: no cover - exercised on Windows
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]


class _WindowsJob:
    """Best-effort Windows Job Object that kills a process tree on demand.

    Falls back to terminating only the direct child when job creation or
    assignment fails (for example when the parent already runs inside a
    non-nestable job). On non-Windows platforms every operation is a no-op.
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        if _kernel32 is None:
            return
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = _JobExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            _kernel32.CloseHandle(handle)
            return
        self._handle = int(handle)

    def assign(self, pid: int) -> bool:
        if _kernel32 is None or self._handle is None:
            return False
        process = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            return False
        try:
            return bool(_kernel32.AssignProcessToJobObject(self._handle, process))
        finally:
            _kernel32.CloseHandle(process)

    def terminate(self) -> bool:
        if _kernel32 is None or self._handle is None:
            return False
        return bool(_kernel32.TerminateJobObject(self._handle, 1))

    def close(self) -> None:
        if _kernel32 is not None and self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None


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

    The allowlist constrains request routing only: a spec is trusted
    configuration, and whoever constructs it effectively grants the child
    process code-execution authority. The executor is not a sandbox.
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
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> ProcessExecutor:
    """Build a process executor whose only allowed backend is the reference engine.

    Scenarios are indexed by their canonical SHA-256 digest; the executor
    embeds each required scenario once in the work envelope so a stateless
    child can reconstruct it and run the registered reference slice.
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
        max_output_bytes=max_output_bytes,
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


@dataclass(frozen=True, slots=True)
class _TrackedChild:
    """A spawned child plus its optional Windows Job Object."""

    proc: subprocess.Popen[bytes]
    job: _WindowsJob | None = None


class _BoundedPipeReader(threading.Thread):
    """Stream one child pipe with a hard memory cap; overflow discards.

    Once the cap is reached the reader keeps draining and discarding so the
    child never blocks on a full pipe, while memory stays bounded. Exceeding
    the cap is reported through :attr:`overflow` and the executor fails closed.
    """

    def __init__(self, stream: Any, *, max_bytes: int) -> None:
        super().__init__(daemon=True, name="arena-process-reader")
        self._stream = stream
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._chunks: list[bytes] = []
        self._total = 0
        self._overflow = False
        self._finished = threading.Event()

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(_READ_CHUNK)
                if not chunk:
                    break
                with self._lock:
                    if self._total < self._max_bytes:
                        remaining = self._max_bytes - self._total
                        if len(chunk) > remaining:
                            self._chunks.append(chunk[:remaining])
                            self._total += remaining
                            self._overflow = True
                        else:
                            self._chunks.append(chunk)
                            self._total += len(chunk)
                    else:
                        self._overflow = True
        except OSError:
            # The executor closed the pipe after a bounded drain window.
            pass
        finally:
            self._finished.set()

    def collected(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)

    @property
    def overflow(self) -> bool:
        with self._lock:
            return self._overflow

    def wait(self, timeout: float) -> bool:
        return self._finished.wait(timeout)


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
    scenarios: dict[str, object] = {}
    requests: list[dict[str, object]] = []
    for request in chunk:
        entry = request_to_json(request)
        digest = request.input_artifact_sha256
        if digest is not None:
            scenario = None if scenario_provider is None else scenario_provider(digest)
            if scenario is None:
                raise ProcessExecutorError(
                    f"request input scenario is not registered with the process executor: {digest}"
                )
            scenarios.setdefault(digest, scenario.to_dict())
            entry["scenario_sha256"] = digest
        requests.append(entry)
    return {
        "schema_version": WORK_ENVELOPE_VERSION,
        "operation_id": plan.operation_id,
        "shard_id": plan.shard_id.value,
        "plan_sha256": plan.plan_sha256,
        "backend_id": spec.backend_id,
        "engine_version": spec.engine_version,
        "protocol_version": next(iter(spec.protocol_versions)),
        "scenarios": scenarios,
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
    """Bounded local process executor with an explicit backend allowlist.

    Thread-safety contract: spawn bookkeeping (the active-child set and the
    closed flag) is lock-serialized, and ``close`` may be called from another
    thread to terminate active children. Concurrent ``execute`` calls on the
    same executor are safe for process bookkeeping, but the ledger and artifact
    store are single-writer contracts, so callers must serialize concurrent
    executions of the same executor themselves.
    """

    def __init__(
        self,
        *,
        backend_specs: Mapping[str, BackendProcessSpec],
        artifact_store: ArtifactStore,
        ledger: ExecutionLedger,
        max_workers: int = 1,
        per_task_timeout: float = 60.0,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
        scenario_provider: Callable[[str], ReferenceScenario | None] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if per_task_timeout <= 0:
            raise ValueError("per_task_timeout must be positive")
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        specs = dict(backend_specs)
        if not specs:
            raise ValueError("at least one backend spec is required")
        self._specs = specs
        self.artifact_store = artifact_store
        self.ledger = ledger
        self.max_workers = max_workers
        self.per_task_timeout = per_task_timeout
        self.max_output_bytes = max_output_bytes
        self._scenario_provider = scenario_provider
        self._active: set[_TrackedChild] = set()
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
        """Terminate active process trees so blocked waits return promptly.

        The closed flag is set under the spawn lock, and the active set is
        snapshotted under the same lock, so no child can be spawned after
        ``close`` returns.
        """
        if self._closed:
            return
        with self._lock:
            self._closed = True
            children = list(self._active)
            self._active.clear()
        for child in children:
            self._kill_tree(child)
            if child.job is not None:
                child.job.close()

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
        except Exception as exc:  # fail closed with diagnostics
            outcome = ChunkOutcome(_failed_results(chunk, f"process executor error: {exc}"))
        return index, outcome

    def _spawn(self, spec: BackendProcessSpec) -> _TrackedChild:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with self._lock:
            if self._closed:
                raise ProcessExecutorClosedError("process executor is closed")
            proc = subprocess.Popen(
                spec.command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                creationflags=creationflags,
                start_new_session=os.name == "posix",
            )
            job = _WindowsJob()
            if not job.assign(proc.pid):
                job.close()
                job = None
            tracked = _TrackedChild(proc=proc, job=job)
            self._active.add(tracked)
        return tracked

    def _kill_tree(self, tracked: _TrackedChild, *, force: bool = False) -> None:
        proc = tracked.proc
        if os.name == "posix":
            with suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL if force else signal.SIGTERM)
            return
        if tracked.job is not None and tracked.job.terminate():
            return
        if force:
            proc.kill()
        else:
            _terminate(proc)

    def _bounded_wait(self, tracked: _TrackedChild) -> None:
        try:
            tracked.proc.wait(timeout=_REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._kill_tree(tracked, force=True)
            with suppress(subprocess.TimeoutExpired):
                tracked.proc.wait(timeout=_REAP_TIMEOUT)

    def _execute_chunk(
        self, spec: BackendProcessSpec, plan: ShardPlan, chunk: Sequence[SimulationRequest]
    ) -> ChunkOutcome:
        envelope = _work_envelope(spec, plan, chunk, self._scenario_provider)
        tracked = self._spawn(spec)
        proc = tracked.proc
        stdout = proc.stdout
        stderr = proc.stderr
        stdin = proc.stdin
        assert stdout is not None and stderr is not None and stdin is not None
        stdout_reader = _BoundedPipeReader(stdout, max_bytes=self.max_output_bytes)
        stderr_reader = _BoundedPipeReader(stderr, max_bytes=self.max_output_bytes)
        stdout_reader.start()
        stderr_reader.start()
        timed_out = False
        try:
            try:
                stdin.write(canonical_json_bytes(envelope) + b"\n")
            except (BrokenPipeError, OSError):
                pass
            finally:
                with suppress(OSError):
                    stdin.close()
            try:
                proc.wait(timeout=self.per_task_timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_tree(tracked)
                self._bounded_wait(tracked)
            deadline = time.monotonic() + _REAP_TIMEOUT
            readers_finished = True
            for reader in (stdout_reader, stderr_reader):
                remaining = max(0.0, deadline - time.monotonic())
                if not reader.wait(remaining):
                    readers_finished = False
                    break
            if timed_out:
                return ChunkOutcome(
                    _failed_results(
                        chunk,
                        f"worker exceeded per-task timeout of {self.per_task_timeout:g} seconds",
                    )
                )
            if proc.returncode is None:
                return ChunkOutcome(
                    _failed_results(
                        chunk, "worker did not terminate within the bounded reap window"
                    )
                )
            if not readers_finished:
                return ChunkOutcome(
                    _failed_results(
                        chunk, "worker did not release output pipes within the bounded window"
                    )
                )
            if stdout_reader.overflow or stderr_reader.overflow:
                return ChunkOutcome(
                    _failed_results(
                        chunk,
                        f"worker output exceeded the {self.max_output_bytes} byte limit",
                    )
                )
            if proc.returncode != 0:
                return ChunkOutcome(
                    _failed_results(
                        chunk,
                        f"worker exited with code {proc.returncode}: "
                        f"{_decode_stderr(stderr_reader.collected())}",
                    )
                )
            try:
                results = _parse_result_envelope(stdout_reader.collected(), envelope, chunk)
            except ProcessExecutorError as exc:
                return ChunkOutcome(_failed_results(chunk, f"invalid worker payload: {exc}"))
            return ChunkOutcome(results)
        finally:
            for stream in (stdin, stdout, stderr):
                with suppress(OSError):
                    stream.close()
            if tracked.job is not None:
                tracked.job.close()
            with self._lock:
                self._active.discard(tracked)
