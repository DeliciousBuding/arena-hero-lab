"""Fail-closed measurement evidence for the real canonical reference workload."""

from __future__ import annotations

import math
import os
import platform
import re
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib import metadata
from types import MappingProxyType

from arena_hero_sim.reference_workload import (
    DifferentialReport,
    ReferenceScenarioRegistry,
    ReferenceWorkloadRunner,
    WorkloadBackendIdentity,
    WorkloadRun,
    canonical_reference_scenario_registry,
    canonical_reference_workload_manifest,
    compare_workload_runs,
)
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value
from arena_hero_sim.workload import WorkloadManifest

MEASUREMENT_PROTOCOL_SCHEMA = "arena.bench.measurement-protocol.v1"
PERFORMANCE_EVIDENCE_SCHEMA = "arena.bench.performance-evidence.v1"
MINIMUM_CREDIBLE_SAMPLE_NS = 1_000
_MAX_WORKERS = 64
_DEPENDENCY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

perf_counter_ns = time.perf_counter_ns


class PerformanceMeasurementError(RuntimeError):
    """Raised when setup cannot produce bounded measurement evidence."""


def _sha256(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _safe_int(value: int, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    return value


def _public_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    lowered = normalized.casefold()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if any(token in normalized for token in ("/", "\\", "\n", "\r", "\0")):
        raise ValueError(f"{field_name} must not contain paths or control characters")
    if re.search(r"\b[A-Za-z]:", normalized) or "://" in normalized:
        raise ValueError(f"{field_name} must not contain a path or URL")
    if any(
        token in lowered
        for token in ("hostname=", "host=", "username=", "user=", "path=", "home=", "cwd=")
    ):
        raise ValueError(f"{field_name} contains host-like metadata")
    return normalized


def _frozen_versions(value: Mapping[str, str]) -> Mapping[str, str]:
    versions: dict[str, str] = {}
    for name, version in value.items():
        normalized_name = str(name).strip()
        if not _DEPENDENCY_NAME.fullmatch(normalized_name):
            raise ValueError("dependency names must be portable identifiers")
        if normalized_name.casefold() in {
            "host",
            "hostname",
            "user",
            "username",
            "path",
            "home",
            "cwd",
        }:
            raise ValueError("dependency metadata must not contain host identity")
        versions[normalized_name] = _public_text(str(version), f"dependency {normalized_name}")
    return MappingProxyType(dict(sorted(versions.items())))


def _percentile_nearest_rank(samples: Sequence[int], percentile: float) -> int:
    ordered = sorted(samples)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


@dataclass(frozen=True, slots=True)
class MeasurementProtocol:
    """Content-addressed timing configuration for one workload/worker point."""

    warmup_rounds: int
    measured_rounds: int
    batch_size: int
    worker_count: int
    timeout_seconds: float
    minimum_sample_ns: int = MINIMUM_CREDIBLE_SAMPLE_NS
    schema_version: str = MEASUREMENT_PROTOCOL_SCHEMA

    def __post_init__(self) -> None:
        _safe_int(self.warmup_rounds, "warmup_rounds", minimum=0)
        _safe_int(self.measured_rounds, "measured_rounds", minimum=1)
        _safe_int(self.batch_size, "batch_size", minimum=1)
        workers = _safe_int(self.worker_count, "worker_count", minimum=1)
        if workers > _MAX_WORKERS:
            raise ValueError(f"worker_count must be <= {_MAX_WORKERS}")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        _safe_int(self.minimum_sample_ns, "minimum_sample_ns", minimum=1)

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "schema_version": self.schema_version,
                "warmup_rounds": self.warmup_rounds,
                "measured_rounds": self.measured_rounds,
                "batch_size": self.batch_size,
                "worker_count": self.worker_count,
                "timeout_seconds": self.timeout_seconds,
                "minimum_sample_ns": self.minimum_sample_ns,
                "clock": "perf_counter_ns",
            }
        )
        assert isinstance(value, dict)
        return value

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class PublicEnvironment:
    """Environment snapshot deliberately excluding host, user, and path identity."""

    python_version: str
    python_implementation: str
    os_family: str
    architecture: str
    cpu_count: int
    dependency_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "python_version",
            "python_implementation",
            "os_family",
            "architecture",
        ):
            object.__setattr__(
                self,
                field_name,
                _public_text(str(getattr(self, field_name)), field_name),
            )
        _safe_int(self.cpu_count, "cpu_count", minimum=1)
        object.__setattr__(
            self,
            "dependency_versions",
            _frozen_versions(self.dependency_versions),
        )

    @classmethod
    def capture(cls) -> PublicEnvironment:
        versions: dict[str, str] = {}
        for package in ("arena-hero-sim", "arena-hero-bench"):
            try:
                versions[package] = metadata.version(package)
            except metadata.PackageNotFoundError:
                versions[package] = "not-installed"
        return cls(
            python_version=platform.python_version(),
            python_implementation=platform.python_implementation(),
            os_family=platform.system() or os.name,
            architecture=platform.machine() or "unknown",
            cpu_count=os.cpu_count() or 1,
            dependency_versions=versions,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "python_version": self.python_version,
                "python_implementation": self.python_implementation,
                "os_family": self.os_family,
                "architecture": self.architecture,
                "cpu_count": self.cpu_count,
                "dependency_versions": self.dependency_versions,
            }
        )
        assert isinstance(value, dict)
        return value


