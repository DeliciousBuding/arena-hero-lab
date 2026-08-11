"""Bounded offline replay soak harness (P6-4): manifest, runner, reverse validation.

The happy path exercises the real P6-2/P6-3 differential classifiers and the
real process executor over the canonical reference workload. Reverse
validation injects faults at the soak-driver level (corrupt corpus, valid
but different corpus, raising/leaking/residue steps) and asserts the
fail-closed classification; it never mocks the executor or the differential.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from arena_hero_bench.cli import main as cli_main
from arena_hero_bench.differential import DifferentialError, run_differential_from_manifest
from arena_hero_bench.kpi_differential import run_kpi_differential_from_manifest
from arena_hero_bench.soak import (
    SOAK_SCHEMA,
    SoakIssueKind,
    SoakManifestError,
    SoakStatus,
    SoakStepSpec,
    _descendant_pids,
    _differential_step,
    _kpi_differential_step,
    _open_handle_count,
    _StepResult,
    load_soak_manifest,
    run_soak,
)
from arena_hero_sim.reference_workload import CANONICAL_REFERENCE_WORKLOAD_SHA256
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

FIXTURES = Path(__file__).parent / "fixtures"
SOAK_FIXTURES = FIXTURES / "soak"
SOAK_MANIFEST = SOAK_FIXTURES / "run-burnin-20260802-a-v1.json"

_KPI_MANIFEST_REL = Path("kpi_differential") / "run-burnin-20260802-a-v1.json"
_DIFF_MANIFEST_REL = Path("differential") / "run-burnin-20260802-a-v1.json"


def _json_dict(value: Mapping[str, object]) -> dict[str, JsonValue]:
    narrowed = to_json_value(value)
    assert isinstance(narrowed, dict)
    return narrowed


def _copy_fixtures(tmp_path: Path, name: str) -> Path:
    dest = tmp_path / name
    shutil.copytree(FIXTURES, dest)
    return dest


def _soak_manifest_dict(
    *,
    rounds: int = 2,
    max_duration_seconds: float | None = 120.0,
    handle_tolerance: int = 32,
    soak_id: str = "soak-test-v1",
    steps: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    if steps is None:
        steps = [{"id": "step-a", "kind": "differential", "manifest": str(_DIFF_MANIFEST_REL)}]
    return {
        "schemaVersion": SOAK_SCHEMA,
        "soak_id": soak_id,
        "rounds": rounds,
        "max_duration_seconds": max_duration_seconds,
        "handle_tolerance": handle_tolerance,
        "steps": [dict(item) for item in steps],
    }


def _write_soak_manifest(
    path: Path,
    *,
    rounds: int = 2,
    max_duration_seconds: float | None = 120.0,
    handle_tolerance: int = 32,
    soak_id: str = "soak-test-v1",
    steps: Sequence[Mapping[str, object]] | None = None,
) -> Path:
    path.write_text(
        json.dumps(
            _soak_manifest_dict(
                rounds=rounds,
                max_duration_seconds=max_duration_seconds,
                handle_tolerance=handle_tolerance,
                soak_id=soak_id,
                steps=steps,
            ),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _run_cli(*argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli_main(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


def _noop_step() -> Any:
    return lambda: _StepResult(content_sha256="a" * 64, artifact_sha256=None, metadata={})


# ---------------------------------------------------------------------------
# Manifest parsing (fail closed)
# ---------------------------------------------------------------------------


def test_soak_manifest_loads_committed_fixture() -> None:
    manifest = load_soak_manifest(SOAK_MANIFEST)
    assert manifest.schema_version == SOAK_SCHEMA
    assert manifest.soak_id == "soak-burnin-20260802-a-v1"
    assert manifest.rounds == 2
    assert manifest.max_duration_seconds == 120.0
    assert manifest.handle_tolerance == 32
    assert [step.kind for step in manifest.steps] == [
        "differential",
        "kpi_differential",
        "process_executor",
    ]
    replay, kpi, executor = manifest.steps
    assert replay.step_id == "replay-differential"
    assert replay.manifest == Path("../differential/run-burnin-20260802-a-v1.json")
    assert kpi.step_id == "kpi-differential"
    assert kpi.manifest == Path("../kpi_differential/run-burnin-20260802-a-v1.json")
    assert executor.step_id == "process-executor"
    assert executor.manifest is None
    assert executor.max_workers == 1
    assert executor.scenario_count == 3


def test_soak_manifest_to_json_round_trip(tmp_path: Path) -> None:
    manifest = load_soak_manifest(SOAK_MANIFEST)
    output = tmp_path / "roundtrip.json"
    output.write_text(json.dumps(manifest.to_json(), sort_keys=True), encoding="utf-8")
    assert load_soak_manifest(output) == manifest


def test_soak_manifest_rejects_wrong_schema_version(tmp_path: Path) -> None:
    payload = _soak_manifest_dict()
    payload["schemaVersion"] = "arena.bench.replay-soak.v999"
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="schemaVersion"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_bad_soak_id(tmp_path: Path) -> None:
    payload = _soak_manifest_dict(soak_id="Soak-Test")
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="soak_id"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_zero_rounds(tmp_path: Path) -> None:
    payload = _soak_manifest_dict(rounds=0)
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="rounds"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_nonpositive_duration(tmp_path: Path) -> None:
    payload = _soak_manifest_dict(max_duration_seconds=0.0)
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="max_duration_seconds"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_negative_tolerance(tmp_path: Path) -> None:
    payload = _soak_manifest_dict(handle_tolerance=-1)
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="handle_tolerance"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_empty_steps(tmp_path: Path) -> None:
    payload = _soak_manifest_dict(steps=[])
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="at least one step"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_duplicate_step_ids(tmp_path: Path) -> None:
    steps = [
        {"id": "same", "kind": "differential", "manifest": str(_DIFF_MANIFEST_REL)},
        {"id": "same", "kind": "kpi_differential", "manifest": str(_KPI_MANIFEST_REL)},
    ]
    payload = _soak_manifest_dict(steps=steps)
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="unique"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_unknown_kind(tmp_path: Path) -> None:
    steps = [{"id": "mystery", "kind": "mystery-engine"}]
    payload = _soak_manifest_dict(steps=steps)
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="mystery-engine"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_differential_without_manifest(tmp_path: Path) -> None:
    steps = [{"id": "no-manifest", "kind": "differential"}]
    payload = _soak_manifest_dict(steps=steps)
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="requires a manifest"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_process_executor_with_manifest(tmp_path: Path) -> None:
    steps = [{"id": "executor", "kind": "process_executor", "manifest": "workload.json"}]
    payload = _soak_manifest_dict(steps=steps)
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="canonical reference workload"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_boolean_rounds(tmp_path: Path) -> None:
    payload = _soak_manifest_dict()
    payload["rounds"] = True
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SoakManifestError, match="rounds"):
        load_soak_manifest(path)


def test_soak_manifest_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SoakManifestError, match="cannot read soak manifest"):
        load_soak_manifest(tmp_path / "missing.json")


def test_soak_manifest_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SoakManifestError, match="cannot read soak manifest"):
        load_soak_manifest(path)


# ---------------------------------------------------------------------------
# Happy path: real differential + KPI differential + process executor
# ---------------------------------------------------------------------------


def test_soak_happy_path_passes_all_steps() -> None:
    report = run_soak(SOAK_MANIFEST)
    assert report.status is SoakStatus.PASS
    assert report.attested is True
    assert report.injected_steps is False
    assert report.rounds_requested == 2
    assert report.rounds_completed == 2
    assert report.issues == ()
    assert report.counts == {kind.value: 0 for kind in SoakIssueKind}


def test_soak_repeated_corpus_digests_are_stable() -> None:
    report = run_soak(SOAK_MANIFEST)
    assert len(report.rounds) == 2
    round_one = {step.step_id: step.content_sha256 for step in report.rounds[0].steps}
    for round_ in report.rounds[1:]:
        for step in round_.steps:
            assert step.content_sha256 == round_one[step.step_id]


def test_soak_round_and_step_structure() -> None:
    report = run_soak(SOAK_MANIFEST)
    for round_ in report.rounds:
        assert round_.round_number in (1, 2)
        assert round_.handle_count >= 0
        assert round_.descendant_pids == ()
        assert round_.issues == ()
        assert [step.kind for step in round_.steps] == [
            "differential",
            "kpi_differential",
            "process_executor",
        ]
        for step in round_.steps:
            assert step.content_sha256 is not None
            assert len(step.content_sha256) == 64
            assert step.elapsed_seconds >= 0.0


def test_soak_process_executor_step_uses_frozen_workload() -> None:
    report = run_soak(SOAK_MANIFEST)
    executor_step = report.rounds[0].steps[2]
    assert executor_step.kind == "process_executor"
    metadata = _json_dict(executor_step.metadata)
    assert metadata["workload_sha256"] == CANONICAL_REFERENCE_WORKLOAD_SHA256
    assert metadata["request_count"] == 3
    assert metadata["publishable"] is True
    assert metadata["status"] == "complete"
    assert len(str(metadata["plan_sha256"])) == 64


def test_soak_independent_runs_share_digest_anchors() -> None:
    first = run_soak(SOAK_MANIFEST)
    second = run_soak(SOAK_MANIFEST)
    first_anchors = {step.step_id: step.content_sha256 for step in first.rounds[0].steps}
    for step in second.rounds[0].steps:
        assert step.content_sha256 == first_anchors[step.step_id]


def test_soak_report_is_content_addressed() -> None:
    report = run_soak(SOAK_MANIFEST)
    payload = _json_dict(report.to_json())
    digest = payload.pop("artifact_sha256")
    assert digest == content_sha256(payload)


# ---------------------------------------------------------------------------
# Reverse validation: corruption, drift, exceptions, leaks, residue, duration
# ---------------------------------------------------------------------------


def _corrupt_differential_corpus(tree: Path) -> None:
    tick_path = tree / "differential" / "ts" / "burnin-20260802-a" / "40437.json"
    payload = json.loads(tick_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["corrupted"] = True
    tick_path.write_text(json.dumps(payload), encoding="utf-8")


def test_soak_corrupt_corpus_fails_closed_through_real_differential(
    tmp_path: Path,
) -> None:
    tree = _copy_fixtures(tmp_path, "tree")
    _corrupt_differential_corpus(tree)
    with pytest.raises(DifferentialError, match="digest"):
        run_differential_from_manifest(tree / _DIFF_MANIFEST_REL)


def test_soak_inject_corrupt_round_fails_and_classifies(tmp_path: Path) -> None:
    clean = _copy_fixtures(tmp_path, "clean")
    corrupted = _copy_fixtures(tmp_path, "corrupted")
    _corrupt_differential_corpus(corrupted)
    manifest_path = _write_soak_manifest(
        tmp_path / "soak.json",
        rounds=2,
        steps=[
            {
                "id": "replay-differential",
                "kind": "differential",
                "manifest": "clean/differential/run-burnin-20260802-a-v1.json",
            }
        ],
    )
    state = {"calls": 0}

    def factory(spec: SoakStepSpec, base: Path) -> Any:
        del base
        assert spec.step_id == "replay-differential"

        def run() -> _StepResult:
            tree = clean if state["calls"] % 2 == 0 else corrupted
            state["calls"] += 1
            sub = SoakStepSpec(
                step_id=spec.step_id,
                kind=spec.kind,
                manifest=_DIFF_MANIFEST_REL,
            )
            return _differential_step(sub, tree)()

        return run

    report = run_soak(manifest_path, step_factory=factory)
    assert report.status is SoakStatus.FAIL
    assert report.injected_steps is True
    assert report.attested is False
    kinds = [issue.kind for issue in report.issues]
    assert kinds == [SoakIssueKind.STEP_EXCEPTION]
    issue = report.issues[0]
    assert issue.round_number == 2
    assert issue.step_id == "replay-differential"
    assert "digest" in issue.detail
    assert report.counts[SoakIssueKind.STEP_EXCEPTION.value] == 1
    failed_outcome = report.rounds[1].steps[0]
    assert failed_outcome.content_sha256 is None
    assert "error" in _json_dict(failed_outcome.metadata)


def test_soak_drift_premise_kpi_digests_differ(tmp_path: Path) -> None:
    clean = _copy_fixtures(tmp_path, "clean")
    modified = _copy_fixtures(tmp_path, "modified")
    snapshot = modified / "kpi_differential" / "python" / "observation-snapshots-v1.jsonl"
    lines = snapshot.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert isinstance(record, dict)
    record["resources"] = int(record["resources"]) + 1
    lines[0] = json.dumps(record, sort_keys=True)
    snapshot.write_text("\n".join(lines) + "\n", encoding="utf-8")
    first = run_kpi_differential_from_manifest(clean / _KPI_MANIFEST_REL)
    second = run_kpi_differential_from_manifest(modified / _KPI_MANIFEST_REL)
    assert first.artifact_sha256 != second.artifact_sha256
    assert first.to_json() != second.to_json()


def test_soak_digest_drift_fails_and_classifies(tmp_path: Path) -> None:
    clean = _copy_fixtures(tmp_path, "clean")
    modified = _copy_fixtures(tmp_path, "modified")
    snapshot = modified / "kpi_differential" / "python" / "observation-snapshots-v1.jsonl"
    lines = snapshot.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert isinstance(record, dict)
    record["resources"] = int(record["resources"]) + 1
    lines[0] = json.dumps(record, sort_keys=True)
    snapshot.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_path = _write_soak_manifest(
        tmp_path / "soak.json",
        rounds=2,
        steps=[
            {
                "id": "kpi-differential",
                "kind": "kpi_differential",
                "manifest": "clean/kpi_differential/run-burnin-20260802-a-v1.json",
            }
        ],
    )
    state = {"calls": 0}

    def factory(spec: SoakStepSpec, base: Path) -> Any:
        del base

        def run() -> _StepResult:
            tree = clean if state["calls"] % 2 == 0 else modified
            state["calls"] += 1
            sub = SoakStepSpec(
                step_id=spec.step_id,
                kind=spec.kind,
                manifest=_KPI_MANIFEST_REL,
            )
            return _kpi_differential_step(sub, tree)()

        return run

    report = run_soak(manifest_path, step_factory=factory)
    assert report.status is SoakStatus.FAIL
    assert [issue.kind for issue in report.issues] == [SoakIssueKind.DIGEST_DRIFT]
    issue = report.issues[0]
    assert issue.round_number == 2
    assert issue.step_id == "kpi-differential"
    assert "changed from" in issue.detail
    assert report.counts[SoakIssueKind.DIGEST_DRIFT.value] == 1
    round_one_digest = report.rounds[0].steps[0].content_sha256
    round_two_digest = report.rounds[1].steps[0].content_sha256
    assert round_one_digest is not None and round_two_digest is not None
    assert round_one_digest != round_two_digest


def test_soak_uncaught_exception_caught_and_reported(tmp_path: Path) -> None:
    manifest_path = _write_soak_manifest(tmp_path / "soak.json", rounds=2)

    def factory(spec: SoakStepSpec, base: Path) -> Any:
        del base
        assert spec.step_id == "step-a"

        def run() -> _StepResult:
            raise RuntimeError("boom")

        return run

    report = run_soak(manifest_path, step_factory=factory)
    assert report.status is SoakStatus.FAIL
    assert report.injected_steps is True
    assert report.attested is False
    assert len(report.issues) == 2  # one per round
    for issue in report.issues:
        assert issue.kind is SoakIssueKind.STEP_EXCEPTION
        assert issue.step_id == "step-a"
        assert "boom" in issue.detail
    assert report.counts[SoakIssueKind.STEP_EXCEPTION.value] == 2
    for round_ in report.rounds:
        outcome = round_.steps[0]
        assert outcome.content_sha256 is None
        assert "RuntimeError" in str(_json_dict(outcome.metadata)["error"])


def test_soak_resource_leak_classified(tmp_path: Path) -> None:
    manifest_path = _write_soak_manifest(
        tmp_path / "soak.json",
        rounds=2,
        handle_tolerance=0,
    )
    held: list[Any] = []
    counter = {"n": 0}

    def factory(spec: SoakStepSpec, base: Path) -> Any:
        del base, spec

        def run() -> _StepResult:
            for _ in range(3):
                # Intentional leak: the open stream is held across the round so
                # the at-rest handle/fd probe can observe the growth.
                stream = open(tmp_path / f"leak-{counter['n']}.bin", "wb")  # noqa: SIM115
                held.append(stream)
                counter["n"] += 1
            return _StepResult(content_sha256="a" * 64, artifact_sha256=None, metadata={})

        return run

    try:
        report = run_soak(manifest_path, step_factory=factory)
    finally:
        for stream in held:
            stream.close()
    assert report.status is SoakStatus.FAIL
    assert SoakIssueKind.RESOURCE_LEAK in {issue.kind for issue in report.issues}
    leak_issues = [issue for issue in report.issues if issue.kind is SoakIssueKind.RESOURCE_LEAK]
    assert leak_issues
    assert "grew from" in leak_issues[0].detail
    assert report.counts[SoakIssueKind.RESOURCE_LEAK.value] >= 1


def test_soak_process_residue_classified(tmp_path: Path) -> None:
    manifest_path = _write_soak_manifest(tmp_path / "soak.json", rounds=1)
    child: subprocess.Popen[bytes] | None = None

    def factory(spec: SoakStepSpec, base: Path) -> Any:
        del base, spec

        def run() -> _StepResult:
            nonlocal child
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            time.sleep(0.3)
            return _StepResult(content_sha256="a" * 64, artifact_sha256=None, metadata={})

        return run

    try:
        report = run_soak(manifest_path, step_factory=factory)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
    assert report.status is SoakStatus.FAIL
    residue = [issue for issue in report.issues if issue.kind is SoakIssueKind.PROCESS_RESIDUE]
    assert len(residue) == 1
    assert child is not None
    assert str(child.pid) in residue[0].detail
    assert child.pid in report.rounds[0].descendant_pids
    assert report.counts[SoakIssueKind.PROCESS_RESIDUE.value] == 1
    assert child.pid not in _descendant_pids()


def test_soak_duration_cap_fails_closed(tmp_path: Path) -> None:
    manifest_path = _write_soak_manifest(
        tmp_path / "soak.json",
        rounds=100,
        max_duration_seconds=0.05,
    )

    def factory(spec: SoakStepSpec, base: Path) -> Any:
        del base, spec
        return _noop_step()

    report = run_soak(manifest_path, step_factory=factory)
    assert report.status is SoakStatus.FAIL
    assert report.rounds_completed < report.rounds_requested
    assert report.rounds_completed >= 1
    kinds = [issue.kind for issue in report.issues]
    assert kinds == [SoakIssueKind.DURATION_EXCEEDED]
    assert "max_duration_seconds" in report.issues[0].detail


def test_soak_failed_round_does_not_establish_drift_reference(tmp_path: Path) -> None:
    manifest_path = _write_soak_manifest(tmp_path / "soak.json", rounds=3)
    state = {"calls": 0}

    def factory(spec: SoakStepSpec, base: Path) -> Any:
        del base, spec

        def run() -> _StepResult:
            state["calls"] += 1
            if state["calls"] == 1:
                raise ValueError("first round fails")
            return _StepResult(content_sha256="b" * 64, artifact_sha256=None, metadata={})

        return run

    report = run_soak(manifest_path, step_factory=factory)
    assert report.status is SoakStatus.FAIL
    assert report.counts[SoakIssueKind.STEP_EXCEPTION.value] == 1
    assert report.counts[SoakIssueKind.DIGEST_DRIFT.value] == 0
    assert report.rounds[1].steps[0].content_sha256 == "b" * 64
    assert report.rounds[2].steps[0].content_sha256 == "b" * 64


def test_soak_injected_steps_never_attest(tmp_path: Path) -> None:
    manifest_path = _write_soak_manifest(tmp_path / "soak.json", rounds=1)

    def factory(spec: SoakStepSpec, base: Path) -> Any:
        del base, spec
        return _noop_step()

    report = run_soak(manifest_path, step_factory=factory)
    assert report.status is SoakStatus.PASS
    assert report.injected_steps is True
    assert report.attested is False


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_soak_prints_machine_readable_report() -> None:
    code, stdout, stderr = _run_cli("soak", "--run", str(SOAK_MANIFEST))
    assert code == 0, stderr
    payload = _json_dict(json.loads(stdout))
    assert payload["schema_version"] == SOAK_SCHEMA
    assert payload["status"] == "pass"
    assert payload["attested"] is True
    digest = payload["artifact_sha256"]
    payload_without_digest = {
        key: value for key, value in payload.items() if key != "artifact_sha256"
    }
    assert digest == content_sha256(payload_without_digest)


def test_cli_soak_fails_closed_on_manifest_error(tmp_path: Path) -> None:
    code, _, stderr = _run_cli("soak", "--run", str(tmp_path / "missing.json"))
    assert code == 2
    assert "soak" in stderr


# ---------------------------------------------------------------------------
# Resource probes
# ---------------------------------------------------------------------------


def test_open_handle_probe_tracks_open_file(tmp_path: Path) -> None:
    before = _open_handle_count()
    with open(tmp_path / "probe.bin", "wb") as stream:
        del stream
        during = _open_handle_count()
        assert during > before


def test_descendant_probe_returns_int_set() -> None:
    descendants = _descendant_pids()
    assert isinstance(descendants, frozenset)
    assert all(isinstance(pid, int) for pid in descendants)
    assert os.getpid() not in descendants
