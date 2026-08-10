"""Executable local microbenchmark for simulator platform overhead."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from arena_hero_sim.contracts import RulesetRef, SimulationRequest, SimulatorConfig
from arena_hero_sim.reference import ReferenceBackendPlaceholder
from arena_hero_sim.registry import BackendRegistry

MICROBENCHMARK_SCHEMA = "arena.sim.microbenchmark.v1"
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


def _percentile(values: tuple[int, ...], quantile: float) -> int:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run_contract_dispatch_microbenchmark(
        episodes=args.episodes,
        repeats=args.repeats,
        batch_size=args.batch_size,
    )
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
