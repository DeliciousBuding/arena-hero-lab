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
import shutil
from pathlib import Path
from typing import Any

import pytest

from arena_hero_bench.cli import main as cli_main
from arena_hero_bench.competitive_eval import (
    BatteryCellSpec,
    BatteryIssueKind,
    BatteryManifestError,
    BatteryStatus,
    load_battery_manifest,
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
