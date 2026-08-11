"""Executable local microbenchmarks for platform overhead and the real reference engine."""

from __future__ import annotations

import argparse
import json
import math
import platform
import re
import statistics
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from arena_hero_sim.contracts import RulesetRef, SimulationRequest, SimulatorConfig
from arena_hero_sim.reference import ReferenceBackendPlaceholder
from arena_hero_sim.reference_workload import WorkloadRun, run_canonical_reference_workload
from arena_hero_sim.registry import BackendRegistry
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

MICROBENCHMARK_SCHEMA = "arena.sim.microbenchmark.v1"
REFERENCE_WORKLOAD_BENCHMARK_SCHEMA_V1 = "arena.sim.reference-workload-benchmark.v1"
REFERENCE_WORKLOAD_BENCHMARK_SCHEMA = "arena.sim.reference-workload-benchmark.v2"
REFERENCE_WORKLOAD_MINIMUM_SAMPLE_NS = 1_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RULES_SHA256 = "0" * 64
_STATE_SHA256 = "1" * 64


@dataclass(frozen=True, slots=True)
class MicrobenchmarkReport:
    schema_version: str
    benchmark: str
    backend_id: str
    engine_version: str
    episodes_per_repeat: int
    repeats: int
    batch_size: int
    durations_ns: tuple[int, ...]
    median_ns: int
    p95_ns: int
    episodes_per_second: float
    python_version: str
    platform: str
    production_claim: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "durations_ns", tuple(self.durations_ns))
        if self.schema_version != MICROBENCHMARK_SCHEMA:
            raise ValueError("unsupported microbenchmark report schema")
        for field_name in ("episodes_per_repeat", "repeats", "batch_size"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if len(self.durations_ns) != self.repeats:
            raise ValueError("durations_ns must contain exactly one sample per repeat")
        if any(
            isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0
            for duration in self.durations_ns
        ):
            raise ValueError("durations_ns must contain positive integer nanoseconds")
        expected_median = int(statistics.median(self.durations_ns))
        expected_p95 = _percentile(self.durations_ns, 0.95)
        if self.median_ns != expected_median or self.p95_ns != expected_p95:
            raise ValueError("microbenchmark summaries must match raw durations")
        expected_throughput = self.episodes_per_repeat / (expected_median / 1_000_000_000)
        if (
            not math.isfinite(self.episodes_per_second)
            or self.episodes_per_second <= 0
            or not math.isclose(
                self.episodes_per_second,
                expected_throughput,
                rel_tol=1e-12,
                abs_tol=0.0,
            )
        ):
            raise ValueError("episodes_per_second must match the raw-duration median")
        if self.production_claim is not False:
            raise ValueError("microbenchmark reports cannot claim production performance")

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["durations_ns"] = list(self.durations_ns)
        return value


@dataclass(frozen=True, slots=True)
class ReferenceWorkloadBenchmarkReport:
    """Strict local evidence for one canonical reference-workload measurement."""

    schema_version: str
    benchmark: str
    workload_id: str
    workload_version: str
    workload_sha256: str
    run_sha256: str
    backend_id: str
    engine_version: str
    protocol_version: str
    batch_size: int
    repeats: int
    episodes: int
    ticks: int
    durations_ns: tuple[int, ...]
    median_ns: int
    p95_ns: int
    python_version: str
    platform: str
    production_claim: bool = False
    p99_ns: int | None = None
    minimum_sample_ns: int = 1
    clock: str = "unknown-legacy-clock"
    publishable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "durations_ns", tuple(self.durations_ns))
        if self.schema_version not in {
            REFERENCE_WORKLOAD_BENCHMARK_SCHEMA_V1,
            REFERENCE_WORKLOAD_BENCHMARK_SCHEMA,
        }:
            raise ValueError("unsupported reference workload benchmark schema")
        if self.benchmark != "reference-workload":
            raise ValueError("benchmark must identify the canonical reference workload")
        for field_name in (
            "workload_id",
            "workload_version",
            "backend_id",
            "engine_version",
            "protocol_version",
            "python_version",
            "platform",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in ("workload_sha256", "run_sha256"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
        for field_name in ("batch_size", "repeats", "episodes"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if isinstance(self.ticks, bool) or not isinstance(self.ticks, int) or self.ticks < 0:
            raise ValueError("ticks must be a non-negative integer")
        if (
            isinstance(self.minimum_sample_ns, bool)
            or not isinstance(self.minimum_sample_ns, int)
            or self.minimum_sample_ns < 1
        ):
            raise ValueError("minimum_sample_ns must be a positive integer")
        if len(self.durations_ns) != self.repeats:
            raise ValueError("durations_ns must contain exactly one sample per repeat")
        if any(
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or duration < self.minimum_sample_ns
            for duration in self.durations_ns
        ):
            raise ValueError("durations_ns must meet the minimum credible sample floor")
        expected_median = int(statistics.median(self.durations_ns))
        expected_p95 = _percentile(self.durations_ns, 0.95)
        expected_p99 = _percentile(self.durations_ns, 0.99)
        if self.median_ns != expected_median or self.p95_ns != expected_p95:
            raise ValueError("reference workload summaries must match raw durations")
        if self.schema_version == REFERENCE_WORKLOAD_BENCHMARK_SCHEMA_V1:
            if self.p99_ns is None:
                object.__setattr__(self, "p99_ns", expected_p99)
            elif self.p99_ns != expected_p99:
                raise ValueError("p99_ns must match raw durations")
            if self.publishable:
                raise ValueError("legacy v1 timing provenance is unattested")
        else:
            if self.p99_ns != expected_p99:
                raise ValueError("p99_ns must match raw durations")
            if self.clock not in {"perf_counter_ns", "injected-test-clock"}:
                raise ValueError("clock provenance is unsupported")
            expected_publishable = self.clock == "perf_counter_ns"
            if self.publishable is not expected_publishable:
                raise ValueError("publishability must match fixed clock provenance")
        if self.production_claim is not False:
            raise ValueError(
                "reference workload benchmark reports cannot claim production performance"
            )

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object], *, expected_sha256: str | None = None
    ) -> ReferenceWorkloadBenchmarkReport:
        schema = value.get("schema_version")
        v1_fields = {
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
            "durations_ns",
            "median_ns",
            "p95_ns",
            "python_version",
            "platform",
            "production_claim",
        }
        v2_fields = v1_fields | {"p99_ns", "minimum_sample_ns", "clock", "publishable"}
        expected = v1_fields if schema == REFERENCE_WORKLOAD_BENCHMARK_SCHEMA_V1 else v2_fields
        if set(value) != expected:
            raise ValueError("reference workload benchmark fields mismatch")

        def text(name: str) -> str:
            item = value[name]
            if not isinstance(item, str):
                raise ValueError(f"{name} must be a string")
            return item

        def integer(name: str) -> int:
            item = value[name]
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError(f"{name} must be an integer")
            return item

        durations = value["durations_ns"]
        if not isinstance(durations, list):
            raise ValueError("durations_ns must be a list")
        production_claim = value["production_claim"]
        if not isinstance(production_claim, bool):
            raise ValueError("production_claim must be a boolean")
        publishable = value.get("publishable", False)
        if not isinstance(publishable, bool):
            raise ValueError("publishable must be a boolean")
        report = cls(
            schema_version=text("schema_version"),
            benchmark=text("benchmark"),
            workload_id=text("workload_id"),
            workload_version=text("workload_version"),
            workload_sha256=text("workload_sha256"),
            run_sha256=text("run_sha256"),
            backend_id=text("backend_id"),
            engine_version=text("engine_version"),
            protocol_version=text("protocol_version"),
            batch_size=integer("batch_size"),
            repeats=integer("repeats"),
            episodes=integer("episodes"),
            ticks=integer("ticks"),
            durations_ns=tuple(
                item
                if isinstance(item, int) and not isinstance(item, bool)
                else (_raise_type("duration"))
                for item in durations
            ),
            median_ns=integer("median_ns"),
            p95_ns=integer("p95_ns"),
            python_version=text("python_version"),
            platform=text("platform"),
            production_claim=production_claim,
            p99_ns=(integer("p99_ns") if "p99_ns" in value else None),
            minimum_sample_ns=(integer("minimum_sample_ns") if "minimum_sample_ns" in value else 1),
            clock=(text("clock") if "clock" in value else "unknown-legacy-clock"),
            publishable=publishable,
        )
        report.verify(expected_sha256)
        return report

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "benchmark": self.benchmark,
            "workload_id": self.workload_id,
            "workload_version": self.workload_version,
            "workload_sha256": self.workload_sha256,
            "run_sha256": self.run_sha256,
            "backend_id": self.backend_id,
            "engine_version": self.engine_version,
            "protocol_version": self.protocol_version,
            "batch_size": self.batch_size,
            "repeats": self.repeats,
            "episodes": self.episodes,
            "ticks": self.ticks,
            "durations_ns": self.durations_ns,
            "median_ns": self.median_ns,
            "p95_ns": self.p95_ns,
            "python_version": self.python_version,
            "platform": self.platform,
            "production_claim": self.production_claim,
        }
        if self.schema_version == REFERENCE_WORKLOAD_BENCHMARK_SCHEMA:
            payload.update(
                p99_ns=self.p99_ns,
                minimum_sample_ns=self.minimum_sample_ns,
                clock=self.clock,
                publishable=self.publishable,
            )
        result = to_json_value(payload)
        assert isinstance(result, dict)
        return result

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())

    def verify(self, expected_sha256: str | None = None) -> None:
        if expected_sha256 is not None:
            if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
                raise ValueError("expected digest must be a lowercase SHA-256")
            if self.sha256 != expected_sha256:
                raise ValueError("reference workload benchmark digest mismatch")


