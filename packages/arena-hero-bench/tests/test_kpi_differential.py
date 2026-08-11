"""Evolve/Python Agent multi-dimensional KPI differential (P6-3)."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from arena_hero_bench.cli import main as cli_main
from arena_hero_bench.differential import (
    DifferentialError,
    DifferentialStatus,
    PyAgentCanonicalRecord,
    TsLegacyCanonicalRecord,
    load_py_agent_corpus,
    load_ts_legacy_corpus,
)
from arena_hero_bench.kpi_differential import (
    DEFAULT_KPI_EXPECTED_UNKNOWN,
    KPI_DIFFERENTIAL_SCHEMA,
    EvolveDecisionRecord,
    KpiDimension,
    KpiDimensionResult,
    KpiReport,
    PyObservationRecord,
    classify_kpi_differential,
    load_evolve_decision_trace,
    load_py_observation_snapshots,
    run_kpi_differential_from_manifest,
)
from arena_hero_sim.serialization import JsonValue

FIXTURES = Path(__file__).parent / "fixtures" / "kpi_differential"
RUN_MANIFEST = FIXTURES / "run-burnin-20260802-a-v1.json"
EVOLVE_DECISION_TRACE = FIXTURES / "evolve" / "decision-trace-v1.jsonl"
PY_SNAPSHOTS = FIXTURES / "python" / "observation-snapshots-v1.jsonl"
TS_MANIFEST = FIXTURES.parent / "differential" / "ts" / "burnin-20260802-a" / "manifest.json"
TS_DATA = FIXTURES.parent / "differential" / "ts" / "burnin-20260802-a"
PY_RECORDS = FIXTURES.parent / "differential" / "python" / "agent_run_records_aligned_v1.jsonl"
DATASET = "burnin-20260802-a"
TENANT = "lab-diff-t1"


def _load_sides() -> tuple[
    tuple[TsLegacyCanonicalRecord, ...],
    tuple[PyAgentCanonicalRecord, ...],
    Mapping[str, JsonValue] | None,
    tuple[EvolveDecisionRecord, ...],
    tuple[PyObservationRecord, ...],
]:
    evolve, _ = load_ts_legacy_corpus(TS_MANIFEST, TS_DATA)
    py, metrics = load_py_agent_corpus(PY_RECORDS, tenant_id=TENANT)
    decisions = load_evolve_decision_trace(EVOLVE_DECISION_TRACE, tenant_id=TENANT)
    snapshots = load_py_observation_snapshots(PY_SNAPSHOTS, tenant_id=TENANT)
    return evolve, py, metrics, decisions, snapshots


def _classify(
    *,
    evolve: tuple[TsLegacyCanonicalRecord, ...],
    py: tuple[PyAgentCanonicalRecord, ...],
    metrics: Mapping[str, JsonValue] | None,
    decisions: tuple[EvolveDecisionRecord, ...] = (),
    snapshots: tuple[PyObservationRecord, ...] = (),
    expected_unknown: dict[KpiDimension, str] | None = None,
) -> KpiReport:
    return classify_kpi_differential(
        evolve_records=evolve,
        py_records=py,
        dataset_id=DATASET,
        tenant_id=TENANT,
        evolve_decision_trace=decisions,
        py_observation_snapshots=snapshots,
        py_loop_metrics=metrics,
        expected_unknown=expected_unknown
        if expected_unknown is not None
        else DEFAULT_KPI_EXPECTED_UNKNOWN,
    )


def _by_dimension(report: KpiReport) -> dict[KpiDimension, KpiDimensionResult]:
    return {result.dimension: result for result in report.dimensions}


def _narrow_dict(value: JsonValue | None) -> Mapping[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _stat_of(evidence: JsonValue | None) -> Mapping[str, JsonValue]:
    return _narrow_dict(_narrow_dict(evidence).get("statistic"))


def _run_cli(*argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli_main(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


# ---------------------------------------------------------------------------
# Companion fixture loading
# ---------------------------------------------------------------------------


def test_decision_trace_loads_ordered_records() -> None:
    records = load_evolve_decision_trace(EVOLVE_DECISION_TRACE, tenant_id=TENANT)
    assert [record.tick for record in records] == list(range(40437, 40445))
    assert records[2].deadline_outcome == "soft_deadline"
    assert records[3].submit_result == "rejected"
    assert records[3].submit_error == "local submit refused"


def test_observation_snapshots_load_ordered_records() -> None:
    records = load_py_observation_snapshots(PY_SNAPSHOTS, tenant_id=TENANT)
    assert [record.tick for record in records] == list(range(40437, 40445))
    assert records[0].resources == 1
    assert records[2].population == 5
    assert records[3].enemy_count == 1
    assert records[0].core_state == "NORMAL"


def test_companion_torn_tail_fails_closed(tmp_path: Path) -> None:
    lines = EVOLVE_DECISION_TRACE.read_text(encoding="utf-8").splitlines()
    torn = tmp_path / "torn.jsonl"
    torn.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(DifferentialError, match="torn tail"):
        load_evolve_decision_trace(torn, tenant_id=TENANT)

    snapshot_lines = PY_SNAPSHOTS.read_text(encoding="utf-8").splitlines()
    torn_snapshot = tmp_path / "torn-snapshot.jsonl"
    torn_snapshot.write_text("\n".join(snapshot_lines), encoding="utf-8")
    with pytest.raises(DifferentialError, match="torn tail"):
        load_py_observation_snapshots(torn_snapshot, tenant_id=TENANT)


def test_companion_corrupt_record_fails_closed(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.jsonl"
    corrupt.write_text(
        '{"schemaVersion":1,"recordType":"decision","tick":1}\nnot-json\n', encoding="utf-8"
    )
    with pytest.raises(DifferentialError, match="corrupt"):
        load_evolve_decision_trace(corrupt, tenant_id=TENANT)


def test_companion_unknown_record_type_fails_closed(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.jsonl"
    unknown.write_text(
        '{"schemaVersion":1,"recordType":"bogus","tenantId":"lab-diff-t1","tick":1}\n',
        encoding="utf-8",
    )
    with pytest.raises(DifferentialError, match="recordType"):
        load_py_observation_snapshots(unknown, tenant_id=TENANT)


def test_companion_cross_tenant_fails_closed(tmp_path: Path) -> None:
    lines = PY_SNAPSHOTS.read_text(encoding="utf-8").splitlines()
    wrong = tmp_path / "wrong-tenant.jsonl"
    wrong.write_text(lines[0].replace("lab-diff-t1", "other-tenant") + "\n", encoding="utf-8")
    with pytest.raises(DifferentialError, match="tenantId"):
        load_py_observation_snapshots(wrong, tenant_id=TENANT)


def test_companion_duplicate_tick_fails_closed(tmp_path: Path) -> None:
    lines = EVOLVE_DECISION_TRACE.read_text(encoding="utf-8").splitlines()
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text("\n".join([lines[0], lines[0]]) + "\n", encoding="utf-8")
    with pytest.raises(DifferentialError, match="duplicate tick"):
        load_evolve_decision_trace(duplicate, tenant_id=TENANT)


# ---------------------------------------------------------------------------
# Full corpus classification and content addressing
# ---------------------------------------------------------------------------


def test_full_corpus_classification_counts_and_unclassified_zero() -> None:
    report = run_kpi_differential_from_manifest(RUN_MANIFEST)
    assert report.counts[DifferentialStatus.MATCH] == 6
    assert report.counts[DifferentialStatus.MISMATCH] == 0
    assert report.counts[DifferentialStatus.EXPECTED_UNKNOWN] == 0
    assert report.counts[DifferentialStatus.INCONCLUSIVE] == 0
    assert report.unclassified_count == 0
    assert len(report.dimensions) == len(list(KpiDimension))
    assert set(result.dimension for result in report.dimensions) == set(KpiDimension)


def test_report_artifact_is_deterministic() -> None:
    first = run_kpi_differential_from_manifest(RUN_MANIFEST)
    second = run_kpi_differential_from_manifest(RUN_MANIFEST)
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.artifact["schema_version"] == KPI_DIFFERENTIAL_SCHEMA
    assert first.artifact["evidence_kind"] == "sanitized_fixture"
    assert first.artifact["unclassified_count"] == 0


def test_reordering_inputs_does_not_change_artifact_hash(tmp_path: Path) -> None:
    baseline = run_kpi_differential_from_manifest(RUN_MANIFEST).artifact_sha256
    evolve, _, _, decisions, snapshots = _load_sides()

    shuffled = tmp_path / "shuffled.jsonl"
    lines = PY_RECORDS.read_text(encoding="utf-8").splitlines()
    shuffled.write_text("\n".join(lines[:-1][::-1] + lines[-1:]) + "\n", encoding="utf-8")
    py_shuffled, metrics_shuffled = load_py_agent_corpus(shuffled, tenant_id=TENANT)
    reordered = _classify(
        evolve=tuple(reversed(evolve)),
        py=py_shuffled,
        metrics=metrics_shuffled,
        decisions=tuple(reversed(decisions)),
        snapshots=tuple(reversed(snapshots)),
    )
    assert reordered.artifact_sha256 == baseline


def test_statistics_are_reported_per_dimension() -> None:
    report = run_kpi_differential_from_manifest(RUN_MANIFEST)
    by_dim = _by_dimension(report)

    resource = by_dim[KpiDimension.RESOURCE_GROWTH]
    assert resource.status == DifferentialStatus.MATCH
    assert resource.evolve is not None
    assert resource.python_agent is not None
    evolve_stat = _stat_of(resource.evolve)
    assert evolve_stat["initial"] == 1
    assert evolve_stat["final"] == 1
    assert evolve_stat["growth"] == 0
    assert evolve_stat["samples"] == 8

    decisions = by_dim[KpiDimension.DECISION_DISTRIBUTION]
    assert decisions.status == DifferentialStatus.MATCH
    py_stat = _stat_of(decisions.python_agent)
    assert py_stat["deadline_outcome_counts"] == {
        "candidate": 6,
        "soft_deadline": 1,
        "selection_timeout": 1,
    }
    assert py_stat["submit_result_counts"] == {"accepted": 5, "rejected": 1, "not_submitted": 2}

    terminal = by_dim[KpiDimension.SURVIVAL_TERMINAL]
    assert terminal.status == DifferentialStatus.MATCH
    py_terminal_stat = _stat_of(terminal.python_agent)
    assert py_terminal_stat["last_tick"] == 40444
    assert py_terminal_stat["core_state"] == "NORMAL"
    assert py_terminal_stat["loop"] == {
        "last_tick": 40444,
        "ticks_processed": 8,
        "stopped_reason": "stream_ended",
        "outcome_count": 8,
    }

    alignment = by_dim[KpiDimension.TICK_ALIGNMENT]
    assert alignment.status == DifferentialStatus.MATCH
    assert _stat_of(alignment.evolve)["tick_count"] == 8
    assert _stat_of(alignment.python_agent)["last_tick"] == 40444


# ---------------------------------------------------------------------------
# Missing evidence must never be reported as MATCH
# ---------------------------------------------------------------------------


def test_contract_gap_is_expected_unknown_not_match() -> None:
    evolve, py, metrics, _, _ = _load_sides()
    report = _classify(evolve=evolve, py=py, metrics=metrics)
    by_dim = _by_dimension(report)

    assert by_dim[KpiDimension.TICK_ALIGNMENT].status == DifferentialStatus.MATCH
    for dimension in (
        KpiDimension.RESOURCE_GROWTH,
        KpiDimension.COLLECTION_DELIVERY,
        KpiDimension.POPULATION_FORCES,
        KpiDimension.SURVIVAL_TERMINAL,
        KpiDimension.DECISION_DISTRIBUTION,
    ):
        result = by_dim[dimension]
        assert result.status == DifferentialStatus.EXPECTED_UNKNOWN, dimension
        assert result.status is not DifferentialStatus.MATCH, dimension
        assert result.python_agent is None or result.evolve is None


def test_undeclared_missing_evidence_is_inconclusive_not_match() -> None:
    evolve, py, metrics, _, _ = _load_sides()
    report = _classify(evolve=evolve, py=py, metrics=metrics, expected_unknown={})
    by_dim = _by_dimension(report)

    assert by_dim[KpiDimension.TICK_ALIGNMENT].status == DifferentialStatus.MATCH
    for dimension in (
        KpiDimension.RESOURCE_GROWTH,
        KpiDimension.COLLECTION_DELIVERY,
        KpiDimension.POPULATION_FORCES,
        KpiDimension.SURVIVAL_TERMINAL,
        KpiDimension.DECISION_DISTRIBUTION,
    ):
        result = by_dim[dimension]
        assert result.status == DifferentialStatus.INCONCLUSIVE, dimension
        assert result.status is not DifferentialStatus.MATCH, dimension


def test_partial_companion_evidence_is_inconclusive_not_match() -> None:
    evolve, py, metrics, _, snapshots = _load_sides()
    report = _classify(
        evolve=evolve, py=py, metrics=metrics, snapshots=snapshots, expected_unknown={}
    )
    by_dim = _by_dimension(report)

    # Snapshots upgrade world-state dimensions; the evolve decision trace is
    # absent so decision distribution remains inconclusive (undeclared gap).
    assert by_dim[KpiDimension.RESOURCE_GROWTH].status == DifferentialStatus.MATCH
    assert by_dim[KpiDimension.DECISION_DISTRIBUTION].status == DifferentialStatus.INCONCLUSIVE


def test_companion_evidence_upgrades_declared_contract_gap_to_match() -> None:
    evolve, py, metrics, decisions, snapshots = _load_sides()
    report = _classify(
        evolve=evolve, py=py, metrics=metrics, decisions=decisions, snapshots=snapshots
    )
    by_dim = _by_dimension(report)
    for dimension in KpiDimension:
        assert by_dim[dimension].status == DifferentialStatus.MATCH, dimension


# ---------------------------------------------------------------------------
# Reverse validation: real dimension differences must be MISMATCH
# ---------------------------------------------------------------------------


def test_resource_growth_difference_is_mismatch() -> None:
    evolve, py, metrics, decisions, snapshots = _load_sides()
    mutated = tuple(
        dataclasses.replace(record, resources=record.resources + 1)
        if record.tick == 40440
        else record
        for record in snapshots
    )
    report = _classify(
        evolve=evolve, py=py, metrics=metrics, decisions=decisions, snapshots=mutated
    )
    by_dim = _by_dimension(report)
    # The resources field feeds both resource growth and the delivery balance
    # inside collection delivery, so both dimensions legitimately flip.
    assert by_dim[KpiDimension.RESOURCE_GROWTH].status == DifferentialStatus.MISMATCH
    assert by_dim[KpiDimension.COLLECTION_DELIVERY].status == DifferentialStatus.MISMATCH
    for dimension in (
        KpiDimension.POPULATION_FORCES,
        KpiDimension.SURVIVAL_TERMINAL,
        KpiDimension.TICK_ALIGNMENT,
        KpiDimension.DECISION_DISTRIBUTION,
    ):
        assert by_dim[dimension].status == DifferentialStatus.MATCH, dimension


def test_collection_delivery_difference_is_mismatch() -> None:
    evolve, py, metrics, decisions, snapshots = _load_sides()
    mutated = tuple(
        dataclasses.replace(record, cargo_total=record.cargo_total + 1)
        if record.tick == 40440
        else record
        for record in snapshots
    )
    report = _classify(
        evolve=evolve, py=py, metrics=metrics, decisions=decisions, snapshots=mutated
    )
    by_dim = _by_dimension(report)
    assert by_dim[KpiDimension.COLLECTION_DELIVERY].status == DifferentialStatus.MISMATCH
    assert by_dim[KpiDimension.RESOURCE_GROWTH].status == DifferentialStatus.MATCH


def test_population_forces_difference_is_mismatch() -> None:
    evolve, py, metrics, decisions, snapshots = _load_sides()
    mutated = tuple(
        dataclasses.replace(record, enemy_count=record.enemy_count + 1)
        if record.tick == 40441
        else record
        for record in snapshots
    )
    report = _classify(
        evolve=evolve, py=py, metrics=metrics, decisions=decisions, snapshots=mutated
    )
    by_dim = _by_dimension(report)
    assert by_dim[KpiDimension.POPULATION_FORCES].status == DifferentialStatus.MISMATCH
    assert by_dim[KpiDimension.SURVIVAL_TERMINAL].status == DifferentialStatus.MATCH


def test_survival_terminal_difference_is_mismatch() -> None:
    evolve, py, metrics, decisions, snapshots = _load_sides()
    mutated = tuple(
        dataclasses.replace(record, core_hp=record.core_hp - 1) if record.tick == 40444 else record
        for record in snapshots
    )
    report = _classify(
        evolve=evolve, py=py, metrics=metrics, decisions=decisions, snapshots=mutated
    )
    by_dim = _by_dimension(report)
    assert by_dim[KpiDimension.SURVIVAL_TERMINAL].status == DifferentialStatus.MISMATCH
    assert by_dim[KpiDimension.POPULATION_FORCES].status == DifferentialStatus.MATCH


def test_decision_distribution_difference_is_mismatch() -> None:
    evolve, py, metrics, decisions, snapshots = _load_sides()
    mutated = tuple(
        dataclasses.replace(record, submit_result="rejected") if record.tick == 40439 else record
        for record in decisions
    )
    report = _classify(
        evolve=evolve, py=py, metrics=metrics, decisions=mutated, snapshots=snapshots
    )
    by_dim = _by_dimension(report)
    assert by_dim[KpiDimension.DECISION_DISTRIBUTION].status == DifferentialStatus.MISMATCH
    assert by_dim[KpiDimension.TICK_ALIGNMENT].status == DifferentialStatus.MATCH


def test_tick_alignment_difference_is_mismatch() -> None:
    evolve, py, metrics, decisions, snapshots = _load_sides()
    py_minus = tuple(record for record in py if record.tick != 40440)
    snapshots_minus = tuple(record for record in snapshots if record.tick != 40440)
    report = _classify(
        evolve=evolve, py=py_minus, metrics=metrics, decisions=decisions, snapshots=snapshots_minus
    )
    by_dim = _by_dimension(report)
    assert by_dim[KpiDimension.TICK_ALIGNMENT].status == DifferentialStatus.MISMATCH
    assert by_dim[KpiDimension.RESOURCE_GROWTH].status == DifferentialStatus.MISMATCH


# ---------------------------------------------------------------------------
# Fail-closed behavior
# ---------------------------------------------------------------------------


def test_snapshot_tick_set_mismatch_fails_closed(tmp_path: Path) -> None:
    lines = PY_SNAPSHOTS.read_text(encoding="utf-8").splitlines()
    shifted = tmp_path / "shifted.jsonl"
    shifted.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
    evolve, py, metrics, decisions, _ = _load_sides()
    with pytest.raises(DifferentialError, match="tick set does not match"):
        _classify(
            evolve=evolve,
            py=py,
            metrics=metrics,
            decisions=decisions,
            snapshots=load_py_observation_snapshots(shifted, tenant_id=TENANT),
        )


def test_decision_trace_tick_set_mismatch_fails_closed(tmp_path: Path) -> None:
    lines = EVOLVE_DECISION_TRACE.read_text(encoding="utf-8").splitlines()
    shifted = tmp_path / "shifted.jsonl"
    shifted.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
    evolve, py, metrics, _, snapshots = _load_sides()
    with pytest.raises(DifferentialError, match="tick set does not match"):
        _classify(
            evolve=evolve,
            py=py,
            metrics=metrics,
            decisions=load_evolve_decision_trace(shifted, tenant_id=TENANT),
            snapshots=snapshots,
        )


def test_empty_side_fails_closed() -> None:
    evolve, _, _, _, _ = _load_sides()
    with pytest.raises(DifferentialError, match="python-agent replay side is empty"):
        _classify(evolve=evolve, py=(), metrics=None)


def test_unsupported_evidence_kind_fails_closed() -> None:
    evolve, py, metrics, decisions, snapshots = _load_sides()
    with pytest.raises(DifferentialError, match="evidence_kind"):
        classify_kpi_differential(
            evolve_records=evolve,
            py_records=py,
            dataset_id=DATASET,
            tenant_id=TENANT,
            evolve_decision_trace=decisions,
            py_observation_snapshots=snapshots,
            py_loop_metrics=metrics,
            evidence_kind="unsanitized_guess",
        )


def test_unsupported_run_manifest_fails_closed(tmp_path: Path) -> None:
    manifest = json.loads(RUN_MANIFEST.read_text(encoding="utf-8"))
    manifest["schemaVersion"] = "arena.bench.kpi-differential.v999"
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DifferentialError, match="schemaVersion"):
        run_kpi_differential_from_manifest(broken)


def test_unknown_dimension_in_expected_unknown_fails_closed() -> None:
    evolve, py, metrics, _, _ = _load_sides()
    with pytest.raises(DifferentialError, match="expected_unknown"):
        classify_kpi_differential(
            evolve_records=evolve,
            py_records=py,
            dataset_id=DATASET,
            tenant_id=TENANT,
            py_loop_metrics=metrics,
            expected_unknown={KpiDimension.TICK_ALIGNMENT: "bogus"},
        )


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_kpi_differential_prints_deterministic_report() -> None:
    code, stdout, stderr = _run_cli("kpi-differential", "--run", str(RUN_MANIFEST))
    assert code == 0, stderr
    payload = json.loads(stdout)
    assert payload["schema_version"] == KPI_DIFFERENTIAL_SCHEMA
    assert payload["counts"] == {
        "MATCH": 6,
        "MISMATCH": 0,
        "EXPECTED_UNKNOWN": 0,
        "INCONCLUSIVE": 0,
    }
    assert payload["unclassified_count"] == 0
    _, second_stdout, _ = _run_cli("kpi-differential", "--run", str(RUN_MANIFEST))
    assert stdout == second_stdout


def test_cli_kpi_differential_fails_closed_on_tenant_mismatch(tmp_path: Path) -> None:
    import shutil

    fixtures_copy = tmp_path / "fixtures"
    shutil.copytree(FIXTURES.parent, fixtures_copy)
    manifest_path = fixtures_copy / "kpi_differential" / RUN_MANIFEST.name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tenant_id"] = "other-tenant"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    code, _, stderr = _run_cli("kpi-differential", "--run", str(manifest_path))
    assert code == 2
    assert "tenantId" in stderr
