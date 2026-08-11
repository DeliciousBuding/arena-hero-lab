"""TS/Python replay differential corpus and classifier (P6-2)."""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from arena_hero_bench.agent_runtime import AgentRuntimeImportError
from arena_hero_bench.cli import main as cli_main
from arena_hero_bench.differential import (
    REPLAY_DIFFERENTIAL_SCHEMA,
    DifferentialDimension,
    DifferentialError,
    DifferentialReport,
    DifferentialStatus,
    PyAgentCanonicalRecord,
    TsLegacyCanonicalRecord,
    build_differential_run,
    canonicalize_ts_legacy_record,
    classify_differential_run,
    load_py_agent_corpus,
    load_ts_legacy_corpus,
    run_differential_from_manifest,
    world_state_digest,
)
from arena_hero_sim.serialization import JsonValue, to_json_value


def _json_dict(value: Mapping[str, object]) -> dict[str, JsonValue]:
    narrowed = to_json_value(value)
    assert isinstance(narrowed, dict)
    return narrowed


def _narrow_dict(value: JsonValue) -> Mapping[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _narrow_list(value: JsonValue) -> list[Mapping[str, JsonValue]]:
    assert isinstance(value, list)
    return [_narrow_dict(item) for item in value]


def _canon(raw: Mapping[str, object]) -> TsLegacyCanonicalRecord:
    return canonicalize_ts_legacy_record(
        raw,
        tick=40437,
        segment_id="unknown-001",
        dataset_id=DATASET,
        config_hash="sha256:" + "a" * 64,
        map_mode="disabled",
        input_sha256="sha256:" + "b" * 64,
    )


FIXTURES = Path(__file__).parent / "fixtures" / "differential"
TS_MANIFEST = FIXTURES / "ts" / "burnin-20260802-a" / "manifest.json"
TS_DATA = FIXTURES / "ts" / "burnin-20260802-a"
PY_RECORDS = FIXTURES / "python" / "agent_run_records_aligned_v1.jsonl"
RUN_MANIFEST = FIXTURES / "run-burnin-20260802-a-v1.json"
DATASET = "burnin-20260802-a"
TENANT = "lab-diff-t1"


def _load() -> tuple[tuple[TsLegacyCanonicalRecord, ...], tuple[PyAgentCanonicalRecord, ...]]:
    ts, _ = load_ts_legacy_corpus(TS_MANIFEST, TS_DATA)
    py, _ = load_py_agent_corpus(PY_RECORDS, tenant_id=TENANT)
    return ts, py


def _run_cli(*argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli_main(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------


def test_ts_corpus_loads_manifest_ordered_ticks() -> None:
    records, meta = load_ts_legacy_corpus(TS_MANIFEST, TS_DATA)
    assert [record.tick for record in records] == list(range(40437, 40445))
    assert meta["dataset_id"] == DATASET
    assert meta["map_mode"] == "disabled"
    assert str(meta["config_hash"]).startswith("sha256:")
    for record in records:
        assert record.input_sha256.startswith("sha256:")
        assert record.segment_id == "unknown-001"


def test_py_corpus_loads_aligned_ticks_and_loop_metrics() -> None:
    records, metrics = load_py_agent_corpus(PY_RECORDS, tenant_id=TENANT)
    assert [record.tick for record in records] == list(range(40437, 40445))
    assert metrics is not None
    assert metrics["last_tick"] == 40444
    assert metrics["ticks_processed"] == 8
    assert metrics["stopped_reason"] == "stream_ended"


def test_ts_manifest_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    manifest = json.loads(TS_MANIFEST.read_text(encoding="utf-8"))
    manifest["inputs"]["40437"]["sha256"] = "sha256:" + "0" * 64
    broken = tmp_path / "manifest.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DifferentialError, match="does not match the fixture file"):
        load_ts_legacy_corpus(broken, TS_DATA)


def test_ts_manifest_unknown_map_mode_fails_closed(tmp_path: Path) -> None:
    manifest = json.loads(TS_MANIFEST.read_text(encoding="utf-8"))
    manifest["map_mode"] = "warp"
    broken = tmp_path / "manifest.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DifferentialError, match="map_mode"):
        load_ts_legacy_corpus(broken, TS_DATA)


# --------------------------------------------------------------------------
# Canonicalization
# --------------------------------------------------------------------------


def test_canonicalize_ts_known_answer_40437() -> None:
    raw = json.loads((TS_DATA / "40437.json").read_text(encoding="utf-8"))
    record = canonicalize_ts_legacy_record(
        raw,
        tick=40437,
        segment_id="unknown-001",
        dataset_id=DATASET,
        config_hash="sha256:" + "a" * 64,
        map_mode="disabled",
        input_sha256="sha256:" + "b" * 64,
    )
    state = record.world_state
    assert state["resources"] == 1
    assert state["population"] == 3
    core = _narrow_dict(state["core"])
    assert core["id"] == "d2d5a983-d24d-4763-a01a-9a658bc35010"
    assert core["state"] == "NORMAL"
    units = _narrow_list(state["units"])
    assert [str(unit["id"]) for unit in units] == [
        "312e4dbf-d356-49ef-b599-691ef3f7c9e8",
        "9c8ee7d0-f25c-420f-9ed7-c4997540a14b",
        "b1db4ce5-08df-485f-a5d6-2a8982621a9c",
    ]
    assert state["enemies"] == []
    beacon = _narrow_dict(state["beacon"])
    assert beacon["position"] == [-17, 77]
    obstacles = state["obstacle_cells"]
    assert isinstance(obstacles, list)
    assert len(obstacles) == 10
    assert state["resource_cells"] == []


def test_canonicalize_ts_maps_uncontrolled_units_to_enemies() -> None:
    raw = json.loads((TS_DATA / "40442.json").read_text(encoding="utf-8"))
    record = canonicalize_ts_legacy_record(
        raw,
        tick=40442,
        segment_id="unknown-001",
        dataset_id=DATASET,
        config_hash="sha256:" + "a" * 64,
        map_mode="disabled",
        input_sha256="sha256:" + "b" * 64,
    )
    state = record.world_state
    assert [str(unit["unit_type"]) for unit in _narrow_list(state["units"])] == ["WORKER"] * 5
    enemies = _narrow_list(state["enemies"])
    assert [str(enemy["unit_type"]) for enemy in enemies] == ["VANGUARD"]
    assert enemies[0]["position"] == [36, 50]


def test_canonicalize_ts_is_deterministic_and_sensitive() -> None:
    raw = json.loads((TS_DATA / "40437.json").read_text(encoding="utf-8"))
    first = _canon(raw)
    second = _canon(raw)
    assert world_state_digest(first.world_state) == world_state_digest(second.world_state)

    altered = dict(raw)
    altered["resources"] = raw["resources"] + 1
    third = _canon(altered)
    assert world_state_digest(third.world_state) != world_state_digest(first.world_state)


def test_canonicalize_ts_rejects_unknown_object_kind() -> None:
    raw = json.loads((TS_DATA / "40437.json").read_text(encoding="utf-8"))
    altered = dict(raw)
    altered["objects"] = [*list(raw["objects"]), {"kind": "TURRET"}]
    with pytest.raises(DifferentialError, match="unsupported kind"):
        canonicalize_ts_legacy_record(
            altered,
            tick=40437,
            segment_id="unknown-001",
            dataset_id=DATASET,
            config_hash="sha256:" + "a" * 64,
            map_mode="disabled",
            input_sha256="sha256:" + "b" * 64,
        )


def test_canonicalize_ts_rejects_missing_core() -> None:
    raw = json.loads((TS_DATA / "40437.json").read_text(encoding="utf-8"))
    altered = dict(raw)
    altered["objects"] = [obj for obj in raw["objects"] if obj["kind"] != "CORE"]
    with pytest.raises(DifferentialError, match="no CORE"):
        canonicalize_ts_legacy_record(
            altered,
            tick=40437,
            segment_id="unknown-001",
            dataset_id=DATASET,
            config_hash="sha256:" + "a" * 64,
            map_mode="disabled",
            input_sha256="sha256:" + "b" * 64,
        )


def test_canonicalize_ts_rejects_boolean_resource_count() -> None:
    raw = json.loads((TS_DATA / "40437.json").read_text(encoding="utf-8"))
    altered = dict(raw)
    altered["resources"] = True
    with pytest.raises(DifferentialError, match="must be an integer"):
        canonicalize_ts_legacy_record(
            altered,
            tick=40437,
            segment_id="unknown-001",
            dataset_id=DATASET,
            config_hash="sha256:" + "a" * 64,
            map_mode="disabled",
            input_sha256="sha256:" + "b" * 64,
        )


def test_canonicalize_py_record_keeps_decision_evidence() -> None:
    _ts, py = _load()
    first = py[0]
    decision_id = first.decision["decision_id"]
    assert isinstance(decision_id, str)
    assert decision_id.startswith("decision:")
    assert first.decision["deadline_outcome"] == "candidate"
    assert first.decision["submit_result"] == "accepted"
    assert first.decision["submit_error"] is None
    assert first.world_state is None


# --------------------------------------------------------------------------
# Classification on the shipped corpus
# --------------------------------------------------------------------------


def _report() -> DifferentialReport:
    return build_differential_run(
        ts_manifest_path=TS_MANIFEST,
        ts_data_dir=TS_DATA,
        py_records_path=PY_RECORDS,
        dataset_id=DATASET,
        tenant_id=TENANT,
    )


def test_corpus_classification_counts_and_unclassified_zero() -> None:
    report = _report()
    assert report.counts[DifferentialStatus.MATCH] == 9  # 8 x tick_identity + record_ordering
    assert report.counts[DifferentialStatus.EXPECTED_UNKNOWN] == 17  # 8+8 world/decision + terminal
    assert report.counts[DifferentialStatus.MISMATCH] == 0
    assert report.counts[DifferentialStatus.INCONCLUSIVE] == 0
    assert report.unclassified_count == 0
    assert len(report.outcomes) == 8 * 3 + 2


def test_missing_evidence_is_never_reported_as_match() -> None:
    report = _report()
    world = [o for o in report.outcomes if o.dimension == DifferentialDimension.WORLD_STATE_DIGEST]
    decision = [
        o for o in report.outcomes if o.dimension == DifferentialDimension.DECISION_ACTION_CANONICAL
    ]
    assert world and all(o.status == DifferentialStatus.EXPECTED_UNKNOWN for o in world)
    assert decision and all(o.status == DifferentialStatus.EXPECTED_UNKNOWN for o in decision)
    assert not any(o.status == DifferentialStatus.MATCH for o in world)
    assert not any(o.status == DifferentialStatus.MATCH for o in decision)


def test_instance_missing_evidence_is_inconclusive_not_match() -> None:
    ts, py = _load()
    # Without the python-side expected-unknown declaration, a missing py world
    # state becomes INCONCLUSIVE rather than MATCH or EXPECTED_UNKNOWN.
    report = classify_differential_run(
        ts_records=ts,
        py_records=py,
        dataset_id=DATASET,
        tenant_id=TENANT,
        expected_unknown={
            DifferentialDimension.DECISION_ACTION_CANONICAL: "ts_legacy",
            DifferentialDimension.TERMINAL_METRICS: "ts_legacy",
        },
    )
    world = [o for o in report.outcomes if o.dimension == DifferentialDimension.WORLD_STATE_DIGEST]
    assert world and all(o.status == DifferentialStatus.INCONCLUSIVE for o in world)


def test_report_artifact_is_deterministic() -> None:
    assert _report().artifact_sha256 == _report().artifact_sha256
    assert _report().artifact["schema_version"] == REPLAY_DIFFERENTIAL_SCHEMA
    assert _report().artifact["unclassified_count"] == 0


def test_reordering_inputs_does_not_change_artifact_hash(tmp_path: Path) -> None:
    baseline = _report().artifact_sha256
    ts, _ = load_ts_legacy_corpus(TS_MANIFEST, TS_DATA)
    py, metrics = load_py_agent_corpus(PY_RECORDS, tenant_id=TENANT)

    # Shuffle the python records (tick order restored internally).
    shuffled = tmp_path / "shuffled.jsonl"
    lines = PY_RECORDS.read_text(encoding="utf-8").splitlines()
    shuffled.write_text("\n".join(lines[:-1][::-1] + lines[-1:]) + "\n", encoding="utf-8")
    py_shuffled, metrics_shuffled = load_py_agent_corpus(shuffled, tenant_id=TENANT)
    reordered = classify_differential_run(
        ts_records=ts,
        py_records=py_shuffled,
        dataset_id=DATASET,
        tenant_id=TENANT,
        py_metrics=metrics_shuffled,
    )
    assert reordered.artifact_sha256 == baseline

    # Reverse the ts side (manifest order is restored internally).
    reversed_ts = classify_differential_run(
        ts_records=tuple(reversed(ts)),
        py_records=py,
        dataset_id=DATASET,
        tenant_id=TENANT,
        py_metrics=metrics,
    )
    assert reversed_ts.artifact_sha256 == baseline


# --------------------------------------------------------------------------
# Reverse validation: real differences must be MISMATCH
# --------------------------------------------------------------------------


def test_tick_identity_difference_is_mismatch() -> None:
    ts, py = _load()
    py_minus = [record for record in py if record.tick != 40440]
    report = classify_differential_run(
        ts_records=ts,
        py_records=py_minus,
        dataset_id=DATASET,
        tenant_id=TENANT,
        py_metrics={"last_tick": 40444, "ticks_processed": 7, "stopped_reason": "stream_ended"},
    )
    mismatches = [o for o in report.outcomes if o.status == DifferentialStatus.MISMATCH]
    assert any(
        o.dimension == DifferentialDimension.TICK_IDENTITY and o.tick == 40440 for o in mismatches
    )
    assert any(o.dimension == DifferentialDimension.RECORD_ORDERING for o in mismatches)


def test_world_state_difference_is_mismatch_when_both_capture_it() -> None:
    ts, py = _load()
    shared = _json_dict(
        {"resources": 1, "population": 3, "units": [{"id": "u1", "position": [1, 2]}]}
    )
    py_with_state = [
        PyAgentCanonicalRecord(
            tick=record.tick,
            tenant_id=record.tenant_id,
            decision=record.decision,
            world_state=shared,
        )
        for record in py
    ]
    report = classify_differential_run(
        ts_records=ts,
        py_records=py_with_state,
        dataset_id=DATASET,
        tenant_id=TENANT,
    )
    world = [o for o in report.outcomes if o.dimension == DifferentialDimension.WORLD_STATE_DIGEST]
    assert world and all(o.status == DifferentialStatus.MISMATCH for o in world)

    # Identical world state on both sides is MATCH.
    py_matching = [
        PyAgentCanonicalRecord(
            tick=record.tick,
            tenant_id=record.tenant_id,
            decision=record.decision,
            world_state=dict(ts_record.world_state),
        )
        for record, ts_record in zip(py, ts, strict=True)
    ]
    report_match = classify_differential_run(
        ts_records=ts,
        py_records=py_matching,
        dataset_id=DATASET,
        tenant_id=TENANT,
    )
    world_match = [
        o for o in report_match.outcomes if o.dimension == DifferentialDimension.WORLD_STATE_DIGEST
    ]
    assert world_match and all(o.status == DifferentialStatus.MATCH for o in world_match)


def test_decision_difference_is_mismatch_when_both_capture_it() -> None:
    ts, _ = load_ts_legacy_corpus(TS_MANIFEST, TS_DATA)
    py, metrics = load_py_agent_corpus(PY_RECORDS, tenant_id=TENANT)
    ts_with_decision = [
        TsLegacyCanonicalRecord(
            tick=record.tick,
            segment_id=record.segment_id,
            dataset_id=record.dataset_id,
            map_mode=record.map_mode,
            config_hash=record.config_hash,
            input_sha256=record.input_sha256,
            world_state=record.world_state,
            decision=dict(py_record.decision),
        )
        for record, py_record in zip(ts, py, strict=True)
    ]
    report = classify_differential_run(
        ts_records=ts_with_decision,
        py_records=py,
        dataset_id=DATASET,
        tenant_id=TENANT,
        py_metrics=metrics,
    )
    decisions = [
        o for o in report.outcomes if o.dimension == DifferentialDimension.DECISION_ACTION_CANONICAL
    ]
    assert decisions and all(o.status == DifferentialStatus.MATCH for o in decisions)

    # Mutate one python decision and the same dimension becomes MISMATCH.
    py_mutated = [
        PyAgentCanonicalRecord(
            tick=record.tick,
            tenant_id=record.tenant_id,
            decision=(
                {**record.decision, "decision_id": "decision:" + "f" * 64}
                if record.tick == 40439
                else record.decision
            ),
        )
        for record in py
    ]
    report_diff = classify_differential_run(
        ts_records=ts_with_decision,
        py_records=py_mutated,
        dataset_id=DATASET,
        tenant_id=TENANT,
        py_metrics=metrics,
    )
    decisions_diff = [
        o
        for o in report_diff.outcomes
        if o.dimension == DifferentialDimension.DECISION_ACTION_CANONICAL
    ]
    assert any(o.status == DifferentialStatus.MISMATCH and o.tick == 40439 for o in decisions_diff)


def test_terminal_metrics_match_and_mismatch() -> None:
    ts, _ = load_ts_legacy_corpus(TS_MANIFEST, TS_DATA)
    py, _ = load_py_agent_corpus(PY_RECORDS, tenant_id=TENANT)
    metrics = {"last_tick": 40444, "ticks_processed": 8, "stopped_reason": "stream_ended"}
    report = classify_differential_run(
        ts_records=ts,
        py_records=py,
        dataset_id=DATASET,
        tenant_id=TENANT,
        ts_metrics=metrics,
        py_metrics=metrics,
    )
    terminal = [o for o in report.outcomes if o.dimension == DifferentialDimension.TERMINAL_METRICS]
    assert terminal and terminal[0].status == DifferentialStatus.MATCH

    report_diff = classify_differential_run(
        ts_records=ts,
        py_records=py,
        dataset_id=DATASET,
        tenant_id=TENANT,
        ts_metrics=metrics,
        py_metrics={**metrics, "ticks_processed": 7},
    )
    terminal_diff = [
        o for o in report_diff.outcomes if o.dimension == DifferentialDimension.TERMINAL_METRICS
    ]
    assert terminal_diff and terminal_diff[0].status == DifferentialStatus.MISMATCH


# --------------------------------------------------------------------------
# Fail-closed behavior
# --------------------------------------------------------------------------


def test_bad_tail_python_records_fail_closed(tmp_path: Path) -> None:
    lines = PY_RECORDS.read_text(encoding="utf-8").splitlines()
    torn = tmp_path / "torn.jsonl"
    torn.write_text("\n".join(lines), encoding="utf-8")  # missing final newline
    with pytest.raises(AgentRuntimeImportError, match="torn tail"):
        load_py_agent_corpus(torn, tenant_id=TENANT)


def test_corrupt_python_record_fails_closed(tmp_path: Path) -> None:
    lines = PY_RECORDS.read_text(encoding="utf-8").splitlines()
    lines[3] = '{"schemaVersion": 1, '
    corrupt = tmp_path / "corrupt.jsonl"
    corrupt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AgentRuntimeImportError, match="corrupt"):
        load_py_agent_corpus(corrupt, tenant_id=TENANT)


def test_cross_tenant_python_records_fail_closed() -> None:
    with pytest.raises(AgentRuntimeImportError, match="tenant"):
        load_py_agent_corpus(PY_RECORDS, tenant_id="other-tenant")


def test_bad_tail_ts_fixture_fails_closed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    manifest = json.loads(TS_MANIFEST.read_text(encoding="utf-8"))
    (data_dir / "40437.json").write_text('{"broken": ', encoding="utf-8")
    for tick in range(40438, 40445):
        (data_dir / f"{tick}.json").write_bytes((TS_DATA / f"{tick}.json").read_bytes())
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DifferentialError, match="40437"):
        load_ts_legacy_corpus(tmp_path / "manifest.json", data_dir)


def test_duplicate_tick_fails_closed() -> None:
    ts, py = _load()
    duplicated = (*ts[:1], *ts)
    with pytest.raises(DifferentialError, match="duplicate tick"):
        classify_differential_run(
            ts_records=duplicated,
            py_records=py,
            dataset_id=DATASET,
            tenant_id=TENANT,
        )


def test_empty_side_fails_closed() -> None:
    ts, _ = load_ts_legacy_corpus(TS_MANIFEST, TS_DATA)
    with pytest.raises(DifferentialError, match="empty"):
        classify_differential_run(
            ts_records=ts,
            py_records=(),
            dataset_id=DATASET,
            tenant_id=TENANT,
        )


def test_unsupported_run_manifest_fails_closed(tmp_path: Path) -> None:
    broken = tmp_path / "run.json"
    broken.write_text(
        json.dumps({"schemaVersion": "arena.bench.not-real.v9", "dataset_id": DATASET}),
        encoding="utf-8",
    )
    with pytest.raises(DifferentialError, match="schemaVersion"):
        run_differential_from_manifest(broken)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_differential_prints_deterministic_report() -> None:
    first = _run_cli("differential", "--run", str(RUN_MANIFEST))
    second = _run_cli("differential", "--run", str(RUN_MANIFEST))
    assert first[0] == 0, first[2]
    assert second[0] == 0, second[2]
    first_data = json.loads(first[1])
    second_data = json.loads(second[1])
    assert first_data["artifact_sha256"] == second_data["artifact_sha256"]
    assert first_data["unclassified_count"] == 0
    assert first_data["counts"]["MISMATCH"] == 0


def test_cli_differential_fails_closed_on_tenant_mismatch(tmp_path: Path) -> None:
    manifest = json.loads(RUN_MANIFEST.read_text(encoding="utf-8"))
    manifest["tenant_id"] = "wrong-tenant"
    manifest["ts_legacy"] = {
        "manifest": str(TS_MANIFEST),
        "data_dir": str(TS_DATA),
    }
    manifest["python_agent"] = {"records": str(PY_RECORDS)}
    broken = tmp_path / "run.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")
    code, stdout, stderr = _run_cli("differential", "--run", str(broken))
    assert code == 2
    assert "tenant" in stderr.lower()
    assert stdout == ""
