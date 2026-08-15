"""Phase 3 mega-seeds study: scaled lab battery with bootstrap CI.

Runs the 4 lab scenarios (256x256 / 2000t) over many seeds and reports the
composite ranking with bootstrap 95% bands.  This is the local stop-gap for the
100+ seed distributed run (``scripts/distributed_battery.py``) while compute
nodes are prepared.

Usage:
    python scripts/mega_seeds_study.py --seeds $(seq 0 29) --workers 8
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed

from arena_hero_sim.ffa.leaderboard import (
    ScenarioPreset,
    aggregate_leaderboard,
    bootstrap_leaderboard,
    rank_metrics,
    terminal_metrics,
)
from arena_hero_sim.ffa.orchestrator import run_ffa
from arena_hero_sim.ffa.public_contestants import (
    PUBLIC_ROSTER,
    build_public_leaderboard_contestants,
)


def _lab_scenarios() -> tuple[ScenarioPreset, ...]:
    from arena_hero_sim.ffa.leaderboard import SCENARIOS

    return tuple(s for s in SCENARIOS if s.size == 256)


def _run_one(args: tuple[ScenarioPreset, int]) -> list[dict[str, float | str]]:
    scenario, seed = args
    contestants, sdk = build_public_leaderboard_contestants()
    try:
        report = run_ffa(
            contestants,
            seed=seed,
            ticks=scenario.ticks,
            size=scenario.size,
            obstacle_density=scenario.obstacle_density,
            spawn_center=scenario.spawn_center,
            resource_scale=scenario.resource_scale,
            resource_replenish_every=scenario.resource_replenish_every,
            respawn_style=scenario.respawn_style,
        )
    finally:
        for strategy in sdk:
            strategy.close()
    return rank_metrics(
        [{**terminal_metrics(t), "contestant": t.contestant_id} for t in report.terminal]
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, nargs="+", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    scenarios = _lab_scenarios()
    roster = list(PUBLIC_ROSTER)

    work = [(scenario, seed) for scenario in scenarios for seed in args.seeds]
    total = len(work)
    print(
        f"[mega] {len(scenarios)} lab scenarios x {len(args.seeds)} seeds = {total} "
        f"matches, workers={args.workers}",
        flush=True,
    )

    match_groups: list[list[dict[str, float | str]]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_run_one, w) for w in work]
        for i, future in enumerate(as_completed(futures), start=1):
            match_groups.append(future.result())
            if i % 20 == 0 or i == total:
                print(f"[mega] {i}/{total} done", flush=True)

    records = [record for group in match_groups for record in group]
    rows = aggregate_leaderboard(records, roster)
    bands = bootstrap_leaderboard(match_groups, roster)

    ordered = sorted(rows, key=lambda r: -r.composite)
    print("\n=== scaled lab composite (bootstrap 95% CI) ===")
    print(f"{'rank':>4} | {'agent':>9} | {'composite':>9} | {'CI':>17} | {'wins':>5}")
    print("-" * 52)
    for position, row in enumerate(ordered, start=1):
        band = bands.get(row.contestant, {}).get("composite", [float("nan"), float("nan")])
        print(
            f"{position:>4} | {row.contestant:>9} | {row.composite:>9.3f} | "
            f"[{band[0]:.3f},{band[1]:.3f}] | {row.wins:>5}/{row.matches}"
        )

    if args.out:
        payload = {
            "scenarios": [s.id for s in scenarios],
            "seeds": args.seeds,
            "roster": roster,
            "leaderboard": [
                {
                    "contestant": r.contestant,
                    "composite": round(r.composite, 4),
                    "mean_rank": round(r.mean_rank, 3),
                    "wins": r.wins,
                    "matches": r.matches,
                    "bootstrap": bands.get(r.contestant, {}),
                }
                for r in ordered
            ],
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
