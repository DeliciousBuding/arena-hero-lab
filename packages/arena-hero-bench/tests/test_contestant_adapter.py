"""Tests for the contestant entry point adapter (P3-6)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from arena_hero_bench.contestant import (
    ContestantManifest,
    IsolationRequirements,
    ResourceRequirements,
)
from arena_hero_bench.contestant_adapter import (
    ContestantAdapterError,
    ContestantRawOutcome,
    ContestantRunResult,
    ContestantRunStatus,
    build_plan,
    build_request,
    build_spec,
    execute_contestant,
    normalize_result,
    run_contestant,
)
from arena_hero_bench.orchestration import (
    InMemoryArtifactStore,
    InMemoryExecutionLedger,
)
from arena_hero_bench.process_executor import BackendProcessSpec, ProcessExecutor

FIXTURES = Path(__file__).parent / "fixtures" / "contestant"
ENTRY_POINT_WORKER = FIXTURES / "entry_point_worker.py"
SIMULATED_RUNNER = FIXTURES / "simulated_runner.py"
_SHA256 = "0" * 64


def make_manifest(
    *,
    entry_point: str = "python -m arena_hero.agent.io.v1.runner",
    language: str = "python",
    version: str = "0.1.0",
    protocol_version: str = "arena.agent.io.v1",
    capabilities: frozenset[str] = frozenset(),
    timeout_seconds: float = 10.0,
    subprocess_required: bool = True,
    environment_allowlist: tuple[str, ...] = ("PATH",),
) -> ContestantManifest:
    return ContestantManifest(
        schema_version="arena.contestant.manifest.v1",
        contestant_id="smoke-bot",
        version=version,
        entry_point=entry_point,
        language=language,
        runtime="cpython 3.12",
        protocol_version=protocol_version,
        artifact_sha256=_SHA256,
        config_schema={},
        resources=ResourceRequirements(
            cpu_cores=1.0,
            memory_mb=256,
            process_limit=1,
            timeout_seconds=timeout_seconds,
        ),
        capabilities=capabilities,
        isolation=IsolationRequirements(
            subprocess_required=subprocess_required,
            environment_allowlist=environment_allowlist,
        ),
    )


def _smoke_manifest(mode: str, *, timeout_seconds: float = 10.0) -> ContestantManifest:
    return make_manifest(
        entry_point=f"{SIMULATED_RUNNER} --mode {mode}",
        timeout_seconds=timeout_seconds,
    )


def _run_smoke(
    manifest: ContestantManifest,
) -> tuple[ContestantRunResult, InMemoryArtifactStore, InMemoryExecutionLedger]:
    store = InMemoryArtifactStore()
    ledger = InMemoryExecutionLedger()
    result = run_contestant(
        manifest,
        worker_script=str(ENTRY_POINT_WORKER),
        artifact_store=store,
        ledger=ledger,
    )
    return result, store, ledger


# --- spec generation (table-driven) ------------------------------------------


def test_build_spec_module_form() -> None:
    manifest = make_manifest(entry_point="python -m arena_hero.agent.io.v1.runner")
    spec = build_spec(manifest)
    assert isinstance(spec, BackendProcessSpec)
    assert spec.backend_id == "smoke-bot"
    assert spec.engine_version == "0.1.0"
    assert spec.protocol_versions == frozenset({"arena.agent.io.v1"})
    assert spec.supported_features == frozenset()
    assert spec.worker_module == "arena_hero.agent.io.v1.runner"
    assert spec.worker_script is None
    assert spec.command() == (sys.executable, "-m", "arena_hero.agent.io.v1.runner")


def test_build_spec_module_form_with_args() -> None:
    manifest = make_manifest(
        entry_point=("python -m arena_hero.agent.io.v1.runner --mode subprocess --deadline-ms 5000")
    )
    spec = build_spec(manifest)
    assert spec.worker_module == "arena_hero.agent.io.v1.runner"
    assert spec.worker_script is None


def test_build_spec_script_form_with_args() -> None:
    manifest = make_manifest(entry_point=f"{SIMULATED_RUNNER} --mode ok")
    spec = build_spec(manifest)
    assert spec.worker_script == str(Path(SIMULATED_RUNNER).resolve())
    assert spec.worker_module is None


def test_build_spec_worker_script_override() -> None:
    manifest = make_manifest(entry_point=f"{SIMULATED_RUNNER} --mode ok")
    spec = build_spec(manifest, worker_script=str(ENTRY_POINT_WORKER))
    assert spec.worker_script == str(Path(ENTRY_POINT_WORKER).resolve())
    assert spec.worker_module is None


def test_build_spec_carries_capabilities() -> None:
    manifest = make_manifest(capabilities=frozenset({"deterministic", "replay"}))
    spec = build_spec(manifest)
    assert spec.supported_features == frozenset({"deterministic", "replay"})


def test_build_spec_requires_subprocess_isolation() -> None:
    manifest = make_manifest(subprocess_required=False)
    with pytest.raises(ContestantAdapterError, match="subprocess"):
        build_spec(manifest)


def test_build_spec_rejects_non_python_language() -> None:
    manifest = make_manifest(language="rust")
    with pytest.raises(ContestantAdapterError, match="language"):
        build_spec(manifest)


@pytest.mark.parametrize(
    ("entry_point", "match"),
    [
        ("bash run.sh", "entry_point must be"),
        ("python script.py", "entry_point must be"),
        ("python -m", "entry_point must be"),
        ("python -m bad!module", "entry_point module form"),
        ("python -m .hidden", "entry_point module form"),
    ],
)
def test_build_spec_fails_closed_entry_point(entry_point: str, match: str) -> None:
    manifest = make_manifest(entry_point=entry_point)
    with pytest.raises(ContestantAdapterError, match=match):
        build_spec(manifest)


def test_build_spec_fails_closed_invalid_protocol_version() -> None:
    manifest = make_manifest(protocol_version="not an identifier!")
    with pytest.raises(ContestantAdapterError):
        build_spec(manifest)


# --- request / plan building --------------------------------------------------


def test_build_request_carries_execution_spec() -> None:
    manifest = make_manifest(
        entry_point=f"{SIMULATED_RUNNER} --mode ok",
        environment_allowlist=("PATH", "LANG"),
        timeout_seconds=7.5,
    )
    request = build_request(manifest)
    assert request.config.backend_id == "smoke-bot"
    assert request.config.engine_version == "0.1.0"
    assert request.config.protocol_version == "arena.agent.io.v1"
    assert request.config.requested_features == frozenset()
    assert request.contestant_ids == ("smoke-bot",)
    assert request.input_artifact_sha256 is None
    assert request.config.parameters["entry_point"] == f"{SIMULATED_RUNNER} --mode ok"
    assert request.config.parameters["environment_allowlist"] == "PATH,LANG"
    assert request.config.parameters["timeout_seconds"] == "7.5"


def test_build_request_timeout_override() -> None:
    manifest = make_manifest(timeout_seconds=10.0)
    request = build_request(manifest, timeout_seconds=3.0)
    assert request.config.parameters["timeout_seconds"] == "3.0"


def test_build_request_rejects_non_positive_timeout() -> None:
    manifest = make_manifest(timeout_seconds=10.0)
    with pytest.raises(ContestantAdapterError, match="timeout"):
        build_request(manifest, timeout_seconds=0.0)


def test_build_plan_verifies() -> None:
    manifest = make_manifest()
    request = build_request(manifest)
    plan = build_plan(request)
    plan.verify()
    assert plan.requests == (request,)
    assert plan.operation_id == "contestant-op-1"


# --- result normalization (table-driven) --------------------------------------


def test_normalize_ok() -> None:
    result = normalize_result(ContestantRawOutcome())
    assert result.status is ContestantRunStatus.OK
    assert result.ok
    assert result.exit_code == 0
    assert result.error is None


def test_normalize_carries_stdout_stderr() -> None:
    result = normalize_result(
        ContestantRawOutcome(stdout="hello", stderr="warn", round_status="ok")
    )
    assert result.status is ContestantRunStatus.OK
    assert result.stdout == "hello"
    assert result.stderr == "warn"


@pytest.mark.parametrize(
    ("outcome", "expected", "expected_exit_code"),
    [
        (ContestantRawOutcome(timed_out=True), ContestantRunStatus.TIMEOUT, None),
        (ContestantRawOutcome(crashed=True, exit_code=3), ContestantRunStatus.CRASH, 3),
        (ContestantRawOutcome(crashed=True), ContestantRunStatus.CRASH, None),
        (
            ContestantRawOutcome(round_status="timeout", round_error="deadline"),
            ContestantRunStatus.TIMEOUT,
            None,
        ),
        (
            ContestantRawOutcome(round_status="crash", round_error="boom"),
            ContestantRunStatus.CRASH,
            None,
        ),
        (
            ContestantRawOutcome(round_status="protocol", round_error="bad frame"),
            ContestantRunStatus.PROTOCOL,
            None,
        ),
        (
            ContestantRawOutcome(round_status="error", round_error="oops"),
            ContestantRunStatus.ERROR,
            None,
        ),
        (ContestantRawOutcome(round_status="ok"), ContestantRunStatus.OK, 0),
        (ContestantRawOutcome(round_status="bogus"), ContestantRunStatus.ERROR, None),
        (
            ContestantRawOutcome(worker_error="worker exceeded per-task timeout of 1 seconds"),
            ContestantRunStatus.TIMEOUT,
            None,
        ),
        (
            ContestantRawOutcome(worker_error="worker exited with code 7: oops"),
            ContestantRunStatus.CRASH,
            7,
        ),
        (
            ContestantRawOutcome(
                worker_error="worker did not terminate within the bounded reap window"
            ),
            ContestantRunStatus.CRASH,
            None,
        ),
        (
            ContestantRawOutcome(worker_error="invalid worker payload: boom"),
            ContestantRunStatus.ERROR,
            None,
        ),
        (ContestantRawOutcome(exit_code=5), ContestantRunStatus.CRASH, 5),
    ],
)
def test_normalize_classification_table(
    outcome: ContestantRawOutcome,
    expected: ContestantRunStatus,
    expected_exit_code: int | None,
) -> None:
    result = normalize_result(outcome)
    assert result.status is expected
    assert result.exit_code == expected_exit_code
    assert result.ok is (expected is ContestantRunStatus.OK)


# --- full-chain smoke through the process executor ----------------------------


def test_run_contestant_ok_smoke() -> None:
    manifest = _smoke_manifest("ok")
    result, _store, _ledger = _run_smoke(manifest)
    assert result.ok
    assert result.status is ContestantRunStatus.OK
    assert result.exit_code == 0
    assert result.error is None
    assert result.artifact_ref is not None
    assert result.artifact_ref.startswith("sha256:")


def test_run_contestant_round_timeout() -> None:
    manifest = _smoke_manifest("timeout")
    result, _store, _ledger = _run_smoke(manifest)
    assert result.status is ContestantRunStatus.TIMEOUT
    assert not result.ok
    assert result.error is not None


def test_run_contestant_round_crash() -> None:
    manifest = _smoke_manifest("crash")
    result, _store, _ledger = _run_smoke(manifest)
    assert result.status is ContestantRunStatus.CRASH
    assert not result.ok


def test_run_contestant_round_protocol() -> None:
    manifest = _smoke_manifest("protocol")
    result, _store, _ledger = _run_smoke(manifest)
    assert result.status is ContestantRunStatus.PROTOCOL
    assert not result.ok


def test_run_contestant_round_error() -> None:
    manifest = _smoke_manifest("error")
    result, _store, _ledger = _run_smoke(manifest)
    assert result.status is ContestantRunStatus.ERROR
    assert not result.ok


def test_run_contestant_outer_timeout_isolated_by_executor() -> None:
    manifest = _smoke_manifest("hang", timeout_seconds=1.0)
    result, _store, _ledger = _run_smoke(manifest)
    assert result.status is ContestantRunStatus.TIMEOUT
    assert not result.ok
    assert result.error is not None
    assert "per-task timeout" in (result.error or "")


def test_run_contestant_hard_exit_code_isolated_by_executor() -> None:
    manifest = _smoke_manifest("hard_exit")
    result, _store, _ledger = _run_smoke(manifest)
    assert result.status is ContestantRunStatus.CRASH
    assert result.exit_code == 3
    assert not result.ok


def test_execute_contestant_stores_artifact_and_resumes() -> None:
    manifest = _smoke_manifest("ok")
    store = InMemoryArtifactStore()
    ledger = InMemoryExecutionLedger()
    spec = build_spec(manifest, worker_script=str(ENTRY_POINT_WORKER))
    with ProcessExecutor(
        backend_specs={spec.backend_id: spec},
        artifact_store=store,
        ledger=ledger,
        max_workers=1,
        per_task_timeout=manifest.resources.timeout_seconds,
    ) as executor:
        first = execute_contestant(manifest, executor)
        second = execute_contestant(manifest, executor)
    assert first.ok
    assert second.ok
    assert first.artifact_ref == second.artifact_ref
    assert first.artifact_ref is not None
    digest = first.artifact_ref.removeprefix("sha256:")
    assert store.get(digest) is not None
