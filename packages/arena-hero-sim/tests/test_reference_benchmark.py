"""Reference-workload benchmark harness behavior and CLI contract."""

from __future__ import annotations

import json
import socket
import statistics
from pathlib import Path
from typing import cast

import pytest

from arena_hero_sim import (
    CANONICAL_REFERENCE_WORKLOAD_ID,
    CANONICAL_REFERENCE_WORKLOAD_SHA256,
    CANONICAL_REFERENCE_WORKLOAD_VERSION,
    REFERENCE_ENGINE_VERSION,
    REFERENCE_WORKLOAD_BENCHMARK_SCHEMA,
    ReferenceWorkloadBenchmarkReport,
)
from arena_hero_sim.microbenchmark import (
    main,
    run_reference_workload_benchmark,
)

_CANONICAL_RUN_SHA256 = "c143ccfc73ce6c784e6abfadda237cda33af63854929ec27d8bc50c41763045e"


def test_report_shape_and_frozen_identity() -> None:
    report = run_reference_workload_benchmark(batch_size=1, repeats=2)

    assert report.schema_version == REFERENCE_WORKLOAD_BENCHMARK_SCHEMA
    assert report.schema_version == "arena.sim.reference-workload-benchmark.v1"
    assert report.benchmark == "reference-workload"
    assert report.workload_id == CANONICAL_REFERENCE_WORKLOAD_ID
    assert report.workload_version == CANONICAL_REFERENCE_WORKLOAD_VERSION
    assert report.workload_sha256 == CANONICAL_REFERENCE_WORKLOAD_SHA256
    assert report.run_sha256 == _CANONICAL_RUN_SHA256
    assert report.backend_id == "reference-engine"
    assert report.engine_version == REFERENCE_ENGINE_VERSION
    assert report.protocol_version == "arena.sim.v1"
    assert report.batch_size == 1
    assert report.repeats == 2
    assert report.episodes == 9
    assert report.ticks == 13
    assert len(report.durations_ns) == 2
    assert report.median_ns == int(statistics.median(report.durations_ns))
    assert report.p95_ns == max(report.durations_ns)
    assert report.production_claim is False


def test_run_digest_is_stable_across_batch_sizes() -> None:
    first = run_reference_workload_benchmark(batch_size=1, repeats=1)
    second = run_reference_workload_benchmark(batch_size=9, repeats=1)

    assert first.workload_sha256 == second.workload_sha256
    assert first.run_sha256 == second.run_sha256 == _CANONICAL_RUN_SHA256
    assert first.episodes == second.episodes == 9
    assert first.ticks == second.ticks == 13


def test_raw_samples_and_summary_match_injected_timer() -> None:
    readings = iter((0, 10_000_000, 0, 20_000_000, 0, 30_000_000))

    def clock() -> int:
        return next(readings)

    report = run_reference_workload_benchmark(batch_size=1, repeats=3, timer=clock)

    assert report.durations_ns == (10_000_000, 20_000_000, 30_000_000)
    assert report.median_ns == 20_000_000
    assert report.p95_ns == 30_000_000


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_invalid_batch_size_fails_closed(batch_size: object) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        run_reference_workload_benchmark(batch_size=cast(int, batch_size), repeats=1)


@pytest.mark.parametrize("repeats", [0, -1, True, 1.5])
def test_invalid_repeats_fails_closed(repeats: object) -> None:
    with pytest.raises(ValueError, match="repeats"):
        run_reference_workload_benchmark(batch_size=1, repeats=cast(int, repeats))


def test_bool_timer_reading_fails_closed() -> None:
    def clock() -> int:
        return cast(int, True)

    with pytest.raises(ValueError, match="integer"):
        run_reference_workload_benchmark(batch_size=1, repeats=1, timer=clock)


def test_float_timer_reading_fails_closed() -> None:
    def clock() -> int:
        return cast(int, 1.5)

    with pytest.raises(ValueError, match="integer"):
        run_reference_workload_benchmark(batch_size=1, repeats=1, timer=clock)


