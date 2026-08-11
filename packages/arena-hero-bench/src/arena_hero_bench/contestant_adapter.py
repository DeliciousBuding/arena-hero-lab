"""Contestant entry point adapter for the bounded process executor (P3-6).

Turns a :class:`ContestantManifest` into the executable spec that
:class:`ProcessExecutor` can run -- a :class:`BackendProcessSpec` plus a single
request envelope carrying the entry point command, environment allowlist, and
timeout -- and normalizes the isolated execution outcome into one unified
:class:`ContestantRunResult` (stdout/stderr/exit code/timeout/crash).

Design notes
------------
- No new execution engine: the adapter reuses ``ProcessExecutor`` for spawn,
  per-task timeout, process-tree reaping, output caps, and exit-code handling.
  A contestant worker (supplied by the caller as ``worker_script``, or derived
  from the manifest entry point) speaks the ``arena.process.work.v1`` /
  ``arena.process.result.v1`` envelope protocol and runs the actual contestant
  entry point (for Python contestants the SDK entry point runner).
- The manifest is validated conservatively: only Python contestants that
  require subprocess isolation are accepted, and the entry point must be one of
  the two forms ``ProcessExecutor`` can spawn (``python -m <module> [args...]``
  or a ``.py`` script path). Unknown or unsupported values fail closed with
  :class:`ContestantAdapterError`.
- The sim envelope intentionally does not carry contestant stdout or a
  transcript digest; the normalized result keeps those fields for callers that
  execute through a non-envelope seam, and the envelope path documents them as
  empty. Timeout, crash, and exit-code classification is deterministic and
  table-tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from arena_hero_bench.contestant import ContestantManifest
from arena_hero_bench.orchestration import (
    ArtifactStore,
    ExecutionLedger,
    ExperimentId,
    RunId,
    RunStatus,
    ShardId,
    ShardPlan,
    ShardResult,
)
from arena_hero_bench.process_executor import BackendProcessSpec, ProcessExecutor
from arena_hero_sim.contracts import RulesetRef, SimulationRequest, SimulatorConfig
from arena_hero_sim.serialization import content_sha256

_DEFAULT_MAX_OUTPUT_BYTES = 1_048_576
_DEFAULT_REQUEST_ID = "contestant-1"
_DEFAULT_EPISODE_ID = "episode-1"
_DEFAULT_OPERATION_ID = "contestant-op-1"
_DEFAULT_EXPERIMENT_ID = "experiment-1"
_DEFAULT_RUN_ID = "run-1"
_DEFAULT_SHARD_ID = "shard-a"

_MODULE_FORM_PREFIX = "python -m "
_MODULE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

_CONTESTANT_RULESET = RulesetRef(
    name="arena.contestant.adapter",
    version="1",
    rules_sha256=content_sha256(b"arena.contestant.adapter.ruleset"),
)
"""Placeholder ruleset identity for the sim-shaped request envelope.

The contestant adapter does not simulate the game; the request envelope still
requires a ruleset identity, so a fixed neutral placeholder is used until real
integration binds the actual game ruleset.
"""

_CONTESTANT_INITIAL_STATE = content_sha256(b"arena.contestant.adapter.initial")
"""Placeholder initial-state digest for the sim-shaped request envelope."""

_WORKER_TIMEOUT_MARKERS = (
    "exceeded per-task timeout",
    "did not read the work envelope within",
)
_WORKER_CRASH_MARKERS = ("did not terminate within the bounded reap window",)
_WORKER_EXIT_RE = re.compile(r"worker exited with code (-?\d+)")


class ContestantAdapterError(ValueError):
    """Fail-closed error for manifest parsing and execution normalization."""


class ContestantRunStatus(StrEnum):
    """Fail-closed outcome classification; only ``OK`` is success."""

    OK = "ok"
    TIMEOUT = "timeout"
    CRASH = "crash"
    PROTOCOL = "protocol"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ContestantRawOutcome:
    """Raw execution outcome before normalization.

    Exactly one classification source should be set: ``round_status`` for
    worker-reported SDK-runner-style outcomes, or the process-level flags
    (``timed_out``/``crashed``/``worker_error``) for executor-isolated
    failures. ``stdout``/``stderr`` carry bounded captured output when the
    caller has access to it.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    crashed: bool = False
    worker_error: str | None = None
    round_status: str | None = None
    round_error: str | None = None


