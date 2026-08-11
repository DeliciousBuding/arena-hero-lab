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
from dataclasses import dataclass, field, replace
from importlib import metadata
from types import MappingProxyType
from typing import Protocol

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

COMPARATIVE_PERFORMANCE_EVIDENCE_SCHEMA = "arena.bench.comparative-performance-evidence.v1"
MINIMUM_CREDIBLE_SAMPLE_NS = 1_000
PERF_COUNTER_CLOCK = "perf_counter_ns"
INJECTED_TEST_CLOCK = "injected-test-clock"
_MAX_WORKERS = 64
_DEPENDENCY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

perf_counter_ns = time.perf_counter_ns


class PerformanceMeasurementError(RuntimeError):
    """Raised when setup cannot produce bounded measurement evidence."""


class WorkloadRunnerProtocol(Protocol):
    def run(self, manifest: WorkloadManifest, *, batch_size: int = 1) -> WorkloadRun: ...


WorkloadRunnerFactory = Callable[[], WorkloadRunnerProtocol]


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _strict_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _strict_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _strict_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return value


def _strict_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be an object with string keys")
    return value


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
    clock: str = PERF_COUNTER_CLOCK
    schema_version: str = MEASUREMENT_PROTOCOL_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != MEASUREMENT_PROTOCOL_SCHEMA:
            raise ValueError("unsupported measurement protocol schema")
        _safe_int(self.warmup_rounds, "warmup_rounds", minimum=0)
        _safe_int(self.measured_rounds, "measured_rounds", minimum=1)
        _safe_int(self.batch_size, "batch_size", minimum=1)
        workers = _safe_int(self.worker_count, "worker_count", minimum=1)
        if workers > _MAX_WORKERS:
            raise ValueError(f"worker_count must be <= {_MAX_WORKERS}")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        _safe_int(self.minimum_sample_ns, "minimum_sample_ns", minimum=1)
        if self.clock not in {PERF_COUNTER_CLOCK, INJECTED_TEST_CLOCK}:
            raise ValueError("clock must identify the fixed wall clock or injected test clock")

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> MeasurementProtocol:
        expected = {
            "schema_version",
            "warmup_rounds",
            "measured_rounds",
            "batch_size",
            "worker_count",
            "timeout_seconds",
            "minimum_sample_ns",
            "clock",
        }
        if set(value) != expected:
            raise ValueError("measurement protocol fields mismatch")
        timeout = value["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout_seconds must be numeric")
        return cls(
            warmup_rounds=_strict_int(value["warmup_rounds"], "warmup_rounds"),
            measured_rounds=_strict_int(value["measured_rounds"], "measured_rounds"),
            batch_size=_strict_int(value["batch_size"], "batch_size"),
            worker_count=_strict_int(value["worker_count"], "worker_count"),
            timeout_seconds=float(timeout),
            minimum_sample_ns=_strict_int(value["minimum_sample_ns"], "minimum_sample_ns"),
            clock=_strict_str(value["clock"], "clock"),
            schema_version=_strict_str(value["schema_version"], "schema_version"),
        )

    def verify(self, expected_sha256: str | None = None) -> None:
        if expected_sha256 is not None and self.sha256 != _sha256(expected_sha256, "protocol"):
            raise ValueError("measurement protocol digest mismatch")

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
                "clock": self.clock,
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

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PublicEnvironment:
        expected = {
            "python_version",
            "python_implementation",
            "os_family",
            "architecture",
            "cpu_count",
            "dependency_versions",
        }
        if set(value) != expected:
            raise ValueError("public environment fields mismatch")
        versions_raw = _strict_mapping(value["dependency_versions"], "dependency_versions")
        versions = {
            key: _strict_str(item, f"dependency {key}") for key, item in versions_raw.items()
        }
        return cls(
            python_version=_strict_str(value["python_version"], "python_version"),
            python_implementation=_strict_str(
                value["python_implementation"], "python_implementation"
            ),
            os_family=_strict_str(value["os_family"], "os_family"),
            architecture=_strict_str(value["architecture"], "architecture"),
            cpu_count=_strict_int(value["cpu_count"], "cpu_count"),
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


def _backend_identity_from_dict(value: Mapping[str, object]) -> WorkloadBackendIdentity:
    expected = {
        "backend_id",
        "engine_version",
        "protocol_version",
        "features",
        "execution_modes",
        "max_batch_size",
        "supports_batch",
        "supports_incremental_world_hash",
        "supports_zero_copy",
        "interchange_formats",
    }
    if set(value) != expected:
        raise ValueError("backend identity fields mismatch")

    def strings(name: str) -> tuple[str, ...]:
        return tuple(_strict_str(item, name) for item in _strict_list(value[name], name))

    return WorkloadBackendIdentity(
        backend_id=_strict_str(value["backend_id"], "backend_id"),
        engine_version=_strict_str(value["engine_version"], "engine_version"),
        protocol_version=_strict_str(value["protocol_version"], "protocol_version"),
        features=strings("features"),
        execution_modes=strings("execution_modes"),
        max_batch_size=_strict_int(value["max_batch_size"], "max_batch_size"),
        supports_batch=_strict_bool(value["supports_batch"], "supports_batch"),
        supports_incremental_world_hash=_strict_bool(
            value["supports_incremental_world_hash"], "supports_incremental_world_hash"
        ),
        supports_zero_copy=_strict_bool(value["supports_zero_copy"], "supports_zero_copy"),
        interchange_formats=strings("interchange_formats"),
    )


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
        if self.schema_version != PERFORMANCE_EVIDENCE_SCHEMA:
            raise ValueError("unsupported performance evidence schema")
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
        if self.warmup_rounds_completed > self.protocol.warmup_rounds:
            raise ValueError("warmup_rounds_completed exceeds the protocol")
        for value in self.raw_durations_ns:
            _safe_int(value, "raw duration", minimum=self.protocol.minimum_sample_ns)
        if len(self.observed_run_sha256s) != len(self.raw_durations_ns):
            raise ValueError("every raw sample must bind one observed digest row")
        for round_digests in self.observed_run_sha256s:
            if len(round_digests) != self.protocol.worker_count:
                raise ValueError("every observed digest row must bind every worker")
            for digest in round_digests:
                _sha256(digest, "observed run")
        if not isinstance(self.publishable, bool):
            raise ValueError("publishable must be a boolean")
        if self.production_claim is not False:
            raise ValueError("performance evidence must keep production_claim=false")
        if any(not isinstance(issue, str) or not issue for issue in self.issues):
            raise ValueError("issues must contain non-empty strings")
        if self.raw_durations_ns:
            expected_median = statistics.median(self.raw_durations_ns)
            expected_p95 = _percentile_nearest_rank(self.raw_durations_ns, 0.95)
            expected_p99 = _percentile_nearest_rank(self.raw_durations_ns, 0.99)
            if self.median_ns != expected_median:
                raise ValueError("median_ns must be derived from raw durations")
            if self.p95_ns != expected_p95 or self.p99_ns != expected_p99:
                raise ValueError("percentiles must be derived from raw durations")
        elif any(value is not None for value in (self.median_ns, self.p95_ns, self.p99_ns)):
            raise ValueError("empty evidence cannot contain timing summaries")
        complete = (
            len(self.raw_durations_ns) == self.protocol.measured_rounds
            and self.warmup_rounds_completed == self.protocol.warmup_rounds
        )
        if self.publishable and self.protocol.clock != PERF_COUNTER_CLOCK:
            raise ValueError("only perf_counter_ns evidence can be publishable")
        if self.publishable and (self.issues or not complete):
            raise ValueError("publishable performance evidence must be complete and issue-free")

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PerformanceEvidence:
        expected = {
            "schema_version",
            "protocol",
            "protocol_sha256",
            "environment",
            "workload_sha256",
            "backend",
            "semantic_run_sha256",
            "differential_report_sha256",
            "raw_durations_ns",
            "observed_run_sha256s",
            "warmup_rounds_completed",
            "measured_rounds_completed",
            "median_ns",
            "p95_ns",
            "p99_ns",
            "publishable",
            "production_claim",
            "issues",
        }
        if set(value) != expected:
            raise ValueError("performance evidence fields mismatch")
        protocol = MeasurementProtocol.from_dict(_strict_mapping(value["protocol"], "protocol"))
        protocol.verify(_strict_str(value["protocol_sha256"], "protocol_sha256"))
        environment = PublicEnvironment.from_dict(
            _strict_mapping(value["environment"], "environment")
        )
        backend = _backend_identity_from_dict(_strict_mapping(value["backend"], "backend"))
        raw = tuple(
            _strict_int(item, "raw duration")
            for item in _strict_list(value["raw_durations_ns"], "raw_durations_ns")
        )
        observed = tuple(
            tuple(
                _strict_str(digest, "observed run")
                for digest in _strict_list(row, "observed digest row")
            )
            for row in _strict_list(value["observed_run_sha256s"], "observed_run_sha256s")
        )
        measured = _strict_int(value["measured_rounds_completed"], "measured_rounds_completed")
        if measured != len(raw):
            raise ValueError("measured_rounds_completed does not match raw durations")
        median = value["median_ns"]
        if median is not None and (
            isinstance(median, bool) or not isinstance(median, (int, float))
        ):
            raise ValueError("median_ns must be numeric or null")
        p95 = value["p95_ns"]
        p99 = value["p99_ns"]
        if p95 is not None:
            p95 = _strict_int(p95, "p95_ns")
        if p99 is not None:
            p99 = _strict_int(p99, "p99_ns")
        return cls(
            protocol=protocol,
            environment=environment,
            workload_sha256=_strict_str(value["workload_sha256"], "workload_sha256"),
            backend=backend,
            semantic_run_sha256=_strict_str(value["semantic_run_sha256"], "semantic_run_sha256"),
            differential_report_sha256=_strict_str(
                value["differential_report_sha256"], "differential_report_sha256"
            ),
            raw_durations_ns=raw,
            observed_run_sha256s=observed,
            warmup_rounds_completed=_strict_int(
                value["warmup_rounds_completed"], "warmup_rounds_completed"
            ),
            median_ns=median,
            p95_ns=p95,
            p99_ns=p99,
            publishable=_strict_bool(value["publishable"], "publishable"),
            issues=tuple(
                _strict_str(item, "issue") for item in _strict_list(value["issues"], "issues")
            ),
            production_claim=_strict_bool(value["production_claim"], "production_claim"),
            schema_version=_strict_str(value["schema_version"], "schema_version"),
        )

    def verify(self, expected_sha256: str | None = None) -> None:
        if expected_sha256 is not None and self.sha256 != _sha256(expected_sha256, "evidence"):
            raise ValueError("performance evidence digest mismatch")

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


@dataclass(frozen=True, slots=True)
class ComparativePerformanceEvidence:
    """Versioned, non-production comparison of two real backend executions."""

    protocol: MeasurementProtocol
    environment: PublicEnvironment
    workload_sha256: str
    reference: PerformanceEvidence
    candidate: PerformanceEvidence
    differential_report_sha256: str
    reference_run_sha256: str
    candidate_run_sha256: str
    episode_order_sha256: str
    publishable: bool
    issues: tuple[str, ...] = field(default_factory=tuple)
    production_claim: bool = False
    schema_version: str = COMPARATIVE_PERFORMANCE_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "workload_sha256", _sha256(self.workload_sha256, "workload"))
        object.__setattr__(
            self,
            "differential_report_sha256",
            _sha256(self.differential_report_sha256, "differential report"),
        )
        object.__setattr__(
            self, "reference_run_sha256", _sha256(self.reference_run_sha256, "reference run")
        )
        object.__setattr__(
            self, "candidate_run_sha256", _sha256(self.candidate_run_sha256, "candidate run")
        )
        object.__setattr__(
            self, "episode_order_sha256", _sha256(self.episode_order_sha256, "episode order")
        )
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.production_claim is not False:
            raise ValueError("comparative evidence cannot claim production performance")
        if self.reference.backend.backend_id == self.candidate.backend.backend_id:
            raise ValueError("comparative evidence requires distinct backend identities")
        if self.reference.protocol != self.protocol or self.candidate.protocol != self.protocol:
            raise ValueError("comparative evidence protocol binding mismatch")
        if (
            self.reference.environment != self.environment
            or self.candidate.environment != self.environment
        ):
            raise ValueError("comparative evidence environment binding mismatch")
        if self.reference.workload_sha256 != self.workload_sha256:
            raise ValueError("reference workload binding mismatch")
        if self.candidate.workload_sha256 != self.workload_sha256:
            raise ValueError("candidate workload binding mismatch")
        if self.reference.semantic_run_sha256 != self.reference_run_sha256:
            raise ValueError("reference run binding mismatch")
        if self.candidate.semantic_run_sha256 != self.candidate_run_sha256:
            raise ValueError("candidate run binding mismatch")
        if self.reference.differential_report_sha256 != self.differential_report_sha256:
            raise ValueError("reference differential binding mismatch")
        if self.candidate.differential_report_sha256 != self.differential_report_sha256:
            raise ValueError("candidate differential binding mismatch")
        expected_publishable = (
            self.reference.publishable and self.candidate.publishable and not self.issues
        )
        if self.publishable is not expected_publishable:
            raise ValueError("comparative publishability must be fail-closed")

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "schema_version": self.schema_version,
                "protocol": self.protocol.to_dict(),
                "protocol_sha256": self.protocol.sha256,
                "environment": self.environment.to_dict(),
                "workload_sha256": self.workload_sha256,
                "reference": self.reference.to_dict(),
                "candidate": self.candidate.to_dict(),
                "differential_report_sha256": self.differential_report_sha256,
                "reference_run_sha256": self.reference_run_sha256,
                "candidate_run_sha256": self.candidate_run_sha256,
                "episode_order_sha256": self.episode_order_sha256,
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
    runners: Sequence[WorkloadRunnerProtocol],
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