def test_backwards_timer_reading_fails_closed() -> None:
    readings = iter((100, 50))

    def clock() -> int:
        return next(readings)

    with pytest.raises(ValueError, match="backwards"):
        run_reference_workload_benchmark(batch_size=1, repeats=1, timer=clock)


def test_production_claim_is_always_false() -> None:
    report = run_reference_workload_benchmark(batch_size=1, repeats=1)
    assert report.production_claim is False


def test_report_rejects_explicit_production_claim() -> None:
    with pytest.raises(ValueError, match="production"):
        ReferenceWorkloadBenchmarkReport(
            schema_version="arena.sim.reference-workload-benchmark.v1",
            benchmark="reference-workload",
            workload_id="workload",
            workload_version="v1",
            workload_sha256="0" * 64,
            run_sha256="1" * 64,
            backend_id="backend",
            engine_version="engine",
            protocol_version="protocol",
            batch_size=1,
            repeats=1,
            episodes=1,
            ticks=1,
            durations_ns=(1,),
            median_ns=1,
            p95_ns=1,
            python_version="3.12",
            platform="Linux-x86_64",
            production_claim=True,
        )


def test_report_json_is_deterministic_without_host_identity() -> None:
    report = run_reference_workload_benchmark(batch_size=1, repeats=1)
    payload = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)

    assert payload == json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
    assert json.loads(payload) == report.to_dict()
    assert socket.gethostname() not in payload
    assert str(Path.home()) not in payload
    assert str(Path.cwd()) not in payload
    assert "C" + ":\\" not in payload


def test_cli_default_is_contract_dispatch(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "contract.json"
    assert (
        main(
            [
                "--episodes",
                "20",
                "--repeats",
                "2",
                "--batch-size",
                "8",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "arena.sim.microbenchmark.v1"
    assert payload["benchmark"] == "contract-validation-and-placeholder-batch-dispatch"
    assert payload["production_claim"] is False


def test_cli_reference_workload_selector_roundtrip(tmp_path: Path) -> None:
    output = tmp_path / "reference.json"
    assert (
        main(
            [
                "--benchmark",
                "reference-workload",
                "--repeats",
                "1",
                "--batch-size",
                "1",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "arena.sim.reference-workload-benchmark.v1"
    assert payload["benchmark"] == "reference-workload"
    assert payload["workload_sha256"] == CANONICAL_REFERENCE_WORKLOAD_SHA256
    assert payload["run_sha256"] == _CANONICAL_RUN_SHA256
    assert payload["backend_id"] == "reference-engine"
    assert payload["episodes"] == 9
    assert payload["ticks"] == 13
    assert payload["production_claim"] is False
    assert len(payload["durations_ns"]) == 1


def test_cli_identity_fields_are_deterministic_across_runs(tmp_path: Path) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    args = ["--benchmark", "reference-workload", "--repeats", "1", "--batch-size", "1"]
    assert main([*args, "--output", str(first_path)]) == 0
    assert main([*args, "--output", str(second_path)]) == 0
    first = json.loads(first_path.read_text(encoding="utf-8"))
    second = json.loads(second_path.read_text(encoding="utf-8"))
    identity_fields = (
        "schema_version",
        "benchmark",
        "workload_id",
        "workload_version",
        "workload_sha256",
        "run_sha256",
        "backend_id",
        "engine_version",
        "protocol_version",
        "batch_size",
        "repeats",
        "episodes",
        "ticks",
        "python_version",
        "platform",
    )
    for key in identity_fields:
        assert first[key] == second[key]


def test_cli_rejects_episodes_for_reference_workload() -> None:
    with pytest.raises(SystemExit):
        main(["--benchmark", "reference-workload", "--episodes", "5"])


def test_cli_writes_only_caller_path_when_output_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out.json"
    assert (
        main(
            [
                "--benchmark",
                "reference-workload",
                "--repeats",
                "1",
                "--batch-size",
                "1",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert output.is_file()