@dataclass(frozen=True, slots=True)
class ContestantRunResult:
    """Unified normalized contestant execution result."""

    status: ContestantRunStatus
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None
    artifact_ref: str | None = None

    @property
    def ok(self) -> bool:
        """True only for a completed contestant round."""
        return self.status is ContestantRunStatus.OK


def _parse_entry_point(entry_point: str) -> tuple[str, str]:
    """Parse an entry point into the worker form ``ProcessExecutor`` can spawn.

    Accepted forms (exactly): ``python -m <module> [args...]`` and
    ``<path>.py [args...]``. Everything else fails closed; the first token of a
    module form must be a portable dotted identifier.
    """
    text = entry_point.strip()
    if not text:
        raise ContestantAdapterError("entry_point must not be empty")
    if text.startswith(_MODULE_FORM_PREFIX):
        rest = text[len(_MODULE_FORM_PREFIX) :].strip()
        module = rest.split(maxsplit=1)[0] if rest else ""
        if not module or not _MODULE_NAME_RE.fullmatch(module):
            raise ContestantAdapterError(
                f"entry_point module form must name a portable module: {entry_point!r}"
            )
        return "module", module
    tokens = text.split()
    if tokens and tokens[0].endswith(".py"):
        return "script", tokens[0]
    raise ContestantAdapterError(
        "entry_point must be 'python -m <module> [args...]' or a '.py' script "
        f"path: {entry_point!r}"
    )


def build_spec(
    manifest: ContestantManifest,
    *,
    worker_script: str | None = None,
) -> BackendProcessSpec:
    """Build the process executor spec (backend allowlist entry) for a contestant.

    Conservative validation: only Python contestants that require subprocess
    isolation are accepted, and the entry point must parse into a spawnable
    form. When ``worker_script`` is supplied it wins as the envelope worker;
    otherwise the worker form is derived from the manifest entry point.
    """
    if manifest.language != "python":
        raise ContestantAdapterError(
            f"unsupported contestant language: {manifest.language!r}; only "
            "'python' is wired through the entry point adapter"
        )
    if not manifest.isolation.subprocess_required:
        raise ContestantAdapterError("contestant isolation must require subprocess execution")
    _parse_entry_point(manifest.entry_point)
    try:
        if worker_script is not None:
            return BackendProcessSpec(
                backend_id=manifest.contestant_id,
                engine_version=manifest.version,
                protocol_versions=frozenset({manifest.protocol_version}),
                supported_features=manifest.capabilities,
                worker_script=worker_script,
            )
        kind, value = _parse_entry_point(manifest.entry_point)
        if kind == "module":
            return BackendProcessSpec(
                backend_id=manifest.contestant_id,
                engine_version=manifest.version,
                protocol_versions=frozenset({manifest.protocol_version}),
                supported_features=manifest.capabilities,
                worker_module=value,
            )
        return BackendProcessSpec(
            backend_id=manifest.contestant_id,
            engine_version=manifest.version,
            protocol_versions=frozenset({manifest.protocol_version}),
            supported_features=manifest.capabilities,
            worker_script=value,
        )
    except ValueError as exc:
        raise ContestantAdapterError(
            f"contestant cannot be executed by process_executor: {exc}"
        ) from exc