def _raise_type(field_name: str) -> int:
    raise ValueError(f"{field_name} must be an integer")


def _percentile(values: tuple[int, ...], quantile: float) -> int:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def _default_timer() -> int:
    return time.perf_counter_ns()


def _validate_timer_reading(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timer readings must be integer nanoseconds")


def _requests(count: int) -> tuple[SimulationRequest, ...]:
    ruleset = RulesetRef("arena-hero", "platform-placeholder", _RULES_SHA256)
    return tuple(
        SimulationRequest(
            request_id=f"request-{index}",
            episode_id=f"episode-{index}",
            config=SimulatorConfig(
                backend_id="reference-placeholder",
                engine_version="0.1.0-placeholder",
                ruleset=ruleset,
                seed=index,
                max_ticks=1,
                protocol_version="arena.sim.v1",
            ),
            initial_state_sha256=_STATE_SHA256,
            contestant_ids=("contestant-a", "contestant-b"),
        )
        for index in range(count)
    )


def run_contract_dispatch_microbenchmark(
    *, episodes: int = 10_000, repeats: int = 5, batch_size: int = 256
) -> MicrobenchmarkReport:
    """Measure contract validation and placeholder batch dispatch, not engine throughput."""

    if episodes < 1 or repeats < 1 or batch_size < 1:
        raise ValueError("episodes, repeats, and batch_size must be positive")
    registry = BackendRegistry()
    backend = ReferenceBackendPlaceholder()
    registry.register(backend)
    requests = _requests(episodes)
    durations: list[int] = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        for offset in range(0, episodes, batch_size):
            registry.simulate_batch(requests[offset : offset + batch_size])
        durations.append(time.perf_counter_ns() - started)
    duration_tuple = tuple(durations)
    median_ns = int(statistics.median(duration_tuple))
    throughput = episodes / (median_ns / 1_000_000_000)
    return MicrobenchmarkReport(
        schema_version=MICROBENCHMARK_SCHEMA,
        benchmark="contract-validation-and-placeholder-batch-dispatch",
        backend_id=backend.descriptor.backend_id,
        engine_version=backend.descriptor.engine_version,
        episodes_per_repeat=episodes,
        repeats=repeats,
        batch_size=batch_size,
        durations_ns=duration_tuple,
        median_ns=median_ns,
        p95_ns=_percentile(duration_tuple, 0.95),
        episodes_per_second=throughput,
        python_version=platform.python_version(),
        platform=f"{platform.system()}-{platform.machine()}",
        production_claim=False,
    )


def run_reference_workload_benchmark(
    *, batch_size: int = 9, repeats: int = 5
) -> ReferenceWorkloadBenchmarkReport:
    """Run publishable local evidence with the fixed perf_counter_ns clock."""

    return _run_reference_workload_benchmark(
        batch_size=batch_size, repeats=repeats, timer=_default_timer, publishable=True
    )


def _run_reference_workload_benchmark_for_testing(
    *, batch_size: int = 9, repeats: int = 5, timer: Callable[[], int]
) -> ReferenceWorkloadBenchmarkReport:
    """Private deterministic-clock helper; evidence is always non-publishable."""

    return _run_reference_workload_benchmark(
        batch_size=batch_size, repeats=repeats, timer=timer, publishable=False
    )


def _run_reference_workload_benchmark(
    *, batch_size: int, repeats: int, timer: Callable[[], int], publishable: bool
) -> ReferenceWorkloadBenchmarkReport:
    """Measure one canonical reference workload per round through the real engine.

    Each round executes the frozen canonical reference workload end to end,
    including known-answer verification and the semantic run digest. Raw integer
    wall-clock durations are retained for every round. The report never claims
    production performance.
    """

    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    clock = timer
    durations: list[int] = []
    run: WorkloadRun | None = None
    for _ in range(repeats):
        started = clock()
        _validate_timer_reading(started)
        current = run_canonical_reference_workload(batch_size=batch_size)
        finished = clock()
        _validate_timer_reading(finished)
        if finished < started:
            raise ValueError("timer readings must not go backwards")
        durations.append(finished - started)
        run = current
    assert run is not None
    duration_tuple = tuple(durations)
    return ReferenceWorkloadBenchmarkReport(
        schema_version=REFERENCE_WORKLOAD_BENCHMARK_SCHEMA,
        benchmark="reference-workload",
        workload_id=run.workload_id,
        workload_version=run.workload_version,
        workload_sha256=run.manifest_sha256,
        run_sha256=run.sha256,
        backend_id=run.backend.backend_id,
        engine_version=run.backend.engine_version,
        protocol_version=run.backend.protocol_version,
        batch_size=batch_size,
        repeats=repeats,
        episodes=len(run.episodes),
        ticks=sum(episode.ticks_completed for episode in run.episodes),
        durations_ns=duration_tuple,
        median_ns=int(statistics.median(duration_tuple)),
        p95_ns=_percentile(duration_tuple, 0.95),
        python_version=platform.python_version(),
        platform=f"{platform.system()}-{platform.machine()}",
        production_claim=False,
        p99_ns=_percentile(duration_tuple, 0.99),
        minimum_sample_ns=REFERENCE_WORKLOAD_MINIMUM_SAMPLE_NS,
        clock="perf_counter_ns" if publishable else "injected-test-clock",
        publishable=publishable,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        choices=("contract-dispatch", "reference-workload"),
        default="contract-dispatch",
        help="benchmark to run (default: contract-dispatch)",
    )
    parser.add_argument("--episodes", type=int, default=None)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.benchmark == "reference-workload":
        if args.episodes is not None:
            parser.error(
                "--episodes applies only to contract-dispatch; a reference-workload round "
                "is the frozen canonical workload"
            )
        batch_size = 9 if args.batch_size is None else args.batch_size
        report = run_reference_workload_benchmark(batch_size=batch_size, repeats=args.repeats)
    else:
        episodes = 10_000 if args.episodes is None else args.episodes
        batch_size = 256 if args.batch_size is None else args.batch_size
        report = run_contract_dispatch_microbenchmark(
            episodes=episodes,
            repeats=args.repeats,
            batch_size=batch_size,
        )
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