@dataclass(frozen=True, slots=True)
class PerformanceEvidence:
    """Raw, content-addressed timing evidence with explicit publication eligibility."""

    protocol: MeasurementProtocol
    environment: PublicEnvironment
    workload_sha256: str
    backend: WorkloadBackendIdentity
    semantic_run_sha256: str
    differential_report_sha256: str
    raw_durations_ns: tuple[int, ...]
    observed_run_sha256s: tuple[tuple[str, ...], ...]
    warmup_rounds_completed: int
    median_ns: float | None
    p95_ns: int | None
    p99_ns: int | None
    publishable: bool
    issues: tuple[str, ...] = field(default_factory=tuple)
    production_claim: bool = False
    schema_version: str = PERFORMANCE_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "workload_sha256", _sha256(self.workload_sha256, "workload"))
        object.__setattr__(
            self,
            "semantic_run_sha256",
            _sha256(self.semantic_run_sha256, "semantic run"),
        )
        object.__setattr__(
            self,
            "differential_report_sha256",
            _sha256(self.differential_report_sha256, "differential report"),
        )
        object.__setattr__(self, "raw_durations_ns", tuple(self.raw_durations_ns))
        object.__setattr__(
            self,
            "observed_run_sha256s",
            tuple(tuple(round_digests) for round_digests in self.observed_run_sha256s),
        )
        object.__setattr__(self, "issues", tuple(self.issues))
        _safe_int(self.warmup_rounds_completed, "warmup_rounds_completed", minimum=0)
        if any(
            isinstance(value, bool) or not isinstance(value, int) for value in self.raw_durations_ns
        ):
            raise ValueError("raw durations must be integers")
        for round_digests in self.observed_run_sha256s:
            for digest in round_digests:
                _sha256(digest, "observed run")
        if self.production_claim:
            raise ValueError("performance evidence must keep production_claim=false")
        if self.publishable and self.issues:
            raise ValueError("publishable performance evidence cannot contain issues")
        if self.publishable and len(self.raw_durations_ns) != self.protocol.measured_rounds:
            raise ValueError("publishable evidence requires every measured raw sample")
        if self.publishable and len(self.observed_run_sha256s) != self.protocol.measured_rounds:
            raise ValueError("publishable evidence requires observed digests for every sample")
        if self.publishable and any(
            len(round_digests) != self.protocol.worker_count
            for round_digests in self.observed_run_sha256s
        ):
            raise ValueError("publishable evidence requires every worker result digest")

    @property
    def sample_count(self) -> int:
        return len(self.raw_durations_ns)

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "schema_version": self.schema_version,
                "protocol": self.protocol.to_dict(),
                "protocol_sha256": self.protocol.sha256,
                "environment": self.environment.to_dict(),
                "workload_sha256": self.workload_sha256,
                "backend": self.backend.to_dict(),
                "semantic_run_sha256": self.semantic_run_sha256,
                "differential_report_sha256": self.differential_report_sha256,
                "raw_durations_ns": self.raw_durations_ns,
                "observed_run_sha256s": self.observed_run_sha256s,
                "warmup_rounds_completed": self.warmup_rounds_completed,
                "measured_rounds_completed": self.sample_count,
                "median_ns": self.median_ns,
                "p95_ns": self.p95_ns,
                "p99_ns": self.p99_ns,
                "publishable": self.publishable,
                "production_claim": self.production_claim,
                "issues": self.issues,
            }
        )
        assert isinstance(value, dict)
        return value

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())


def _run_worker_round(
    runners: Sequence[ReferenceWorkloadRunner],
    manifest: WorkloadManifest,
    protocol: MeasurementProtocol,
    executor: ThreadPoolExecutor | None,
) -> tuple[WorkloadRun, ...]:
    if executor is None:
        return (runners[0].run(manifest, batch_size=protocol.batch_size),)
    futures = [
        executor.submit(runner.run, manifest, batch_size=protocol.batch_size) for runner in runners
    ]
    return tuple(future.result(timeout=protocol.timeout_seconds) for future in futures)