def build_request(
    manifest: ContestantManifest,
    *,
    request_id: str = _DEFAULT_REQUEST_ID,
    episode_id: str = _DEFAULT_EPISODE_ID,
    ruleset: RulesetRef | None = None,
    timeout_seconds: float | None = None,
) -> SimulationRequest:
    """Build the single request that carries the contestant execution spec.

    The entry point command, environment allowlist, and timeout travel in
    ``config.parameters`` (a string map) so the envelope worker can
    reconstruct the contestant invocation without ambient state.
    """
    timeout = manifest.resources.timeout_seconds if timeout_seconds is None else timeout_seconds
    if timeout <= 0:
        raise ContestantAdapterError("contestant timeout must be positive")
    parameters = {
        "entry_point": manifest.entry_point,
        "environment_allowlist": ",".join(manifest.isolation.environment_allowlist),
        "timeout_seconds": str(timeout),
    }
    return SimulationRequest(
        request_id=request_id,
        episode_id=episode_id,
        config=SimulatorConfig(
            backend_id=manifest.contestant_id,
            engine_version=manifest.version,
            ruleset=_CONTESTANT_RULESET if ruleset is None else ruleset,
            seed=0,
            max_ticks=1,
            protocol_version=manifest.protocol_version,
            requested_features=manifest.capabilities,
            parameters=parameters,
        ),
        initial_state_sha256=_CONTESTANT_INITIAL_STATE,
        contestant_ids=(manifest.contestant_id,),
    )


def build_plan(
    request: SimulationRequest,
    *,
    operation_id: str = _DEFAULT_OPERATION_ID,
    experiment_id: str = _DEFAULT_EXPERIMENT_ID,
    run_id: str = _DEFAULT_RUN_ID,
    shard_id: str = _DEFAULT_SHARD_ID,
) -> ShardPlan:
    """Build the single-request shard plan for one contestant round."""
    return ShardPlan.create(
        operation_id=operation_id,
        experiment_id=ExperimentId(experiment_id),
        run_id=RunId(run_id),
        shard_id=ShardId(shard_id),
        requests=(request,),
    )


def _classify_worker_error(
    message: str,
) -> tuple[ContestantRunStatus, str, int | None]:
    """Classify a process_executor worker failure message."""
    if any(marker in message for marker in _WORKER_TIMEOUT_MARKERS):
        return ContestantRunStatus.TIMEOUT, message, None
    match = _WORKER_EXIT_RE.search(message)
    if match is not None:
        return ContestantRunStatus.CRASH, message, int(match.group(1))
    if any(marker in message for marker in _WORKER_CRASH_MARKERS):
        return ContestantRunStatus.CRASH, message, None
    return ContestantRunStatus.ERROR, message, None


def _shard_result_to_outcome(shard_result: ShardResult) -> ContestantRawOutcome:
    """Translate one shard result into a raw outcome for normalization."""
    if shard_result.status is RunStatus.COMPLETE:
        return ContestantRawOutcome()
    error = shard_result.errors[0] if shard_result.errors else "shard failed without diagnostics"
    extra = "\n".join(shard_result.errors[1:])
    if error.startswith("round_status="):
        _, _, status_and_detail = error.partition("=")
        status, _, detail = status_and_detail.partition(":")
        return ContestantRawOutcome(
            round_status=status.strip(),
            round_error=detail.strip() or None,
            stderr=extra,
        )
    status, message, exit_code = _classify_worker_error(error)
    if status is ContestantRunStatus.TIMEOUT:
        return ContestantRawOutcome(timed_out=True, worker_error=message, stderr=extra)
    if status is ContestantRunStatus.CRASH:
        _, _, tail = error.partition(": ")
        return ContestantRawOutcome(
            crashed=True,
            exit_code=exit_code,
            worker_error=message,
            stderr=tail if tail else extra,
        )
    return ContestantRawOutcome(worker_error=message, stderr=extra)