def _valid_timer_sample(
    started: object, finished: object, *, label: str, issues: list[str]
) -> int | None:
    """Validate one timer pair against the integer wall-clock contract, fail closed.

    The measurement clock contract is ``Callable[[], int]``; anything else (bools,
    floats, non-finite values, strings, or a backwards clock) is rejected with an
    issue instead of being allowed to poison raw samples.
    """
    if isinstance(started, bool) or not isinstance(started, int):
        issues.append(f"{label} timer start is not an integer")
        return None
    if isinstance(finished, bool) or not isinstance(finished, int):
        issues.append(f"{label} timer finish is not an integer")
        return None
    duration = finished - started
    if isinstance(duration, bool) or not isinstance(duration, int):
        issues.append(f"{label} timer duration is not a finite integer")
        return None
    if duration < 0:
        issues.append(f"{label} timer duration is negative (clock moved backwards)")
        return None
    return duration


def _measure_backend_runners(
    protocol: MeasurementProtocol,
    *,
    environment: PublicEnvironment,
    workload: WorkloadManifest,
    runners: Sequence[WorkloadRunnerProtocol],
    baseline: WorkloadRun,
    gate: DifferentialReport,
    initial_issues: Sequence[str],
    timer: Callable[[], int],
) -> PerformanceEvidence:
    issues = list(initial_issues)
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
            label = f"measured {round_index}"
            duration = _valid_timer_sample(started, finished, label=label, issues=issues)
            if duration is None:
                continue
            if duration <= 0:
                issues.append(f"{label} duration is not positive")
                continue
            if duration < protocol.minimum_sample_ns:
                issues.append(f"{label} duration is below the credibility floor")
                continue
            if duration > timeout_ns:
                issues.append(f"{label} exceeded timeout_seconds")
                continue
            raw_durations.append(duration)
            observed_digests.append(
                _record_round_issues(
                    runs,
                    expected_sha256=baseline.sha256,
                    label=label,
                    issues=issues,
                )
            )
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    median_ns = statistics.median(raw_durations) if raw_durations else None
    p95_ns = _percentile_nearest_rank(raw_durations, 0.95) if raw_durations else None
    p99_ns = _percentile_nearest_rank(raw_durations, 0.99) if raw_durations else None
    if len(raw_durations) != protocol.measured_rounds:
        issues.append("measured raw sample count is incomplete")
    if warmups_completed != protocol.warmup_rounds:
        issues.append("warmup round count is incomplete")

    return PerformanceEvidence(
        protocol=protocol,
        environment=environment,
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


def measure_comparative_workloads(
    protocol: MeasurementProtocol,
    *,
    reference_runner_factory: WorkloadRunnerFactory,
    candidate_runner_factory: WorkloadRunnerFactory,
    manifest: WorkloadManifest | None = None,
    environment: PublicEnvironment | None = None,
    clock: Callable[[], int] | None = None,
) -> ComparativePerformanceEvidence:
    """Execute and time both injected backends against one frozen workload."""

    workload = manifest or canonical_reference_workload_manifest()
    public_environment = environment or PublicEnvironment.capture()
    effective_protocol = protocol if clock is None else replace(protocol, clock=INJECTED_TEST_CLOCK)
    timer = perf_counter_ns if clock is None else clock
    reference_runners = tuple(
        reference_runner_factory() for _ in range(effective_protocol.worker_count)
    )
    candidate_runners = tuple(
        candidate_runner_factory() for _ in range(effective_protocol.worker_count)
    )
    reference_run = reference_runners[0].run(workload, batch_size=protocol.batch_size)
    candidate_run = candidate_runners[0].run(workload, batch_size=protocol.batch_size)
    issues: list[str] = []
    if reference_run.backend.backend_id == candidate_run.backend.backend_id:
        raise PerformanceMeasurementError("candidate backend identity must differ from reference")
    gate = compare_workload_runs(reference_run, candidate_run)
    if not gate.passed:
        issues.append("differential gate did not pass")
    episode_order = tuple(episode.episode_id for episode in reference_run.episodes)
    if episode_order != tuple(episode.episode_id for episode in candidate_run.episodes):
        issues.append("candidate episode order does not match reference")
    episode_order_sha256 = content_sha256(episode_order)
    reference_evidence = _measure_backend_runners(
        effective_protocol,
        environment=public_environment,
        workload=workload,
        runners=reference_runners,
        baseline=reference_run,
        gate=gate,
        initial_issues=(
            *issues,
            *(("injected test clock is not publishable",) if clock is not None else ()),
        ),
        timer=timer,
    )
    candidate_evidence = _measure_backend_runners(
        effective_protocol,
        environment=public_environment,
        workload=workload,
        runners=candidate_runners,
        baseline=candidate_run,
        gate=gate,
        initial_issues=(
            *issues,
            *(("injected test clock is not publishable",) if clock is not None else ()),
        ),
        timer=timer,
    )
    combined_issues = tuple(dict.fromkeys((*reference_evidence.issues, *candidate_evidence.issues)))
    return ComparativePerformanceEvidence(
        protocol=effective_protocol,
        environment=public_environment,
        workload_sha256=workload.sha256,
        reference=reference_evidence,
        candidate=candidate_evidence,
        differential_report_sha256=gate.sha256,
        reference_run_sha256=reference_run.sha256,
        candidate_run_sha256=candidate_run.sha256,
        episode_order_sha256=episode_order_sha256,
        publishable=reference_evidence.publishable
        and candidate_evidence.publishable
        and not combined_issues,
        issues=combined_issues,
        production_claim=False,
    )


def measure_reference_workload(
    protocol: MeasurementProtocol,
    *,
    manifest: WorkloadManifest | None = None,
    scenario_registry: ReferenceScenarioRegistry | None = None,
    environment: PublicEnvironment | None = None,
    differential_report: DifferentialReport | None = None,
    candidate_run: WorkloadRun | None = None,
    clock: Callable[[], int] | None = None,
) -> PerformanceEvidence:
    """Measure real reference-engine runs; registry/scenario construction stays outside samples.

    An injected ``differential_report`` is trusted only when it is byte-identical to a gate
    recomputed over the current baseline run and, when supplied, ``candidate_run``. Stale,
    forged, or wrong-identity reports fail closed and never enter publishable evidence.
    """

    workload = manifest or canonical_reference_workload_manifest()
    scenarios = scenario_registry or canonical_reference_scenario_registry()
    effective_protocol = protocol if clock is None else replace(protocol, clock=INJECTED_TEST_CLOCK)
    runners = tuple(
        ReferenceWorkloadRunner(scenarios) for _ in range(effective_protocol.worker_count)
    )
    timer = perf_counter_ns if clock is None else clock
    public_environment = environment or PublicEnvironment.capture()

    baseline = runners[0].run(workload, batch_size=protocol.batch_size)
    issues: list[str] = []
    if not baseline.publishable:
        issues.append("baseline semantic run is not complete and publishable")

    gate = (
        compare_workload_runs(baseline, candidate_run)
        if candidate_run is not None
        else compare_workload_runs(baseline, baseline)
    )
    if not gate.passed:
        issues.append("differential gate did not pass")
    if differential_report is not None:
        if candidate_run is None:
            issues.append("differential report requires a candidate run")
        else:
            if differential_report.workload_sha256 != gate.workload_sha256:
                issues.append("differential report workload digest mismatch")
            if differential_report.reference_run_sha256 != gate.reference_run_sha256:
                issues.append("differential report reference run digest mismatch")
            if differential_report.candidate_run_sha256 != gate.candidate_run_sha256:
                issues.append("differential report candidate run digest mismatch")
            if differential_report.sha256 != gate.sha256:
                issues.append("differential report content digest mismatch")

    return _measure_backend_runners(
        effective_protocol,
        environment=public_environment,
        workload=workload,
        runners=runners,
        baseline=baseline,
        gate=gate,
        initial_issues=(
            *issues,
            *(("injected test clock is not publishable",) if clock is not None else ()),
        ),
        timer=timer,
    )
