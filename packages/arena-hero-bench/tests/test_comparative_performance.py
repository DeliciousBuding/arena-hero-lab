from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from arena_hero_bench import (
    COMPARATIVE_PERFORMANCE_EVIDENCE_SCHEMA,
    REPLAY_ATTESTATION_UNATTESTED,
    REPLAY_ATTESTATION_VERIFIED,
    MeasurementProtocol,
    PerformanceMeasurementError,
    PublicEnvironment,
    measure_comparative_workloads,
    performance,
)
from arena_hero_sim import (
    BackendWorkloadRunner,
    OptimizedEngineBackend,
    ReferenceEngineBackend,
    ReplayArtifactIdentity,
    SimulatorBackend,
    canonical_reference_scenario_registry,
)
from arena_hero_sim.contracts import SimulationRequest, SimulationResult
from arena_hero_sim.reference_workload import WorkloadRun
from arena_hero_sim.workload import WorkloadManifest


class CapturingBackend:
    def __init__(
        self,
        backend: ReferenceEngineBackend | OptimizedEngineBackend,
        replay_bytes: dict[str, bytes],
    ) -> None:
        self._backend = backend
        self._replay_bytes = replay_bytes
        self.batch_calls = 0

    @property
    def descriptor(self):
        return self._backend.descriptor

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        episode = self._backend.execute(request)
        identity = episode.replay.artifact_identity
        self._replay_bytes[identity.envelope_sha256] = episode.replay.to_bytes()
        return self._backend.simulate(request)

    def simulate_batch(
        self, requests: tuple[SimulationRequest, ...]
    ) -> tuple[SimulationResult, ...]:
        self.batch_calls += 1
        return tuple(self.simulate(request) for request in requests)


class MappingReplayResolver:
    def __init__(self, replay_bytes: dict[str, bytes]) -> None:
        self._replay_bytes = replay_bytes

    def resolve(self, identity: ReplayArtifactIdentity) -> bytes:
        return self._replay_bytes[identity.envelope_sha256]


class StaticRunner:
    def __init__(self, run: WorkloadRun) -> None:
        self._run = run
        self.calls = 0

    def run(self, manifest: WorkloadManifest, *, batch_size: int = 1) -> WorkloadRun:
        self.calls += 1
        return self._run


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


def _capturing_backends():
    scenarios = canonical_reference_scenario_registry()
    replays: dict[str, bytes] = {}
    reference = CapturingBackend(ReferenceEngineBackend(scenarios.scenarios), replays)
    candidate = CapturingBackend(OptimizedEngineBackend(scenarios.scenarios), replays)
    return scenarios, replays, reference, candidate


def test_comparative_measurement_executes_concrete_backends_and_attests_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios, replays, reference, candidate = _capturing_backends()
    monkeypatch.setattr(
        performance,
        "perf_counter_ns",
        _clock(0, 2_000, 3_000, 6_000, 7_000, 11_000, 12_000, 17_000),
    )

    evidence = measure_comparative_workloads(
        _protocol(),
        reference_backend=reference,
        candidate_backend=candidate,
        replay_resolver=MappingReplayResolver(replays),
        scenario_registry=scenarios,
        environment=_environment(),
    )

    assert reference.batch_calls > 0
    assert candidate.batch_calls > 0
    assert evidence.schema_version == COMPARATIVE_PERFORMANCE_EVIDENCE_SCHEMA
    assert evidence.schema_version == "arena.bench.comparative-performance-evidence.v2"
    assert evidence.publishable
    assert evidence.production_claim is False
    assert evidence.replay_attestation == REPLAY_ATTESTATION_VERIFIED
    assert evidence.attested_replay_count == evidence.expected_replay_count == 18
    assert evidence.reference.backend.backend_id == "reference-engine"
    assert evidence.candidate.backend.backend_id == "optimized-python-v1"
    assert evidence.reference.raw_durations_ns == (2_000, 3_000)
    assert evidence.candidate.raw_durations_ns == (4_000, 5_000)
    assert evidence.reference.differential_report_sha256 == evidence.differential_report_sha256
    assert evidence.candidate.differential_report_sha256 == evidence.differential_report_sha256
    assert evidence.reference_run_sha256 == evidence.reference.semantic_run_sha256
    assert evidence.candidate_run_sha256 == evidence.candidate.semantic_run_sha256


