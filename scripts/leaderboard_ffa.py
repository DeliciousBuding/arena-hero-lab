"""Public third-party leaderboard: large FFA across scenarios and seeds.

Runs only the public roster (evolve / drew-z / guide / waaiging / tactic + rand /
wait); our own python and hunter contestants are excluded.  Deterministic and
content-addressed per seed; writes an aggregate leaderboard JSON.

Usage:
    uv run python scripts/leaderboard_ffa.py --seeds 0 1 2 --ticks 500
    uv run python scripts/leaderboard_ffa.py --maze --ticks 2000 --seeds 0
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from arena_hero_sim.ffa.orchestrator import FfaTerminal, run_ffa
from arena_hero_sim.ffa.public_contestants import (
    PUBLIC_ROSTER,
    build_public_leaderboard_contestants,
)

LEADERBOARD_SCHEMA = "arena.leaderboard.public.v1"


@dataclass(frozen=True, slots=True)
class RankedEntry:
    rank: int
    contestant_id: str
    survival_alive: float
    final_resources: float
    population_final: float
    harvested: float
    damage_dealt: float

    def to_json(self) -> dict:
        return {
            "rank": self.rank,
            "contestant": self.contestant_id,
            "survival_alive": round(self.survival_alive, 4),
            "final_resources": round(self.final_resources, 2),
            "population_final": round(self.population_final, 2),
            "harvested": round(self.harvested, 2),
            "damage_dealt": round(self.damage_dealt, 2),
        }


def _terminal_agg(entries: list[FfaTerminal]) -> dict[str, float]:
    n = len(entries)
    return {
        "survival_alive": sum(1 for e in entries if e.survival_alive) / n,
        "final_resources": sum(e.final_resources for e in entries) / n,
        "population_final": sum(e.population_final for e in entries) / n,
        "harvested": sum(e.stats.get("harvested", 0) for e in entries) / n,
        "damage_dealt": sum(e.stats.get("damage_dealt", 0) for e in entries) / n,
    }


def _rank(entries: list[RankedEntry]) -> list[RankedEntry]:
    def key(e: RankedEntry):
        return (
            e.survival_alive,
            e.final_resources,
            e.population_final,
            e.harvested,
            e.damage_dealt,
        )

    ordered = sorted(entries, key=key, reverse=True)
    ranked: list[RankedEntry] = []
    for index, entry in enumerate(ordered):
        rank = ranked[-1].rank if index and key(entry) == key(ordered[index - 1]) else index + 1
        ranked.append(
            RankedEntry(
                rank=rank,
                contestant_id=entry.contestant_id,
                survival_alive=entry.survival_alive,
                final_resources=entry.final_resources,
                population_final=entry.population_final,
                harvested=entry.harvested,
                damage_dealt=entry.damage_dealt,
            )
        )
    return ranked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--ticks", type=int, default=500)
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--density", type=float, default=0.225)
    ap.add_argument("--spawn", type=int, nargs=2, default=(0, 0), metavar=("X", "Y"))
    ap.add_argument("--maze", action="store_true", help="t1 maze stress preset")
    ap.add_argument("--remote", action="store_true", help="far-ring spawn preset")
    ap.add_argument("--out", type=Path, default=None, help="write aggregate leaderboard JSON")
    args = ap.parse_args()

    density = args.density
    spawn = tuple(args.spawn)
    if args.maze:
        density = 0.5
        spawn = (-96, 128)
    if args.remote:
        spawn = (-96, 128)

    contestants, sdk_strategies = build_public_leaderboard_contestants()
    per_seed: dict[int, dict] = {}
    agg: dict[str, dict[str, float]] = {}
    try:
        for seed in args.seeds:
            report = run_ffa(
                contestants,
                seed=seed,
                ticks=args.ticks,
                size=args.size,
                obstacle_density=density,
                spawn_center=spawn,
            )
            per_seed[seed] = {"artifact_sha256": report.artifact_sha256}
            print(f"seed={seed} sha={report.artifact_sha256[:16]}")
            for t in report.terminal:
                agg.setdefault(t.contestant_id, {"n": 0})
                agg[t.contestant_id]["n"] += 1
                for k, v in _terminal_agg([t]).items():
                    agg[t.contestant_id][k] = agg[t.contestant_id].get(k, 0.0) + v
                print(
                    f"  {t.contestant_id:8s} alive={int(t.survival_alive)} res={t.final_resources:3d} "
                    f"pop={t.population_final:2d} harvest={t.stats.get('harvested', 0)} "
                    f"dmg={t.stats.get('damage_dealt', 0)}"
                )
    finally:
        for strategy in sdk_strategies:
            strategy.close()

    entries = []
    for contestant_id in PUBLIC_ROSTER:
        cell = agg.get(contestant_id)
        if cell is None:
            continue
        n = cell["n"]
        entries.append(
            RankedEntry(
                rank=0,
                contestant_id=contestant_id,
                survival_alive=cell["survival_alive"] / n,
                final_resources=cell["final_resources"] / n,
                population_final=cell["population_final"] / n,
                harvested=cell["harvested"] / n,
                damage_dealt=cell["damage_dealt"] / n,
            )
        )
    ranked = _rank(entries)

    print("\npublic leaderboard (mean over seeds)")
    for entry in ranked:
        print(
            f"  #{entry.rank:<2} {entry.contestant_id:8s} alive={entry.survival_alive:.2f} "
            f"res={entry.final_resources:6.2f} pop={entry.population_final:5.2f} "
            f"harvest={entry.harvested:6.2f} dmg={entry.damage_dealt:6.2f}"
        )

    if args.out is not None:
        payload = {
            "schema": LEADERBOARD_SCHEMA,
            "roster": list(PUBLIC_ROSTER),
            "params": {
                "seeds": args.seeds,
                "ticks": args.ticks,
                "size": args.size,
                "obstacle_density": density,
                "spawn_center": list(spawn),
            },
            "per_seed": {str(k): v for k, v in per_seed.items()},
            "leaderboard": [entry.to_json() for entry in ranked],
        }
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
