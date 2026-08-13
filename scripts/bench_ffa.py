"""FFA head-to-head benchmark: python agent vs evolve champion vs baselines.

Deterministic, content-addressed; prints per-seed terminal and aggregate.
Usage:
    uv run python scripts/bench_ffa.py --seeds 0 1 2 --ticks 500
"""
from __future__ import annotations

import argparse
import json

from arena_hero_sim.ffa.contestants import RandomBot, WaitStrategy
from arena_hero_sim.ffa.evolve_shim import EvolveHeuristicStrategy
from arena_hero_sim.ffa.orchestrator import run_ffa
from arena_hero_sim.ffa.python_agent_shim import PythonAgentStrategy


def build_contestants(with_python: bool = True, with_evolve: bool = True):
    out: dict[str, object] = {}
    if with_python:
        out["python"] = PythonAgentStrategy()
    if with_evolve:
        out["evolve"] = EvolveHeuristicStrategy()
    out["rand"] = RandomBot()
    out["wait"] = WaitStrategy()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--ticks", type=int, default=500)
    ap.add_argument("--no-python", action="store_true")
    ap.add_argument("--no-evolve", action="store_true")
    args = ap.parse_args()

    rows = []
    for seed in args.seeds:
        rep = run_ffa(
            build_contestants(not args.no_python, not args.no_evolve),
            seed=seed,
            ticks=args.ticks,
        )
        print(f"seed={seed} sha={rep.artifact_sha256[:16]}")
        for t in rep.terminal:
            print(
                f"  {t.contestant_id:8s} alive={int(t.survival_alive)} hp={t.core_hp} "
                f"res={t.final_resources:3d} pop={t.population_final:2d} "
                f"growth={t.resource_growth:+3d} "
                f"harvest={t.stats.get('harvested', 0)} dmg={t.stats.get('damage_dealt', 0)}"
            )
            rows.append((seed, t.contestant_id, t.survival_alive, t.final_resources,
                         t.population_final, t.resource_growth, t.stats.get("harvested", 0)))

    # aggregate
    print("\naggregate (mean over seeds)")
    ids = sorted({r[1] for r in rows})
    for cid in ids:
        sub = [r for r in rows if r[1] == cid]
        n = len(sub)
        print(
            f"  {cid:8s} alive={sum(r[2] for r in sub)}/{n} "
            f"res={sum(r[3] for r in sub)/n:.1f} pop={sum(r[4] for r in sub)/n:.1f} "
            f"growth={sum(r[5] for r in sub)/n:.1f} harvest={sum(r[6] for r in sub)/n:.1f}"
        )


if __name__ == "__main__":
    main()
