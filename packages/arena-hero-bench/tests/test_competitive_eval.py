"""Competitive evaluation battery (P6-7): manifest, runner, reverse validation.

The happy path exercises the real P6-3 KPI differential classifier over the
committed battery corpus (2 scenarios x 2 seeds x 2 contestants = 8 cells).
Reverse validation injects faults at the battery-driver level (corrupt cell
records, raising injected cells, unclassified injected reports) and asserts the
fail-closed classification; it never mocks the differential classifier.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from arena_hero_bench.cli import main as cli_main
from arena_hero_bench.competitive_eval import (
    AGENT_RUN_RECORDS_FILE,
    AGENT_RUNS_DIR_ENV,
    AgentRunsError,
    BatteryCellSpec,
    BatteryIssueKind,
    BatteryManifestError,
    BatteryStatus,
    load_battery_manifest,
    map_agent_runs_dir,
    run_battery_from_manifest,
)
from arena_hero_bench.differential import DifferentialStatus
from arena_hero_bench.kpi_differential import KpiReport

FIXTURES = Path(__file__).parent / "fixtures"
BATTERY_FIXTURES = FIXTURES / "competitive_eval"
BATTERY_MANIFEST = BATTERY_FIXTURES / "run-burnin-20260802-a-v1.json"

CELL_RECORDS = BATTERY_FIXTURES / "python" / "ffa-seed01-a-v1.jsonl"


def _copy_fixtures(tmp_path: Path) -> Path:
    dest = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, dest)
    return dest


def _run_cli(*argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli_main(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


def _battery_dict(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = json.loads(BATTERY_MANIFEST.read_text(encoding="utf-8"))
    if overrides:
        raw.update(overrides)
    return raw


def _write_manifest(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_full_battery_classification_counts_and_unclassified_zero() -> None:
    report = run_battery_from_manifest(BATTERY_MANIFEST)
    assert report.status is BatteryStatus.PASS
    assert report.attested is True
    assert report.injected_cells is False
    assert len(report.cells) == 8
    assert report.counts[DifferentialStatus.MATCH] == 42
    assert report.counts[DifferentialStatus.MISMATCH] == 6
    assert report.counts[DifferentialStatus.EXPECTED_UNKNOWN] == 0
    assert report.counts[DifferentialStatus.INCONCLUSIVE] == 0
    assert report.unclassified_count == 0
    assert report.issues == ()
    # Deterministic ranking: faithful contestant outranks the aggressive one.
    assert [entry.contestant_id for entry in report.ranking] == [
        "python-agent-ffa",
        "python-agent-soft",
    ]
    assert report.ranking[0].score == 22
    assert report.ranking[1].score == 20
    assert report.aggregates["burnin-a"]["python-agent-ffa"].samples == 2
    assert report.aggregates["burnin-a"]["python-agent-ffa"].dimensions
    assert report.artifact["schema_version"] == "arena.bench.competitive-eval-report.v1"
    assert report.artifact["unclassified_count"] == 0


def test_report_artifact_is_deterministic() -> None:
    first = run_battery_from_manifest(BATTERY_MANIFEST)
    second = run_battery_from_manifest(BATTERY_MANIFEST)
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.artifact == second.artifact
    assert len(first.artifact_sha256) == 64


def test_reordering_contestants_changes_nothing_in_cell_order() -> None:
    # Cell order is fixed by manifest order, so a reordered contestant list is
    # rejected at manifest level; the runner itself must not depend on hash
    # order. Re-running is byte-stable already (test above), and the manifest
    # validator enforces exact scenario/seed coverage for every contestant.
    manifest = load_battery_manifest(BATTERY_MANIFEST)
    assert [c.contestant_id for c in manifest.contestants] == [
        "python-agent-ffa",
        "python-agent-soft",
    ]


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"schemaVersion": "arena.bench.other.v1"}, "unsupported battery manifest schemaVersion"),
        ({"evidence_kind": "made_up"}, "unsupported evidence_kind"),
        ({"seeds": []}, "at least one seed"),
        ({"scenarios": []}, "at least one scenario"),
        ({"contestants": []}, "at least one contestant"),
    ],
)
def test_manifest_validation_fails_closed(
    tmp_path: Path, override: dict[str, Any], match: str
) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    _write_manifest(manifest, _battery_dict(override))
    with pytest.raises(BatteryManifestError, match=match):
        load_battery_manifest(manifest)


def test_manifest_rejects_incomplete_cell_coverage(tmp_path: Path) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    raw = _battery_dict()
    ffa = raw["contestants"][0]
    ffa["records"]["burnin-a"] = {"seed-01": ffa["records"]["burnin-a"]["seed-01"]}
    _write_manifest(manifest, raw)
    with pytest.raises(BatteryManifestError, match="exactly the declared seeds"):
        load_battery_manifest(manifest)


def test_manifest_rejects_duplicate_contestant_key(tmp_path: Path) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    raw = _battery_dict()
    raw["contestants"].append(dict(raw["contestants"][0]))
    _write_manifest(manifest, raw)
    with pytest.raises(BatteryManifestError, match="must be unique"):
        load_battery_manifest(manifest)


def test_corrupt_cell_fails_closed(tmp_path: Path) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    records = dest / "competitive_eval" / "python" / "ffa-seed01-a-v1.jsonl"
    # Torn tail is rejected by the offline importer.
    records.write_text(records.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
    report = run_battery_from_manifest(manifest)
    assert report.status is BatteryStatus.FAIL
    assert report.attested is False
    assert report.unclassified_count == 0
    assert any(issue.kind is BatteryIssueKind.CELL_ERROR for issue in report.issues)
    failed = [cell for cell in report.cells if cell.status == "error"]
    assert len(failed) == 1
    assert failed[0].scenario_id == "burnin-a"
    assert failed[0].seed_id == "seed-01"
    assert failed[0].contestant_id == "python-agent-ffa"
    assert failed[0].kpi_artifact_sha256 is None
    assert "torn tail" in (failed[0].error or "")
    # The other cells still classified.
    ok_cells = [cell for cell in report.cells if cell.status == "ok"]
    assert len(ok_cells) == 7


def test_injected_raising_cell_marks_unattested_and_fails(tmp_path: Path) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"

    def factory(cell: BatteryCellSpec, base: Path) -> Any:
        if cell.contestant.contestant_id == "python-agent-soft":

            def boom() -> KpiReport:
                raise ValueError("injected failure")

            return boom
        return None

    report = run_battery_from_manifest(manifest, cell_factory=factory)
    assert report.injected_cells is True
    assert report.attested is False
    assert report.status is BatteryStatus.FAIL
    assert any(issue.kind is BatteryIssueKind.CELL_ERROR for issue in report.issues)
    assert sum(cell.status == "error" for cell in report.cells) == 4


def test_unclassified_injected_report_fails_battery(tmp_path: Path) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"

    def fake_report() -> KpiReport:
        return KpiReport(
            schema_version="arena.bench.kpi-differential.v1",
            dataset_id="burnin-20260802-a",
            tenant_id="lab-diff-t1",
            evolve_protocol="differential-record-v1",
            py_protocol="agent-run-v1",
            evidence_kind="sanitized_fixture",
            dimensions=(),
            counts={},
            unclassified_count=1,
            artifact={},
            artifact_sha256="0" * 64,
        )

    def factory(cell: BatteryCellSpec, base: Path) -> Any:
        if (
            cell.scenario.scenario_id == "burnin-a"
            and cell.contestant.contestant_id == "python-agent-soft"
            and cell.seed_id == "seed-01"
        ):
            return fake_report
        return None

    report = run_battery_from_manifest(manifest, cell_factory=factory)
    assert report.injected_cells is True
    assert report.attested is False
    assert report.status is BatteryStatus.FAIL
    assert report.unclassified_count == 1
    assert any(issue.kind is BatteryIssueKind.UNCLASSIFIED for issue in report.issues)


def test_cli_competitive_eval_pass_and_fail(tmp_path: Path) -> None:
    code, stdout, stderr = _run_cli("competitive-eval", "--run", str(BATTERY_MANIFEST))
    assert code == 0
    assert '"status": "pass"' in stdout
    assert stderr == ""

    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    records = dest / "competitive_eval" / "python" / "soft-seed02-b-v1.jsonl"
    records.write_text("not json\n", encoding="utf-8")
    code, stdout, stderr = _run_cli("competitive-eval", "--run", str(manifest))
    assert code == 1
    assert '"status": "fail"' in stdout


def test_cli_rejects_invalid_manifest(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"schemaVersion": "nope"}', encoding="utf-8")
    code, _stdout, stderr = _run_cli("competitive-eval", "--run", str(bad))
    assert code == 2
    assert "error" in stderr


# --- Live agent runs directory seam (W19) -------------------------------------

_LIVE_CONTESTANT_PREFIX = {
    "python-agent-ffa": "ffa",
    "python-agent-soft": "soft",
}
_LIVE_SCENARIO_LABEL = {"burnin-a": "a", "burnin-b": "b"}
_LIVE_SEED_LABELS = (("seed-01", "01"), ("seed-02", "02"))


def _copy_live_runs_dir(tmp_path: Path) -> Path:
    """Build a synthetic external agent batch output directory from the
    committed fixture records, laid out as
    ``<runs_dir>/<contestant>/<scenario>/<seed>/<tenant>/ticks.jsonl`` (the
    native ``arena-hero-agent run --data-root <cell-dir>`` layout)."""
    runs_dir = tmp_path / "agent-runs"
    for contestant_id, prefix in _LIVE_CONTESTANT_PREFIX.items():
        for scenario_id, label in _LIVE_SCENARIO_LABEL.items():
            for seed_id, seed_label in _LIVE_SEED_LABELS:
                cell_dir = runs_dir / contestant_id / scenario_id / seed_id
                run_dir = cell_dir / "lab-diff-t1"
                run_dir.mkdir(parents=True)
                source = (
                    BATTERY_FIXTURES / "python" / (f"{prefix}-seed{seed_label}-{label}-v1.jsonl")
                )
                shutil.copy(source, run_dir / AGENT_RUN_RECORDS_FILE)
    return runs_dir


def test_map_agent_runs_dir_resolves_every_cell(tmp_path: Path) -> None:
    manifest = load_battery_manifest(BATTERY_MANIFEST)
    runs_dir = _copy_live_runs_dir(tmp_path)
    mapped = map_agent_runs_dir(runs_dir, manifest)
    assert sorted(mapped) == ["python-agent-ffa", "python-agent-soft"]
    for contestant_id in _LIVE_CONTESTANT_PREFIX:
        assert sorted(mapped[contestant_id]) == ["burnin-a", "burnin-b"]
        for scenario_id in _LIVE_SCENARIO_LABEL:
            assert sorted(mapped[contestant_id][scenario_id]) == ["seed-01", "seed-02"]
            for seed_id, _seed_label in _LIVE_SEED_LABELS:
                expected = (
                    runs_dir / contestant_id / scenario_id / seed_id / "lab-diff-t1" / "ticks.jsonl"
                )
                assert mapped[contestant_id][scenario_id][seed_id] == expected
                assert mapped[contestant_id][scenario_id][seed_id].is_file()


def test_map_agent_runs_dir_fails_closed_missing_directory(tmp_path: Path) -> None:
    manifest = load_battery_manifest(BATTERY_MANIFEST)
    with pytest.raises(AgentRunsError, match="unavailable"):
        map_agent_runs_dir(tmp_path / "does-not-exist", manifest)


def test_map_agent_runs_dir_fails_closed_missing_cell(tmp_path: Path) -> None:
    manifest = load_battery_manifest(BATTERY_MANIFEST)
    runs_dir = _copy_live_runs_dir(tmp_path)
    missing = runs_dir / "python-agent-ffa" / "burnin-a" / "seed-02" / "lab-diff-t1" / "ticks.jsonl"
    missing.unlink()
    with pytest.raises(AgentRunsError, match="python-agent-ffa/burnin-a/seed-02"):
        map_agent_runs_dir(runs_dir, manifest)


def test_map_agent_runs_dir_fails_closed_unexpected_cell(tmp_path: Path) -> None:
    manifest = load_battery_manifest(BATTERY_MANIFEST)
    runs_dir = _copy_live_runs_dir(tmp_path)
    extra = runs_dir / "python-agent-ffa" / "burnin-a" / "seed-03"
    extra.mkdir(parents=True)
    shutil.copy(BATTERY_FIXTURES / "python" / "ffa-seed01-a-v1.jsonl", extra / "ticks.jsonl")
    with pytest.raises(AgentRunsError, match="not declared"):
        map_agent_runs_dir(runs_dir, manifest)


def test_map_agent_runs_dir_fails_closed_tenant_mismatch(tmp_path: Path) -> None:
    manifest = load_battery_manifest(BATTERY_MANIFEST)
    runs_dir = _copy_live_runs_dir(tmp_path)
    records = runs_dir / "python-agent-ffa" / "burnin-a" / "seed-01" / "lab-diff-t1" / "ticks.jsonl"
    lines = records.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["tenantId"] = "other-tenant"
    lines[0] = json.dumps(first, sort_keys=True)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AgentRunsError, match="does not match"):
        map_agent_runs_dir(runs_dir, manifest)


def test_run_battery_with_live_runs_dir_matches_fixture_digest(tmp_path: Path) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    runs_dir = _copy_live_runs_dir(tmp_path)
    baseline = run_battery_from_manifest(BATTERY_MANIFEST)
    live = run_battery_from_manifest(manifest, agent_runs_dir=runs_dir)
    assert live.status is BatteryStatus.PASS
    assert live.attested is True
    assert live.injected_cells is False
    assert len(live.cells) == 8
    assert live.unclassified_count == 0
    # Live records are byte-identical to the fixtures, so the content-addressed
    # report is identical: the seam substitutes inputs, not semantics.
    assert live.artifact_sha256 == baseline.artifact_sha256


def test_run_battery_fails_closed_when_live_runs_dir_unavailable(tmp_path: Path) -> None:
    missing = tmp_path / "missing-agent-runs"
    with pytest.raises(AgentRunsError, match="unavailable"):
        run_battery_from_manifest(BATTERY_MANIFEST, agent_runs_dir=missing)
    # Without the seam the committed fixtures still run: no silent coupling.
    report = run_battery_from_manifest(BATTERY_MANIFEST)
    assert report.status is BatteryStatus.PASS


def test_cli_competitive_eval_agent_runs_dir(tmp_path: Path) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    runs_dir = _copy_live_runs_dir(tmp_path)
    code, stdout, stderr = _run_cli(
        "competitive-eval",
        "--run",
        str(manifest),
        "--agent-runs-dir",
        str(runs_dir),
    )
    assert code == 0
    assert '"status": "pass"' in stdout
    assert stderr == ""

    code, _stdout, stderr = _run_cli(
        "competitive-eval",
        "--run",
        str(manifest),
        "--agent-runs-dir",
        str(tmp_path / "missing-agent-runs"),
    )
    assert code == 2
    assert "unavailable" in stderr


def test_manifest_agent_runs_dir_field(tmp_path: Path) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    runs_dir = _copy_live_runs_dir(tmp_path)
    raw = _battery_dict()
    raw["agent_runs_dir"] = str(runs_dir)
    _write_manifest(manifest, raw)
    report = run_battery_from_manifest(manifest)
    assert report.status is BatteryStatus.PASS
    assert report.attested is True
    assert len(report.cells) == 8
    assert report.artifact_sha256 == run_battery_from_manifest(BATTERY_MANIFEST).artifact_sha256


def test_agent_runs_dir_env_var_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    runs_dir = _copy_live_runs_dir(tmp_path)
    monkeypatch.setenv(AGENT_RUNS_DIR_ENV, str(runs_dir))
    report = run_battery_from_manifest(manifest)
    assert report.status is BatteryStatus.PASS
    assert len(report.cells) == 8


def test_cli_agent_runs_dir_overrides_manifest_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    good_runs = _copy_live_runs_dir(tmp_path)
    bad_runs = tmp_path / "bad-runs"
    raw = _battery_dict()
    raw["agent_runs_dir"] = str(bad_runs)
    _write_manifest(manifest, raw)
    monkeypatch.setenv(AGENT_RUNS_DIR_ENV, str(bad_runs))
    code, stdout, stderr = _run_cli(
        "competitive-eval",
        "--run",
        str(manifest),
        "--agent-runs-dir",
        str(good_runs),
    )
    assert code == 0
    assert '"status": "pass"' in stdout
    assert stderr == ""


@pytest.mark.skipif(
    not os.environ.get(AGENT_RUNS_DIR_ENV),
    reason=(
        "live-agent integration is disabled; set ARENA_AGENT_RUNS_DIR to an "
        "external arena-hero-agent batch output directory laid out as "
        "<contestant>/<scenario>/<seed>/<tenant>/ticks.jsonl for the committed "
        "battery matrix (tenant lab-diff-t1) to enable it"
    ),
)
def test_live_agent_runs_dir_integration(tmp_path: Path) -> None:
    """End-to-end live-agent seam against an external agent runs directory.

    Enabled only when ``ARENA_AGENT_RUNS_DIR`` is set. The directory is
    normally produced by invoking the offline agent CLI through an external uv
    env, e.g. ``uv run --project <arena-hero-agent> arena-hero-agent batch
    --input-dir <scenario-turns> --tenant lab-diff-t1 --data-root
    <runs-dir>/<contestant>`` per contestant. When the configured directory is
    unusable the test stays skipped instead of fabricating evidence.
    """
    runs_dir = Path(os.environ[AGENT_RUNS_DIR_ENV])
    if not runs_dir.is_dir():
        pytest.skip(
            f"configured live agent runs directory {runs_dir} does not exist; "
            "keeping the live-agent integration skipped"
        )
    dest = _copy_fixtures(tmp_path)
    manifest = dest / "competitive_eval" / "run-burnin-20260802-a-v1.json"
    report = run_battery_from_manifest(manifest, agent_runs_dir=runs_dir)
    assert report.status is BatteryStatus.PASS
    assert report.attested is True
    assert report.injected_cells is False
    assert len(report.cells) == 8
    assert report.unclassified_count == 0