def test_missing_replay_resolver_is_explicitly_unattested_and_non_publishable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = canonical_reference_scenario_registry()
    monkeypatch.setattr(
        performance,
        "perf_counter_ns",
        _clock(0, 2_000, 3_000, 6_000, 7_000, 11_000, 12_000, 17_000),
    )
    evidence = measure_comparative_workloads(
        _protocol(),
        reference_backend=ReferenceEngineBackend(scenarios.scenarios),
        candidate_backend=OptimizedEngineBackend(scenarios.scenarios),
        scenario_registry=scenarios,
        environment=_environment(),
    )
    assert not evidence.publishable
    assert evidence.replay_attestation == REPLAY_ATTESTATION_UNATTESTED
    assert evidence.attested_replay_count == 0
    assert any("self-reported and unattested" in issue for issue in evidence.issues)


def test_tampered_replay_bytes_fail_attestation(monkeypatch: pytest.MonkeyPatch) -> None:
    scenarios, replays, reference, candidate = _capturing_backends()
    monkeypatch.setattr(
        performance,
        "perf_counter_ns",
        _clock(0, 2_000, 3_000, 6_000, 7_000, 11_000, 12_000, 17_000),
    )

    class TamperingResolver(MappingReplayResolver):
        def resolve(self, identity: ReplayArtifactIdentity) -> bytes:
            data = super().resolve(identity)
            return data[:-1] + bytes([data[-1] ^ 1])

    evidence = measure_comparative_workloads(
        _protocol(),
        reference_backend=reference,
        candidate_backend=candidate,
        replay_resolver=TamperingResolver(replays),
        scenario_registry=scenarios,
        environment=_environment(),
    )
    assert not evidence.publishable
    assert evidence.replay_attestation == REPLAY_ATTESTATION_UNATTESTED
    assert evidence.attested_replay_count == 0
    assert any("attestation failed" in issue for issue in evidence.issues)


def test_static_runner_injection_is_private_and_never_publishable() -> None:
    scenarios = canonical_reference_scenario_registry()
    manifest = performance.canonical_reference_workload_manifest()
    reference_run = BackendWorkloadRunner(
        scenarios, ReferenceEngineBackend(scenarios.scenarios)
    ).run(manifest, batch_size=3)
    candidate_run = BackendWorkloadRunner(
        scenarios, OptimizedEngineBackend(scenarios.scenarios)
    ).run(manifest, batch_size=3)
    reference = StaticRunner(reference_run)
    candidate = StaticRunner(candidate_run)

    evidence = performance._measure_comparative_workloads_for_testing(
        _protocol(),
        reference_runner_factory=lambda: reference,
        candidate_runner_factory=lambda: candidate,
        environment=_environment(),
        clock=_clock(0, 2_000, 3_000, 6_000, 7_000, 11_000, 12_000, 17_000),
    )

    assert reference.calls == 4
    assert candidate.calls == 4
    assert not evidence.publishable
    assert evidence.replay_attestation == REPLAY_ATTESTATION_UNATTESTED
    assert any("test-only" in issue for issue in evidence.issues)


def test_public_api_rejects_runner_shaped_objects() -> None:
    scenarios = canonical_reference_scenario_registry()
    manifest = performance.canonical_reference_workload_manifest()
    static = StaticRunner(
        BackendWorkloadRunner(scenarios, ReferenceEngineBackend(scenarios.scenarios)).run(manifest)
    )
    with pytest.raises(PerformanceMeasurementError, match="SimulatorBackend"):
        measure_comparative_workloads(
            _protocol(),
            reference_backend=cast(SimulatorBackend, static),
            candidate_backend=OptimizedEngineBackend(scenarios.scenarios),
            scenario_registry=scenarios,
            environment=_environment(),
        )


def test_comparative_evidence_strict_round_trip_and_tamper_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios, replays, reference, candidate = _capturing_backends()
    monkeypatch.setattr(
        performance,
        "perf_counter_ns",
        _clock(0, 2_000, 3_000, 6_000, 7_000, 11_000, 12_000, 17_000),
    )
    evidence = measure_comparative_workloads(
        _protocol(batch_size=9),
        reference_backend=reference,
        candidate_backend=candidate,
        replay_resolver=MappingReplayResolver(replays),
        scenario_registry=scenarios,
        environment=_environment(),
    )
    restored = type(evidence).from_dict(evidence.to_dict())
    assert restored == evidence
    restored.verify(evidence.sha256)
    with pytest.raises(ValueError, match="candidate run binding"):
        replace(evidence, candidate_run_sha256="0" * 64)
    with pytest.raises(ValueError, match="production"):
        replace(evidence, production_claim=True)
    with pytest.raises(ValueError, match="fail-closed"):
        replace(evidence, replay_attestation=REPLAY_ATTESTATION_UNATTESTED)
