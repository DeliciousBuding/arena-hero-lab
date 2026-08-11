"""Bounded offline replay soak harness (P6-4).

The soak driver repeatedly replays the P6-2/P6-3 differential corpus and the
canonical reference workload through the process executor for a bounded,
configurable number of rounds. Every round monitors the resources that a 24h
soak is meant to catch drifting:

- open handles / file descriptors (``RESOURCE_LEAK``),
- per-step content digests against the first successful round (``DIGEST_DRIFT``),
- uncaught step exceptions (``STEP_EXCEPTION``),
- leftover descendant processes after a round (``PROCESS_RESIDUE``).

Any anomaly fails the whole soak with a classification; a clean soak reports
``status=pass`` with every per-round step digest stable. The run is bounded by
``rounds`` and an optional ``max_duration_seconds`` hard cap, so the committed
manifest is a seconds-scale reproducible skeleton and a 24h run is the same
manifest with a larger round count and cap.

The corpus and canonical verification are reused, never reimplemented:
``differential`` and ``kpi_differential`` steps call the P6-2/P6-3 content
addressed classifiers through their versioned run manifests, and the
``process_executor`` step runs the sim package's frozen canonical reference
workload through :func:`reference_engine_process_executor`. The Python-agent
side always goes through the versioned offline importer inside those
classifiers.

Trust and attestation notes
---------------------------
- Step kinds are strictly validated; a manifest can never request injected
  steps. ``run_soak`` accepts a private ``step_factory`` seam for reverse
  validation tests; any report that used injected steps is marked
  ``injected_steps=true`` and ``attested=false`` and is never attestation-grade.
- Resource probes use the platform primitives available without new
  dependencies (``/proc`` on POSIX, Toolhelp32/``GetProcessHandleCount`` on
  Windows via stdlib ``ctypes``) and fail closed on platforms where a probe
  cannot be produced.
- Per-round step digests are deterministic content anchors. The report
  envelope hash covers the emitted payload including wall-clock timings, so
  two runs of the same manifest are not byte-identical even though every
  digest anchor is.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from arena_hero_bench.differential import (
    DifferentialStatus,
    run_differential_from_manifest,
)
from arena_hero_bench.kpi_differential import run_kpi_differential_from_manifest
from arena_hero_bench.orchestration import (
    ExperimentId,
    InMemoryArtifactStore,
    InMemoryExecutionLedger,
    RunId,
    RunStatus,
    ShardId,
    ShardPlan,
)
from arena_hero_bench.process_executor import (
    ProcessExecutorError,
    reference_engine_process_executor,
)
from arena_hero_sim import (
    REFERENCE_BACKEND_ID,
    REFERENCE_ENGINE_VERSION,
    REFERENCE_PROTOCOL_VERSION,
    REFERENCE_RULESET,
)
from arena_hero_sim.contracts import SimulationRequest, SimulatorConfig
from arena_hero_sim.reference_contracts import ReferenceScenario
from arena_hero_sim.reference_workload import (
    ReferenceWorkloadError,
    canonical_reference_scenario_registry,
    canonical_reference_workload_manifest,
)
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

SOAK_SCHEMA = "arena.bench.replay-soak.v1"
SOAK_KINDS = frozenset({"differential", "kpi_differential", "process_executor"})
CANONICAL_REFERENCE_WORKLOAD = "canonical-reference-workload"
_DEFAULT_ROUNDS = 2
_DEFAULT_MAX_DURATION_SECONDS = 120.0
_DEFAULT_HANDLE_TOLERANCE = 32
_DEFAULT_MAX_WORKERS = 1
_DEFAULT_SCENARIO_COUNT = 3
_DEFAULT_PER_TASK_TIMEOUT = 60.0
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

# --- Windows resource probes (stdlib ctypes; best effort, fail closed) ------
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_kernel32: Any = None
if os.name == "nt":  # pragma: no cover - exercised on Windows
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    _kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntry32W),
    ]
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntry32W),
    ]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class ReplaySoakError(ValueError):
    """Base error for the bounded offline replay soak harness."""


class SoakManifestError(ReplaySoakError):
    """A soak run manifest is invalid or unsupported."""


def _strict_int(value: object, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SoakManifestError(f"{field_name} must be an integer >= {minimum}")
    return value


def _strict_optional_int(value: object, field_name: str, *, default: int, minimum: int) -> int:
    if value is None:
        return default
    return _strict_int(value, field_name, minimum=minimum)


def _strict_optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SoakManifestError(f"{field_name} must be a number")
    return float(value)


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SoakManifestError(f"{field_name} must be a non-empty string")
    return value.strip()


def _strict_object(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SoakManifestError(f"{field_name} must be an object")
    return value


def _strict_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise SoakManifestError(f"{field_name} must be an array")
    return value


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    narrowed = to_json_value(value)
    if not isinstance(narrowed, dict):
        raise SoakManifestError("expected a JSON object")
    return narrowed


class SoakStatus(StrEnum):
    """Overall result of one bounded soak run."""

    PASS = "pass"
    FAIL = "fail"


class SoakIssueKind(StrEnum):
    """Fail-closed classification for every anomaly a soak can detect."""

    STEP_EXCEPTION = "step_exception"
    STEP_FAILED = "step_failed"
    DIGEST_DRIFT = "digest_drift"
    RESOURCE_LEAK = "resource_leak"
    PROCESS_RESIDUE = "process_residue"
    DURATION_EXCEEDED = "duration_exceeded"


@dataclass(frozen=True, slots=True)
class SoakStepSpec:
    """One declarative step inside a soak manifest."""

    step_id: str
    kind: str
    manifest: Path | None = None
    max_workers: int = _DEFAULT_MAX_WORKERS
    scenario_count: int = _DEFAULT_SCENARIO_COUNT
    per_task_timeout: float = _DEFAULT_PER_TASK_TIMEOUT

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _strict_str(self.step_id, "step_id"))
        if not _IDENTIFIER.fullmatch(self.step_id):
            raise SoakManifestError("step_id must be a lowercase portable identifier")
        if self.kind not in SOAK_KINDS:
            raise SoakManifestError(f"unsupported soak step kind: {self.kind!r}")
        if self.kind in ("differential", "kpi_differential") and self.manifest is None:
            raise SoakManifestError(f"step {self.step_id} requires a manifest")
        if self.kind == "process_executor" and self.manifest is not None:
            raise SoakManifestError(
                f"step {self.step_id} must not set manifest; "
                f"the canonical reference workload is used"
            )
        if self.max_workers < 1:
            raise SoakManifestError("max_workers must be positive")
        if self.scenario_count < 1:
            raise SoakManifestError("scenario_count must be positive")
        if self.per_task_timeout <= 0:
            raise SoakManifestError("per_task_timeout must be positive")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "id": self.step_id,
            "kind": self.kind,
            "manifest": None if self.manifest is None else str(self.manifest),
            "max_workers": self.max_workers,
            "scenario_count": self.scenario_count,
            "per_task_timeout": self.per_task_timeout,
        }


@dataclass(frozen=True, slots=True)
class SoakManifest:
    """A bounded soak run: rounds, duration cap, tolerance, and steps."""

    schema_version: str
    soak_id: str
    rounds: int
    max_duration_seconds: float | None
    handle_tolerance: int
    steps: tuple[SoakStepSpec, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema_version", _strict_str(self.schema_version, "schema_version")
        )
        if self.schema_version != SOAK_SCHEMA:
            raise SoakManifestError(
                f"unsupported soak manifest schemaVersion {self.schema_version!r}"
            )
        if not _IDENTIFIER.fullmatch(self.soak_id):
            raise SoakManifestError("soak_id must be a lowercase portable identifier")
        if self.rounds < 1:
            raise SoakManifestError("rounds must be positive")
        if self.max_duration_seconds is not None and self.max_duration_seconds <= 0:
            raise SoakManifestError("max_duration_seconds must be positive when set")
        if self.handle_tolerance < 0:
            raise SoakManifestError("handle_tolerance must be non-negative")
        if not self.steps:
            raise SoakManifestError("soak manifest must declare at least one step")
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise SoakManifestError("soak manifest step ids must be unique")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": self.schema_version,
            "soak_id": self.soak_id,
            "rounds": self.rounds,
            "max_duration_seconds": self.max_duration_seconds,
            "handle_tolerance": self.handle_tolerance,
            "steps": [step.to_json() for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class _StepResult:
    """Internal result of one step invocation (timing is added by the runner)."""

    content_sha256: str
    artifact_sha256: str | None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SoakIssue:
    """One classified anomaly with the round and step it belongs to."""

    kind: SoakIssueKind
    round_number: int | None
    step_id: str | None
    detail: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "round": self.round_number,
            "step_id": self.step_id,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SoakStepOutcome:
    """One step invocation in one round."""

    step_id: str
    kind: str
    content_sha256: str | None
    artifact_sha256: str | None
    elapsed_seconds: float
    metadata: Mapping[str, JsonValue]

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "step_id": self.step_id,
            "kind": self.kind,
            "content_sha256": self.content_sha256,
            "artifact_sha256": self.artifact_sha256,
            "elapsed_seconds": self.elapsed_seconds,
            "metadata": _json_object(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SoakRound:
    """One full replay round with at-rest resource snapshots."""

    round_number: int
    elapsed_seconds: float
    handle_count: int
    descendant_pids: tuple[int, ...]
    steps: tuple[SoakStepOutcome, ...]
    issues: tuple[SoakIssue, ...]

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "round_number": self.round_number,
            "elapsed_seconds": self.elapsed_seconds,
            "handle_count": self.handle_count,
            "descendant_pids": list(self.descendant_pids),
            "steps": [step.to_json() for step in self.steps],
            "issues": [issue.to_json() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class SoakReport:
    """Machine-readable, content-addressed report for one bounded soak run."""

    schema_version: str
    soak_id: str
    rounds_requested: int
    rounds_completed: int
    status: SoakStatus
    attested: bool
    injected_steps: bool
    counts: Mapping[str, int]
    rounds: tuple[SoakRound, ...]
    issues: tuple[SoakIssue, ...]
    metadata: Mapping[str, JsonValue]
    artifact_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "soak_id": self.soak_id,
            "rounds_requested": self.rounds_requested,
            "rounds_completed": self.rounds_completed,
            "status": self.status.value,
            "attested": self.attested,
            "injected_steps": self.injected_steps,
            "counts": dict(self.counts),
            "rounds": [round_.to_json() for round_ in self.rounds],
            "issues": [issue.to_json() for issue in self.issues],
            "metadata": _json_object(self.metadata),
            "artifact_sha256": self.artifact_sha256,
        }


# --- Resource probes --------------------------------------------------------


def _win_last_error() -> int:
    """Return the Windows last-error code for the kernel32 handle API.

    ``ctypes.get_last_error`` is Windows-only, so it is resolved through
    ``getattr`` to keep the module type-checkable on POSIX, where the member
    does not exist in the standard library. Only Windows callers (guarded by
    ``_kernel32 is not None``) reach this helper.
    """
    get_last_error = getattr(ctypes, "get_last_error")  # noqa: B009
    return int(get_last_error())


def _windows_handle_count() -> int:
    if _kernel32 is None:  # pragma: no cover - non-Windows
        raise ReplaySoakError("Windows handle probe unavailable")
    count = wintypes.DWORD()
    handle = _kernel32.GetCurrentProcess()
    if not _kernel32.GetProcessHandleCount(handle, ctypes.byref(count)):
        raise ReplaySoakError(f"GetProcessHandleCount failed: {_win_last_error()}")
    return int(count.value)


def _windows_process_parents() -> dict[int, int]:
    if _kernel32 is None:  # pragma: no cover - non-Windows
        raise ReplaySoakError("Windows process snapshot unavailable")
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        raise ReplaySoakError(f"CreateToolhelp32Snapshot failed: {_win_last_error()}")
    parents: dict[int, int] = {}
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise ReplaySoakError(f"Process32FirstW failed: {_win_last_error()}")
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        _kernel32.CloseHandle(snapshot)
    return parents


def _posix_process_parents() -> dict[int, int]:
    parents: dict[int, int] = {}
    try:
        entries = list(os.scandir("/proc"))
    except OSError as exc:
        raise ReplaySoakError(
            f"cannot snapshot /proc process table for residue monitoring: {exc}"
        ) from exc
    for entry in entries:
        if not entry.name.isdigit():
            continue
        stat_path = f"/proc/{entry.name}/stat"
        try:
            with open(stat_path, "rb") as stream:
                data = stream.read()
        except OSError:
            continue
        close = data.rfind(b")")
        if close < 0:
            continue
        fields = data[close + 1 :].split()
        if len(fields) < 2:
            continue
        try:
            parents[int(entry.name)] = int(fields[1])
        except ValueError:
            continue
    return parents


def _descendants_of(pid: int, parents: Mapping[int, int]) -> frozenset[int]:
    children: dict[int, list[int]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    found: set[int] = set()
    stack = list(children.get(pid, ()))
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(children.get(current, ()))
    return frozenset(found)


def _open_handle_count() -> int:
    """Return the number of open handles/fds of this process at rest."""
    if os.name == "nt":
        return _windows_handle_count()
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError as exc:
        raise ReplaySoakError(
            f"cannot count open file descriptors (requires /proc): {exc}"
        ) from exc


def _descendant_pids() -> frozenset[int]:
    """Return the current descendant process ids of this process."""
    parents = _windows_process_parents() if os.name == "nt" else _posix_process_parents()
    return _descendants_of(os.getpid(), parents)


# --- Step builders ----------------------------------------------------------


def _differential_step(spec: SoakStepSpec, base: Path) -> Callable[[], _StepResult]:
    manifest = spec.manifest
    if manifest is None:
        raise SoakManifestError(f"step {spec.step_id} requires a manifest")
    manifest_path = base / manifest

    def run() -> _StepResult:
        report = run_differential_from_manifest(manifest_path)
        counts = {status.value: report.counts.get(status, 0) for status in DifferentialStatus}
        return _StepResult(
            content_sha256=report.artifact_sha256,
            artifact_sha256=report.artifact_sha256,
            metadata=_json_object(
                {
                    "schema_version": report.schema_version,
                    "dataset_id": report.dataset_id,
                    "tenant_id": report.tenant_id,
                    "unclassified_count": report.unclassified_count,
                    "counts": counts,
                }
            ),
        )

    return run


def _kpi_differential_step(spec: SoakStepSpec, base: Path) -> Callable[[], _StepResult]:
    manifest = spec.manifest
    if manifest is None:
        raise SoakManifestError(f"step {spec.step_id} requires a manifest")
    manifest_path = base / manifest

    def run() -> _StepResult:
        report = run_kpi_differential_from_manifest(manifest_path)
        counts = {status.value: report.counts.get(status, 0) for status in DifferentialStatus}
        return _StepResult(
            content_sha256=report.artifact_sha256,
            artifact_sha256=report.artifact_sha256,
            metadata=_json_object(
                {
                    "schema_version": report.schema_version,
                    "dataset_id": report.dataset_id,
                    "tenant_id": report.tenant_id,
                    "evidence_kind": report.evidence_kind,
                    "unclassified_count": report.unclassified_count,
                    "counts": counts,
                }
            ),
        )

    return run


def _process_executor_step(spec: SoakStepSpec, base: Path) -> Callable[[], _StepResult]:
    del base  # the canonical reference workload is programmatic, not a file path
    try:
        registry = canonical_reference_scenario_registry()
        workload = canonical_reference_workload_manifest()
    except ReferenceWorkloadError as exc:
        raise ReplaySoakError(f"cannot load the canonical reference workload: {exc}") from exc
    scenarios: list[ReferenceScenario] = []
    requests: list[SimulationRequest] = []
    for case in workload.cases[: spec.scenario_count]:
        record = registry.resolve(case)
        scenario = record.scenario
        scenarios.append(scenario)
        requests.append(
            SimulationRequest(
                request_id=f"soak-{case.case_id}",
                episode_id=f"episode-{case.case_id}",
                config=SimulatorConfig(
                    backend_id=REFERENCE_BACKEND_ID,
                    engine_version=REFERENCE_ENGINE_VERSION,
                    ruleset=REFERENCE_RULESET,
                    seed=case.seed,
                    max_ticks=case.max_ticks,
                    protocol_version=REFERENCE_PROTOCOL_VERSION,
                    requested_features=case.requested_features,
                ),
                initial_state_sha256=case.initial_state_sha256,
                contestant_ids=case.contestant_ids,
                input_artifact_sha256=case.scenario_sha256,
                labels={"source": CANONICAL_REFERENCE_WORKLOAD},
            )
        )
    plan = ShardPlan.create(
        operation_id=f"soak-{spec.step_id}",
        experiment_id=ExperimentId(f"experiment-{spec.step_id}"),
        run_id=RunId(f"run-{spec.step_id}"),
        shard_id=ShardId(f"shard-{spec.step_id}"),
        requests=requests,
    )

    def run() -> _StepResult:
        store = InMemoryArtifactStore()
        ledger = InMemoryExecutionLedger()
        executor = reference_engine_process_executor(
            scenarios,
            store,
            ledger,
            max_workers=spec.max_workers,
            per_task_timeout=spec.per_task_timeout,
        )
        try:
            try:
                result = executor.execute(plan)
            except ProcessExecutorError as exc:
                raise ReplaySoakError(f"process executor step failed: {exc}") from exc
        finally:
            executor.close()
        if result.status is not RunStatus.COMPLETE or not result.publishable:
            raise ReplaySoakError(
                "process executor shard did not complete cleanly: "
                f"status={result.status.value} publishable={result.publishable} "
                f"errors={list(result.errors)}"
            )
        return _StepResult(
            content_sha256=result.content_sha256,
            artifact_sha256=result.artifact_ref,
            metadata={
                "workload_id": workload.workload_id,
                "workload_version": workload.workload_version,
                "workload_sha256": workload.sha256,
                "plan_sha256": plan.plan_sha256,
                "request_count": len(requests),
                "status": result.status.value,
                "publishable": result.publishable,
            },
        )

    return run


def _default_step(spec: SoakStepSpec, base: Path) -> Callable[[], _StepResult]:
    if spec.kind == "differential":
        return _differential_step(spec, base)
    if spec.kind == "kpi_differential":
        return _kpi_differential_step(spec, base)
    if spec.kind == "process_executor":
        return _process_executor_step(spec, base)
    raise SoakManifestError(f"unsupported soak step kind: {spec.kind}")


SoakStepFactory = Callable[[SoakStepSpec, Path], Callable[[], _StepResult] | None]


def load_soak_manifest(manifest_path: str | Path) -> SoakManifest:
    """Parse and validate one bounded soak run manifest (paths stay relative)."""
    path = Path(manifest_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SoakManifestError(f"cannot read soak manifest {path.name}: {exc}") from exc
    manifest = _strict_object(raw, "soak manifest")
    schema = _strict_str(manifest.get("schemaVersion"), "schemaVersion")
    if schema != SOAK_SCHEMA:
        raise SoakManifestError(f"unsupported soak manifest schemaVersion {schema!r}")
    soak_id = _strict_str(manifest.get("soak_id"), "soak_id")
    rounds = _strict_int(manifest.get("rounds", _DEFAULT_ROUNDS), "rounds", minimum=1)
    max_duration = _strict_optional_float(
        manifest.get("max_duration_seconds"), "max_duration_seconds"
    )
    handle_tolerance = _strict_int(
        manifest.get("handle_tolerance", _DEFAULT_HANDLE_TOLERANCE),
        "handle_tolerance",
        minimum=0,
    )
    steps_raw = _strict_list(manifest.get("steps"), "steps")
    if not steps_raw:
        raise SoakManifestError("soak manifest must declare at least one step")
    steps: list[SoakStepSpec] = []
    for index, item in enumerate(steps_raw):
        step = _strict_object(item, f"steps[{index}]")
        step_id = _strict_str(step.get("id"), f"steps[{index}].id")
        kind = _strict_str(step.get("kind"), f"steps[{index}].kind")
        raw_manifest = step.get("manifest")
        step_manifest = (
            None
            if raw_manifest is None
            else Path(_strict_str(raw_manifest, f"steps[{index}].manifest"))
        )
        max_workers = _strict_optional_int(
            step.get("max_workers"),
            f"steps[{index}].max_workers",
            default=_DEFAULT_MAX_WORKERS,
            minimum=1,
        )
        scenario_count = _strict_optional_int(
            step.get("scenario_count"),
            f"steps[{index}].scenario_count",
            default=_DEFAULT_SCENARIO_COUNT,
            minimum=1,
        )
        per_task_timeout = _strict_optional_float(
            step.get("per_task_timeout"), f"steps[{index}].per_task_timeout"
        )
        if per_task_timeout is None:
            per_task_timeout = _DEFAULT_PER_TASK_TIMEOUT
        if per_task_timeout <= 0:
            raise SoakManifestError(f"steps[{index}].per_task_timeout must be positive")
        steps.append(
            SoakStepSpec(
                step_id=step_id,
                kind=kind,
                manifest=step_manifest,
                max_workers=max_workers,
                scenario_count=scenario_count,
                per_task_timeout=per_task_timeout,
            )
        )
    return SoakManifest(
        schema_version=SOAK_SCHEMA,
        soak_id=soak_id,
        rounds=rounds,
        max_duration_seconds=max_duration,
        handle_tolerance=handle_tolerance,
        steps=tuple(steps),
    )


def _issue(
    kind: SoakIssueKind, round_number: int | None, step_id: str | None, detail: str
) -> SoakIssue:
    return SoakIssue(kind=kind, round_number=round_number, step_id=step_id, detail=detail)


def run_soak(
    manifest_path: str | Path,
    *,
    step_factory: SoakStepFactory | None = None,
) -> SoakReport:
    """Run a bounded soak and return a machine-readable, fail-closed report.

    ``step_factory`` is a private reverse-validation seam. When it returns a
    step for a manifest step, that injected step is used instead of the real
    one and the report is marked ``injected_steps=true`` / ``attested=false``.
    """
    path = Path(manifest_path)
    manifest = load_soak_manifest(path)
    base = path.parent
    steps: list[tuple[SoakStepSpec, Callable[[], _StepResult]]] = []
    injected_steps = False
    for spec in manifest.steps:
        injected = None if step_factory is None else step_factory(spec, base)
        if injected is not None:
            step = injected
            injected_steps = True
        else:
            try:
                step = _default_step(spec, base)
            except ReplaySoakError as exc:
                raise SoakManifestError(f"cannot build soak step {spec.step_id}: {exc}") from exc
        steps.append((spec, step))

    # Fail closed on unsupported platforms before doing any work.
    _open_handle_count()
    _descendant_pids()

    issues: list[SoakIssue] = []
    rounds: list[SoakRound] = []
    reference_digests: dict[str, str] = {}
    started = time.monotonic()

    for round_number in range(1, manifest.rounds + 1):
        elapsed = time.monotonic() - started
        if manifest.max_duration_seconds is not None and elapsed >= manifest.max_duration_seconds:
            issues.append(
                _issue(
                    SoakIssueKind.DURATION_EXCEEDED,
                    round_number,
                    None,
                    f"soak exceeded max_duration_seconds="
                    f"{manifest.max_duration_seconds:g} before round {round_number} "
                    f"(elapsed {elapsed:g}s)",
                )
            )
            break

        round_started = time.monotonic()
        baseline_handles = _open_handle_count()
        baseline_pids = _descendant_pids()
        round_issues: list[SoakIssue] = []
        outcomes: list[SoakStepOutcome] = []
        for spec, step in steps:
            step_started = time.monotonic()
            try:
                result = step()
                outcome = SoakStepOutcome(
                    step_id=spec.step_id,
                    kind=spec.kind,
                    content_sha256=result.content_sha256,
                    artifact_sha256=result.artifact_sha256,
                    elapsed_seconds=time.monotonic() - step_started,
                    metadata=result.metadata,
                )
            except Exception as exc:  # fail closed with classification
                outcome = SoakStepOutcome(
                    step_id=spec.step_id,
                    kind=spec.kind,
                    content_sha256=None,
                    artifact_sha256=None,
                    elapsed_seconds=time.monotonic() - step_started,
                    metadata={"error": f"{type(exc).__name__}: {exc}"},
                )
                round_issues.append(
                    _issue(
                        SoakIssueKind.STEP_EXCEPTION,
                        round_number,
                        spec.step_id,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
            else:
                previous = reference_digests.get(spec.step_id)
                if previous is None:
                    reference_digests[spec.step_id] = result.content_sha256
                elif result.content_sha256 != previous:
                    round_issues.append(
                        _issue(
                            SoakIssueKind.DIGEST_DRIFT,
                            round_number,
                            spec.step_id,
                            f"step digest changed from {previous} to {result.content_sha256}",
                        )
                    )
            outcomes.append(outcome)

        after_handles = _open_handle_count()
        after_pids = _descendant_pids()
        if after_handles > baseline_handles + manifest.handle_tolerance:
            round_issues.append(
                _issue(
                    SoakIssueKind.RESOURCE_LEAK,
                    round_number,
                    None,
                    f"open handle/fd count grew from {baseline_handles} to {after_handles} "
                    f"(tolerance {manifest.handle_tolerance})",
                )
            )
        residual = sorted(after_pids - baseline_pids)
        if residual:
            round_issues.append(
                _issue(
                    SoakIssueKind.PROCESS_RESIDUE,
                    round_number,
                    None,
                    "descendant processes remain after the round: "
                    + ", ".join(str(pid) for pid in residual),
                )
            )
        rounds.append(
            SoakRound(
                round_number=round_number,
                elapsed_seconds=time.monotonic() - round_started,
                handle_count=after_handles,
                descendant_pids=tuple(sorted(after_pids)),
                steps=tuple(outcomes),
                issues=tuple(round_issues),
            )
        )
        issues.extend(round_issues)

    status = SoakStatus.FAIL if issues else SoakStatus.PASS
    counts = {kind.value: count for kind, count in _counts_by_kind(issues).items()}
    payload = {
        "schema_version": SOAK_SCHEMA,
        "soak_id": manifest.soak_id,
        "rounds_requested": manifest.rounds,
        "rounds_completed": len(rounds),
        "status": status.value,
        "attested": status is SoakStatus.PASS and not injected_steps,
        "injected_steps": injected_steps,
        "counts": counts,
        "rounds": [round_.to_json() for round_ in rounds],
        "issues": [issue.to_json() for issue in issues],
        "metadata": {
            "soak_id": manifest.soak_id,
            "rounds_requested": manifest.rounds,
            "max_duration_seconds": manifest.max_duration_seconds,
            "handle_tolerance": manifest.handle_tolerance,
            "steps": [{"id": step.step_id, "kind": step.kind} for step in manifest.steps],
        },
    }
    artifact_sha256 = content_sha256(payload)
    return SoakReport(
        schema_version=SOAK_SCHEMA,
        soak_id=manifest.soak_id,
        rounds_requested=manifest.rounds,
        rounds_completed=len(rounds),
        status=status,
        attested=status is SoakStatus.PASS and not injected_steps,
        injected_steps=injected_steps,
        counts=counts,
        rounds=tuple(rounds),
        issues=tuple(issues),
        metadata=_json_object(payload["metadata"]),
        artifact_sha256=artifact_sha256,
    )


def _counts_by_kind(issues: Sequence[SoakIssue]) -> Mapping[SoakIssueKind, int]:
    counter = Counter(issue.kind for issue in issues)
    return {kind: counter.get(kind, 0) for kind in SoakIssueKind}


__all__ = [
    "CANONICAL_REFERENCE_WORKLOAD",
    "SOAK_KINDS",
    "SOAK_SCHEMA",
    "ReplaySoakError",
    "SoakIssue",
    "SoakIssueKind",
    "SoakManifest",
    "SoakManifestError",
    "SoakReport",
    "SoakRound",
    "SoakStatus",
    "SoakStepOutcome",
    "SoakStepSpec",
    "load_soak_manifest",
    "run_soak",
]
