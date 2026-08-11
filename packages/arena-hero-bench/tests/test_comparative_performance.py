from __future__ import annotations

from dataclasses import replace

import pytest

from arena_hero_bench import (
    COMPARATIVE_PERFORMANCE_EVIDENCE_SCHEMA,
    MeasurementProtocol,
    PerformanceMeasurementError,
    PublicEnvironment,
    measure_comparative_workloads,
)
from arena_hero_sim import (
    BackendWorkloadRunner,
    OptimizedEngineBackend,
    ReferenceWorkloadRunner,
    canonical_reference_scenario_registry,
)
from arena_hero_sim.reference_workload import WorkloadRun
from arena_hero_sim.workload import WorkloadManifest


class CountingRunner:
    def __init__(self, runner, counter: list[int]) -> None:
        self._runner = runner
        self._counter = counter

    def run(self, manifest: WorkloadManifest, *, batch_size: int = 1) -> WorkloadRun:
        self._counter[0] += 1
        return self._runner.run(manifest, batch_size=batch_size)


def _environment() -> PublicEnvironment:
    return PublicEnvironment(
        python_version="3.12.0",
        python_implementation="CPython",
        os_family="TestOS",
        architecture="test-arch",
        cpu_count=4,
        dependency_versions={"arena-hero-sim": "0.2.0", "arena-hero-bench": "0.2.0"},
    )


def _clock(*values: int):
    iterator = iter(values)
    return lambda: next(iterator)


def _protocol(*, batch_size: int = 3) -> MeasurementProtocol:
    return MeasurementProtocol(
        warmup_rounds=1,
        measured_rounds=2,
        batch_size=batch_size,
        worker_count=1,
        timeout_seconds=1.0,
    )


def test_comparative_measurement_executes_real_candidate_and_binds_all_identities() -> None:
    scenarios = canonical_reference_scenario_registry()
    candidate_runs = [0]

    evidence = measure_comparative_workloads(
        _protocol(),
        reference_runner_factory=lambda: ReferenceWorkloadRunner(scenarios),
        candidate_runner_factory=lambda: CountingRunner(
            BackendWorkloadRunner(scenarios, OptimizedEngineBackend(scenarios.scenarios)),
            candidate_runs,
        ),
        environment=_environment(),
        clock=_clock(0, 2_000, 3_000, 6_000, 7_000, 11_000, 12_000, 17_000),
    )

    assert candidate_runs == [4]  # baseline + one warmup + two measured rounds
    assert evidence.schema_version == COMPARATIVE_PERFORMANCE_EVIDENCE_SCHEMA
    assert not evidence.publishable
    assert evidence.protocol.clock == "injected-test-clock"
    assert evidence.production_claim is False
    assert evidence.reference.backend.backend_id == "reference-engine"
    assert evidence.candidate.backend.backend_id == "optimized-python-v1"
    assert evidence.reference.raw_durations_ns == (2_000, 3_000)
    assert evidence.candidate.raw_durations_ns == (4_000, 5_000)
    assert evidence.reference.differential_report_sha256 == evidence.differential_report_sha256
    assert evidence.candidate.differential_report_sha256 == evidence.differential_report_sha256
    assert evidence.reference_run_sha256 == evidence.reference.semantic_run_sha256
    assert evidence.candidate_run_sha256 == evidence.candidate.semantic_run_sha256
    assert evidence.sha256 == evidence.sha256


def test_comparative_measurement_rejects_wrong_candidate_backend_identity() -> None:
    scenarios = canonical_reference_scenario_registry()

    with pytest.raises(PerformanceMeasurementError, match="must differ"):
        measure_comparative_workloads(
            _protocol(batch_size=1),
            reference_runner_factory=lambda: ReferenceWorkloadRunner(scenarios),
            candidate_runner_factory=lambda: ReferenceWorkloadRunner(scenarios),
            environment=_environment(),
            clock=_clock(0, 2_000, 3_000, 6_000, 7_000, 11_000, 12_000, 17_000),
        )


def test_comparative_evidence_rejects_tampered_bindings_and_production_claims() -> None:
    scenarios = canonical_reference_scenario_registry()
    evidence = measure_comparative_workloads(
        _protocol(batch_size=9),
        reference_runner_factory=lambda: ReferenceWorkloadRunner(scenarios),
        candidate_runner_factory=lambda: BackendWorkloadRunner(
            scenarios, OptimizedEngineBackend(scenarios.scenarios)
        ),
        environment=_environment(),
        clock=_clock(0, 2_000, 3_000, 6_000, 7_000, 11_000, 12_000, 17_000),
    )

    with pytest.raises(ValueError, match="candidate run binding"):
        replace(evidence, candidate_run_sha256="0" * 64)
    with pytest.raises(ValueError, match="production"):
        replace(evidence, production_claim=True)
