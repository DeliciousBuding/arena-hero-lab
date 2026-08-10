from __future__ import annotations

import json
from dataclasses import replace

import pytest

from arena_hero_bench import (
    MeasurementProtocol,
    PerformanceEvidence,
    PublicEnvironment,
    measure_reference_workload,
)
from arena_hero_sim import compare_workload_runs, run_canonical_reference_workload


def _environment() -> PublicEnvironment:
    return PublicEnvironment(
        python_version="3.12.10",
        python_implementation="CPython",
        os_family="Windows",
        architecture="AMD64",
        cpu_count=8,
        dependency_versions={"arena-hero-bench": "0.2.0", "arena-hero-sim": "0.2.0"},
    )


def _clock(*values: int):
    iterator = iter(values)
    return lambda: next(iterator)


def test_measurement_excludes_warmups_and_retains_exact_raw_samples() -> None:
    protocol = MeasurementProtocol(
        warmup_rounds=2,
        measured_rounds=3,
        batch_size=2,
        worker_count=1,
        timeout_seconds=1.0,
    )

    evidence = measure_reference_workload(
        protocol,
        environment=_environment(),
        clock=_clock(100, 2_100, 4_000, 7_000, 9_000, 14_000),
    )

    assert evidence.raw_durations_ns == (2_000, 3_000, 5_000)
    assert evidence.sample_count == 3
    assert evidence.warmup_rounds_completed == 2
    assert evidence.median_ns == 3_000
    assert evidence.p95_ns == 5_000
    assert evidence.p99_ns == 5_000
    assert evidence.publishable
    assert evidence.production_claim is False
    assert len(evidence.observed_run_sha256s) == 3
    assert all(
        round_digests == (evidence.semantic_run_sha256,)
        for round_digests in evidence.observed_run_sha256s
    )


def test_measurement_supports_worker_count_without_semantic_drift() -> None:
    protocol = MeasurementProtocol(
        warmup_rounds=1,
        measured_rounds=2,
        batch_size=1024,
        worker_count=2,
        timeout_seconds=2.0,
    )

    evidence = measure_reference_workload(
        protocol,
        environment=_environment(),
        clock=_clock(0, 10_000, 20_000, 40_000),
    )

    assert evidence.publishable
    assert evidence.raw_durations_ns == (10_000, 20_000)
    assert all(
        round_digests == (evidence.semantic_run_sha256, evidence.semantic_run_sha256)
        for round_digests in evidence.observed_run_sha256s
    )


def test_environment_is_public_and_rejects_host_like_metadata() -> None:
    environment = _environment()
    serialized = json.dumps(environment.to_dict(), sort_keys=True).casefold()

    assert "hostname" not in serialized
    assert "username" not in serialized
    assert "cwd" not in serialized
    assert ":\\" not in serialized
    assert "/users/" not in serialized

    with pytest.raises(ValueError, match="host identity"):
        PublicEnvironment(
            python_version="3.12.10",
            python_implementation="CPython",
            os_family="Windows",
            architecture="AMD64",
            cpu_count=8,
            dependency_versions={"hostname": "build-node"},
        )
    with pytest.raises(ValueError, match="paths"):
        PublicEnvironment(
            python_version="3.12.10",
            python_implementation="CPython",
            os_family="Windows",
            architecture="AMD64",
            cpu_count=8,
            dependency_versions={"arena-hero-sim": "C:\\workspace\\sim"},
        )


def test_short_non_positive_and_failed_differential_are_not_publishable() -> None:
    protocol = MeasurementProtocol(
        warmup_rounds=0,
        measured_rounds=2,
        batch_size=1,
        worker_count=1,
        timeout_seconds=1.0,
    )
    short = measure_reference_workload(
        protocol,
        environment=_environment(),
        clock=_clock(10, 10, 20, 21),
    )
    assert not short.publishable
    assert short.raw_durations_ns == (0, 1)
    assert short.median_ns is None
    assert short.p95_ns is None
    assert short.p99_ns is None
    assert any("not positive" in issue for issue in short.issues)
    assert any("credibility floor" in issue for issue in short.issues)

    reference = run_canonical_reference_workload()
    tampered_episode = replace(reference.episodes[0], seed=reference.episodes[0].seed + 1)
    candidate = replace(reference, episodes=(tampered_episode, *reference.episodes[1:]))
    failed_gate = compare_workload_runs(reference, candidate)
    failed = measure_reference_workload(
        replace(protocol, measured_rounds=1),
        environment=_environment(),
        differential_report=failed_gate,
        clock=_clock(100, 2_100),
    )
    assert not failed.publishable
    assert any("differential gate" in issue for issue in failed.issues)


def test_semantic_run_digest_drift_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from arena_hero_bench import performance

    original = performance.ReferenceWorkloadRunner.run
    calls = 0

    def drifting_run(self, manifest, *, batch_size=1):
        nonlocal calls
        calls += 1
        run = original(self, manifest, batch_size=batch_size)
        if calls >= 2:
            episode = replace(run.episodes[0], seed=run.episodes[0].seed + calls)
            return replace(run, episodes=(episode, *run.episodes[1:]))
        return run

    monkeypatch.setattr(performance.ReferenceWorkloadRunner, "run", drifting_run)
    evidence = measure_reference_workload(
        MeasurementProtocol(
            warmup_rounds=0,
            measured_rounds=1,
            batch_size=1,
            worker_count=1,
            timeout_seconds=1.0,
        ),
        environment=_environment(),
        clock=_clock(100, 2_100),
    )

    assert not evidence.publishable
    assert any("digest drifted" in issue for issue in evidence.issues)


def test_protocol_and_evidence_are_content_addressed_and_production_claim_is_fixed() -> None:
    protocol = MeasurementProtocol(
        warmup_rounds=0,
        measured_rounds=1,
        batch_size=1,
        worker_count=1,
        timeout_seconds=1.0,
    )
    evidence = measure_reference_workload(
        protocol,
        environment=_environment(),
        clock=_clock(100, 2_100),
    )

    assert len(protocol.sha256) == 64
    assert len(evidence.sha256) == 64
    assert evidence.sha256 == evidence.sha256
    assert evidence.to_dict()["production_claim"] is False
    with pytest.raises(ValueError, match="production_claim=false"):
        replace(evidence, production_claim=True)
    with pytest.raises(ValueError, match="every measured raw sample"):
        PerformanceEvidence(
            protocol=protocol,
            environment=_environment(),
            workload_sha256=evidence.workload_sha256,
            backend=evidence.backend,
            semantic_run_sha256=evidence.semantic_run_sha256,
            differential_report_sha256=evidence.differential_report_sha256,
            raw_durations_ns=(),
            observed_run_sha256s=(),
            warmup_rounds_completed=0,
            median_ns=None,
            p95_ns=None,
            p99_ns=None,
            publishable=True,
        )
