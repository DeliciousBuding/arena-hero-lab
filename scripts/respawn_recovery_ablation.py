"""Paired offline ablation for the production respawn recovery layer.

This is an opt-in research command.  It runs the same seed and world twice,
changing only the recovery switch, and emits a content-addressed JSON result.
It never connects to a live server or writes production runtime data.
"""

from __future__ import annotations

import argparse
import json
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from arena_hero_sim.ffa.contestants import HunterBot
from arena_hero_sim.ffa.orchestrator import FfaReport, run_ffa
from arena_hero_sim.ffa.python_agent_shim import PythonAgentStrategy
from arena_hero_sim.ffa.strategy import Strategy
from arena_hero_sim.serialization import content_sha256, to_json_value


def _contestants(recovery_enabled: bool) -> dict[str, Strategy]:
    return {
        "python+production": PythonAgentStrategy(
            exploration_v2=True,
            economy_expansion=True,
            respawn_recovery=recovery_enabled,
        ),
        "hunter": HunterBot(),
    }


def _recovery_metrics(report: FfaReport, contestant_id: str) -> dict[str, Any]:
    frames = [cast(dict[str, Any], frame["players"])[contestant_id] for frame in report.trace]
    respawn_ticks: list[int] = []
    for tick, (previous, current) in enumerate(pairwise(frames), start=1):
        previous_core = previous["core"]
        current_core = current["core"]
        if previous_core is None or current_core is None:
            continue
        position_changed = previous_core["pos"] != current_core["pos"]
        population_reset = int(current["population"]) <= 2
        if position_changed and population_reset:
            respawn_ticks.append(tick)

    first_respawn = respawn_ticks[0] if respawn_ticks else None
    recovered_at = None
    if first_respawn is not None:
        for tick, frame in enumerate(frames[first_respawn:], start=first_respawn):
            if int(frame["population"]) >= 20:
                recovered_at = tick
                break

    terminal = next(item for item in report.terminal if item.contestant_id == contestant_id)
    return {
        "respawn_count": terminal.respawn_count,
        "detected_respawn_ticks": respawn_ticks,
        "first_respawn_tick": first_respawn,
        "population_recovery_tick": recovered_at,
        "final_population": terminal.population_final,
        "final_resources": terminal.final_resources,
        "resource_growth": terminal.resource_growth,
        "survival_alive": terminal.survival_alive,
        "stats": dict(sorted(terminal.stats.items())),
    }


def run_paired_ablation(
    *,
    seeds: list[int],
    ticks: int,
    size: int = 512,
    obstacle_density: float = 0.225,
    resource_scale: float = 0.25,
) -> dict[str, Any]:
    """Run paired recovery OFF/ON reports and return a canonical result tree."""

    rows: list[dict[str, Any]] = []
    for seed in seeds:
        common: dict[str, Any] = {
            "seed": seed,
            "ticks": ticks,
            "size": size,
            "obstacle_density": obstacle_density,
            "resource_scale": resource_scale,
            "resource_replenish_every": 0,
            "respawn_style": "barren",
        }
        for recovery_enabled in (False, True):
            report = run_ffa(
                _contestants(recovery_enabled),
                **common,
            )
            rows.append(
                {
                    **common,
                    "recovery_enabled": recovery_enabled,
                    "report_sha256": report.artifact_sha256,
                    "metrics": _recovery_metrics(report, "python+production"),
                }
            )

    artifact = {
        "schema_version": "arena.research.respawn-recovery-ablation.v1",
        "parameters": {
            "seeds": seeds,
            "ticks": ticks,
            "size": size,
            "obstacle_density": obstacle_density,
            "resource_scale": resource_scale,
        },
        "rows": rows,
    }
    canonical_artifact = to_json_value(artifact)
    return {
        **artifact,
        "artifact_sha256": content_sha256(canonical_artifact),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--ticks", type=int, default=4000)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if any(seed < 0 for seed in args.seeds) or args.ticks < 1:
        parser.error("seeds must be non-negative and ticks must be positive")
    result = run_paired_ablation(seeds=args.seeds, ticks=args.ticks)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(args.output)


if __name__ == "__main__":
    main()