def normalize_result(outcome: ContestantRawOutcome) -> ContestantRunResult:
    """Normalize a raw outcome into the unified fail-closed result object.

    Classification precedence: executor-isolated timeout/crash, then the
    worker-reported round status, then other worker failures, then a bare
    non-zero exit code, then success.
    """
    if outcome.timed_out:
        return ContestantRunResult(
            status=ContestantRunStatus.TIMEOUT,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            error=outcome.worker_error or outcome.round_error or "contestant timed out",
        )
    if outcome.crashed:
        return ContestantRunResult(
            status=ContestantRunStatus.CRASH,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            exit_code=outcome.exit_code,
            error=outcome.worker_error or outcome.round_error or "contestant crashed",
        )
    if outcome.round_status is not None:
        try:
            status = ContestantRunStatus(outcome.round_status)
        except ValueError:
            return ContestantRunResult(
                status=ContestantRunStatus.ERROR,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                exit_code=outcome.exit_code,
                error=f"unknown round status {outcome.round_status!r}",
            )
        return ContestantRunResult(
            status=status,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            exit_code=0 if status is ContestantRunStatus.OK else outcome.exit_code,
            error=None if status is ContestantRunStatus.OK else outcome.round_error,
        )
    if outcome.worker_error is not None:
        status, message, exit_code = _classify_worker_error(outcome.worker_error)
        return ContestantRunResult(
            status=status,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            exit_code=exit_code,
            error=message,
        )
    if outcome.exit_code not in (None, 0):
        return ContestantRunResult(
            status=ContestantRunStatus.CRASH,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            exit_code=outcome.exit_code,
            error=f"contestant exited with code {outcome.exit_code}",
        )
    return ContestantRunResult(
        status=ContestantRunStatus.OK,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        exit_code=0,
    )


def execute_contestant(
    manifest: ContestantManifest,
    executor: ProcessExecutor,
    *,
    request: SimulationRequest | None = None,
    operation_id: str = _DEFAULT_OPERATION_ID,
    experiment_id: str = _DEFAULT_EXPERIMENT_ID,
    run_id: str = _DEFAULT_RUN_ID,
    shard_id: str = _DEFAULT_SHARD_ID,
    ruleset: RulesetRef | None = None,
) -> ContestantRunResult:
    """Run one contestant through the process executor and normalize the outcome."""
    plan_request = request if request is not None else build_request(manifest, ruleset=ruleset)
    plan = build_plan(
        plan_request,
        operation_id=operation_id,
        experiment_id=experiment_id,
        run_id=run_id,
        shard_id=shard_id,
    )
    shard_result = executor.execute(plan)
    outcome = _shard_result_to_outcome(shard_result)
    result = normalize_result(outcome)
    return ContestantRunResult(
        status=result.status,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        error=result.error,
        artifact_ref=shard_result.artifact_ref,
    )


def run_contestant(
    manifest: ContestantManifest,
    *,
    worker_script: str | None,
    artifact_store: ArtifactStore,
    ledger: ExecutionLedger,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    operation_id: str = _DEFAULT_OPERATION_ID,
    experiment_id: str = _DEFAULT_EXPERIMENT_ID,
    run_id: str = _DEFAULT_RUN_ID,
    shard_id: str = _DEFAULT_SHARD_ID,
) -> ContestantRunResult:
    """Convenience full chain: build the executor from the manifest and run.

    The executor is constructed with ``per_task_timeout`` from the manifest
    resource requirements and exactly one backend spec derived from the
    manifest, so the caller only supplies the envelope worker and stores.
    """
    spec = build_spec(manifest, worker_script=worker_script)
    with ProcessExecutor(
        backend_specs={spec.backend_id: spec},
        artifact_store=artifact_store,
        ledger=ledger,
        max_workers=1,
        per_task_timeout=manifest.resources.timeout_seconds,
        max_output_bytes=max_output_bytes,
    ) as executor:
        return execute_contestant(
            manifest,
            executor,
            operation_id=operation_id,
            experiment_id=experiment_id,
            run_id=run_id,
            shard_id=shard_id,
        )


__all__ = [
    "ContestantAdapterError",
    "ContestantRawOutcome",
    "ContestantRunResult",
    "ContestantRunStatus",
    "build_plan",
    "build_request",
    "build_spec",
    "execute_contestant",
    "normalize_result",
    "run_contestant",
]