def _record_round_issues(
    runs: Sequence[WorkloadRun],
    *,
    expected_sha256: str,
    label: str,
    issues: list[str],
) -> tuple[str, ...]:
    digests = tuple(run.sha256 for run in runs)
    for index, run in enumerate(runs):
        if not run.publishable:
            issues.append(f"{label} worker {index} did not return a complete publishable run")
        if run.sha256 != expected_sha256:
            issues.append(f"{label} worker {index} semantic run digest drifted")
    return digests


def measure_reference_workload(
    protocol: MeasurementProtocol,
    *,
    manifest: WorkloadManifest | None = None,
    scenario_registry: ReferenceScenarioRegistry | None = None,
    environment: PublicEnvironment | None = None,
    differential_report: DifferentialReport | None = None,
    clock: Callable[[], int] | None = None,
) -> PerformanceEvidence:
    """Measure real reference-engine runs; registry/scenario construction stays outside samples."""

    workload = manifest or canonical_reference_workload_manifest()
    scenarios = scenario_registry or canonical_reference_scenario_registry()
    runners = tuple(ReferenceWorkloadRunner(scenarios) for _ in range(protocol.worker_count))
    timer = clock or perf_counter_ns
    public_environment = environment or PublicEnvironment.capture()

    baseline = runners[0].run(workload, batch_size=protocol.batch_size)
    gate = differential_report or compare_workload_runs(baseline, baseline)
    issues: list[str] = []
    if gate.workload_sha256 != workload.sha256:
        issues.append("differential report workload digest mismatch")
    if not gate.passed:
        issues.append("differential gate did not pass")
    if not baseline.publishable:
        issues.append("baseline semantic run is not complete and publishable")

    warmups_completed = 0
    raw_durations: list[int] = []
    observed_digests: list[tuple[str, ...]] = []
    executor = (
        ThreadPoolExecutor(max_workers=protocol.worker_count, thread_name_prefix="arena-perf")
        if protocol.worker_count > 1
        else None
    )
    try:
        for round_index in range(protocol.warmup_rounds):
            try:
                runs = _run_worker_round(runners, workload, protocol, executor)
            except Exception as error:
                issues.append(f"warmup {round_index} failed: {type(error).__name__}")
                break
            _record_round_issues(
                runs,
                expected_sha256=baseline.sha256,
                label=f"warmup {round_index}",
                issues=issues,
            )
            warmups_completed += 1

        timeout_ns = math.ceil(protocol.timeout_seconds * 1_000_000_000)
        for round_index in range(protocol.measured_rounds):
            started = timer()
            try:
                runs = _run_worker_round(runners, workload, protocol, executor)
            except Exception as error:
                issues.append(f"measured {round_index} failed: {type(error).__name__}")
                break
            finished = timer()
            duration = finished - started
            raw_durations.append(duration)
            observed_digests.append(
                _record_round_issues(
                    runs,
                    expected_sha256=baseline.sha256,
                    label=f"measured {round_index}",
                    issues=issues,
                )
            )
            if duration <= 0:
                issues.append(f"measured {round_index} duration is not positive")
            elif duration < protocol.minimum_sample_ns:
                issues.append(f"measured {round_index} duration is below the credibility floor")
            if duration > timeout_ns:
                issues.append(f"measured {round_index} exceeded timeout_seconds")
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    valid_summary = len(raw_durations) == protocol.measured_rounds and all(
        duration > 0 for duration in raw_durations
    )
    median_ns = statistics.median(raw_durations) if valid_summary else None
    p95_ns = _percentile_nearest_rank(raw_durations, 0.95) if valid_summary else None
    p99_ns = _percentile_nearest_rank(raw_durations, 0.99) if valid_summary else None
    if len(raw_durations) != protocol.measured_rounds:
        issues.append("measured raw sample count is incomplete")
    if warmups_completed != protocol.warmup_rounds:
        issues.append("warmup round count is incomplete")

    return PerformanceEvidence(
        protocol=protocol,
        environment=public_environment,
        workload_sha256=workload.sha256,
        backend=baseline.backend,
        semantic_run_sha256=baseline.sha256,
        differential_report_sha256=gate.sha256,
        raw_durations_ns=tuple(raw_durations),
        observed_run_sha256s=tuple(observed_digests),
        warmup_rounds_completed=warmups_completed,
        median_ns=median_ns,
        p95_ns=p95_ns,
        p99_ns=p99_ns,
        publishable=not issues,
        issues=tuple(issues),
        production_claim=False,
    )
