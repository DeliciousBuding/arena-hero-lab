"""Executable local microbenchmarks for platform overhead and the real reference engine."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from arena_hero_sim.contracts import RulesetRef, SimulationRequest, SimulatorConfig
from arena_hero_sim.reference import ReferenceBackendPlaceholder
from arena_hero_sim.reference_workload import WorkloadRun, run_canonical_reference_workload
from arena_hero_sim.registry import BackendRegistry
from arena_hero_sim.serialization import JsonValue, to_json_value

MICROBENCHMARK_SCHEMA = "arena.sim.microbenchmark.v1"
REFERENCE_WORKLOAD_BENCHMARK_SCHEMA = "arena.sim.reference-workload-benchmark.v1"
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

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["durations_ns"] = list(self.durations_ns)
        return value


@dataclass(frozen=True, slots=True)
class ReferenceWorkloadBenchmarkReport:
    """Versioned evidence for one canonical reference-workload measurement.

    A round is exactly one execution of the frozen canonical reference workload.
    The episode count is fixed by the workload manifest and is recorded, never
    scaled by a caller-supplied count. Reports always set production_claim=False.
    """

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

    def __post_init__(self) -> None:
        object.__setattr__(self, "durations_ns", tuple(self.durations_ns))
        if self.production_claim is not False:
            raise ValueError(
                "reference workload benchmark reports cannot claim production performance"
            )

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
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
                "durations_ns": list(self.durations_ns),
                "median_ns": self.median_ns,
                "p95_ns": self.p95_ns,
                "python_version": self.python_version,
                "platform": self.platform,
                "production_claim": self.production_claim,
            }
        )
        assert isinstance(value, dict)
        return value


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
    *,
    batch_size: int = 9,
    repeats: int = 5,
    timer: Callable[[], int] | None = None,
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
    clock = _default_timer if timer is None else timer
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
