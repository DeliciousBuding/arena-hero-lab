"""Contestant envelope worker for the bench entry point adapter smoke (P3-6).

Spawned by ``ProcessExecutor`` as the ``worker_script`` for one contestant
round. It reads a single ``arena.process.work.v1`` envelope line from stdin,
runs the contestant entry point (carried in the request ``parameters``)
in-process with bounded stdout/stderr capture, and writes one
``arena.process.result.v1`` envelope line to stdout. The worker mirrors the
CLI contract of the SDK entry point runner (``status=ok`` / ``status=timeout``
/ ``status=crash`` / ``status=protocol`` / ``status=error``), so the bench
adapter full chain runs without the SDK installed in the bench environment.

Hard failures (invalid envelope, missing parameters, unparseable entry point)
are reported on stderr with a non-zero exit so the executor fails closed.
"""

from __future__ import annotations

import io
import json
import re
import runpy
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass

from arena_hero_bench.process_executor import (
    RESULT_ENVELOPE_VERSION,
    WORK_ENVELOPE_VERSION,
    ProcessExecutorError,
    request_from_json,
    result_to_json,
)
from arena_hero_sim.contracts import SimulationRequest, SimulationResult, SimulationStatus
from arena_hero_sim.serialization import canonical_json_bytes, content_sha256

_OUTPUT_CAP = 65_536
_STATUS_RE = re.compile(r"^status=(\w+)(?:\s+error=(.*))?$", re.MULTILINE)


class _BoundedBuffer(io.TextIOBase):
    """Text stream that keeps only the first ``cap`` characters."""

    def __init__(self, cap: int) -> None:
        super().__init__()
        self._cap = cap
        self._parts: list[str] = []
        self._size = 0
        self.overflow = False

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._size += len(text)
        if self._size <= self._cap:
            self._parts.append(text)
        else:
            self.overflow = True
        return len(text)

    def value(self) -> str:
        return "".join(self._parts)


@dataclass(frozen=True, slots=True)
class _EntryPointRun:
    exit_code: int
    crash_detail: str | None
    stdout: str
    stderr: str
    stdout_overflow: bool
    stderr_overflow: bool


def _run_entry_point(
    script: str, args: Sequence[str], *, stdout_cap: int, stderr_cap: int
) -> _EntryPointRun:
    """Run a ``.py`` entry point in-process with bounded captured output."""
    old_argv = sys.argv
    stdout_buffer = _BoundedBuffer(stdout_cap)
    stderr_buffer = _BoundedBuffer(stderr_cap)
    sys.argv = [script, *args]
    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            exit_code = 0
            crash_detail = None
            try:
                runpy.run_path(script, run_name="__main__")
            except SystemExit as exc:
                code = exc.code
                if code is None:
                    exit_code = 0
                elif isinstance(code, int):
                    exit_code = code
                else:
                    exit_code = 1
                    crash_detail = str(code)
            except Exception as exc:  # fail-closed worker classification
                exit_code = 1
                crash_detail = f"{type(exc).__name__}: {exc}"
    finally:
        sys.argv = old_argv
    return _EntryPointRun(
        exit_code=exit_code,
        crash_detail=crash_detail,
        stdout=stdout_buffer.value(),
        stderr=stderr_buffer.value(),
        stdout_overflow=stdout_buffer.overflow,
        stderr_overflow=stderr_buffer.overflow,
    )


def _classify_round(
    run: _EntryPointRun,
) -> tuple[str | None, str | None]:
    """Map runner stdout/stderr/exit code to a round status and detail."""
    match = _STATUS_RE.search(run.stderr)
    if match is None:
        if run.exit_code != 0:
            return "crash", f"runner exited with code {run.exit_code}"
        return None, None
    status = match.group(1)
    detail = (match.group(2) or "").strip() or None
    if status == "ok" and run.exit_code != 0:
        return "crash", detail or f"runner exited with code {run.exit_code}"
    return status, detail


def _result_for(request: SimulationRequest, run: _EntryPointRun) -> SimulationResult:
    """Build the per-request result envelope entry for one contestant round."""
    status, detail = _classify_round(run)
    request_id = request.request_id
    episode_id = request.episode_id
    backend_id = request.config.backend_id
    engine_version = request.config.engine_version
    rules_sha256 = request.config.ruleset.rules_sha256
    seed = request.config.seed
    if status is None or (status == "ok" and run.exit_code == 0):
        digest = content_sha256(run.stdout.encode("utf-8"))
        return SimulationResult(
            request_id=request_id,
            episode_id=episode_id,
            backend_id=backend_id,
            engine_version=engine_version,
            rules_sha256=rules_sha256,
            seed=seed,
            status=SimulationStatus.COMPLETE,
            publishable=True,
            ticks_completed=1,
            final_world_sha256=digest,
            metrics={},
            artifact_refs=(digest,),
            errors=(),
        )
    error = f"round_status={status}: {detail or 'no diagnostics'}"
    if run.crash_detail is not None:
        error += f" ({run.crash_detail})"
    if run.stdout_overflow or run.stderr_overflow:
        error += " (output truncated)"
    return SimulationResult(
        request_id=request_id,
        episode_id=episode_id,
        backend_id=backend_id,
        engine_version=engine_version,
        rules_sha256=rules_sha256,
        seed=seed,
        status=SimulationStatus.FAILED,
        publishable=False,
        ticks_completed=0,
        errors=(error,),
    )


def _run_one(request: SimulationRequest) -> SimulationResult:
    parameters = request.config.parameters
    entry_point = parameters.get("entry_point")
    if not entry_point:
        raise ProcessExecutorError("work request is missing the entry_point parameter")
    tokens = entry_point.strip().split()
    if not tokens or not tokens[0].endswith(".py"):
        raise ProcessExecutorError("entry point worker only runs '.py' entry point scripts")
    run = _run_entry_point(
        tokens[0],
        tokens[1:],
        stdout_cap=_OUTPUT_CAP,
        stderr_cap=_OUTPUT_CAP,
    )
    return _result_for(request, run)


def main() -> int:
    line = sys.stdin.buffer.readline()
    if not line:
        print("entry_point_worker: no work envelope on stdin", file=sys.stderr)
        return 2
    try:
        payload = json.loads(line.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ProcessExecutorError("work envelope must be an object")
        if payload.get("schema_version") != WORK_ENVELOPE_VERSION:
            raise ProcessExecutorError("unsupported work envelope schema")
        requests_payload = payload.get("requests")
        if not isinstance(requests_payload, Sequence) or isinstance(requests_payload, (str, bytes)):
            raise ProcessExecutorError("work requests must be an array")
        if not requests_payload:
            raise ProcessExecutorError("work requests must not be empty")
        requests: list[SimulationRequest] = []
        for item in requests_payload:
            if not isinstance(item, Mapping):
                raise ProcessExecutorError("work request entry must be an object")
            requests.append(request_from_json(item))
        results = [_run_one(request) for request in requests]
        output = {
            "schema_version": RESULT_ENVELOPE_VERSION,
            "operation_id": payload.get("operation_id"),
            "shard_id": payload.get("shard_id"),
            "plan_sha256": payload.get("plan_sha256"),
            "backend_id": payload.get("backend_id"),
            "engine_version": payload.get("engine_version"),
            "protocol_version": payload.get("protocol_version"),
            "results": [result_to_json(result) for result in results],
            "errors": [],
        }
        sys.stdout.buffer.write(canonical_json_bytes(output) + b"\n")
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        print(f"entry_point_worker: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
