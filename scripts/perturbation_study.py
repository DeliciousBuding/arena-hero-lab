"""Phase 1 rule-perturbation robustness study (research tool).

Runs the public roster over the lab scenarios under several rule variants and
compares the resulting leaderboards.  The goal is an overfitting probe: if a
ranking is robust to flipping one rule at a time, it is not tuned to that rule.

Variants (see arena_hero_sim.ffa.engine.Engine.rule_variant):
  - baseline       frozen v0.14 (dynamic pricing + free respawn worker)
  - flat-price     dynamic unit pricing disabled
  - paid-respawn   respawn worker charged 5 resources

Usage:
    python scripts/perturbation_study.py --seeds 0 1 2 --scenarios lab
    python scripts/perturbation_study.py --seeds 0 1 2 --workers 4

Outputs a per-variant leaderboard table to stdout.  Does not touch the public
leaderboard artifacts.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed

from arena_hero_sim.ffa.leaderboard import (
    SCENARIOS,
    ScenarioPreset,
    aggregate_leaderboard,
    rank_metrics,
    terminal_metrics,
)
from arena_hero_sim.ffa.orchestrator import run_ffa
from arena_hero_sim.ffa.public_contestants import (
    PUBLIC_ROSTER,
    build_public_leaderboard_contestants,
)

VARIANTS: tuple[tuple[str, str | None], ...] = (
    ("baseline", None),
    ("flat-price", "flat-price"),
    ("paid-respawn", "paid-respawn"),
)


def _lab_scenarios() -> tuple[ScenarioPreset, ...]:
    return tuple(s for s in SCENARIOS if s.size == 256)


def _run_one(args: tuple[ScenarioPreset, str | None, int]) -> tuple[str, str | None, int, list[dict]]:
    scenario, variant, seed = args
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
            rule_variant=variant,
        )
    finally:
        for strategy in sdk:
            strategy.close()
    ranked = rank_metrics(
        [{**terminal_metrics(t), "contestant": t.contestant_id} for t in report.terminal]
    )
    return scenario.id, variant, seed, ranked


def _leaderboard(records: list[dict], roster: tuple[str, ...]) -> list[dict]:
    rows = aggregate_leaderboard(records, roster)
    return sorted(
        [
            {"contestant": r.contestant, "composite": round(r.composite, 3), "wins": r.wins}
            for r in rows
        ],
        key=lambda x: -x["composite"],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--scenarios",
        choices=["lab", "all"],
        default="lab",
        help="lab = 4 fast 256/2000t scenarios; all = full 7-scenario battery (slow)",
    )
    args = ap.parse_args()
    scenarios = SCENARIOS if args.scenarios == "all" else _lab_scenarios()
    roster = PUBLIC_ROSTER

    work = [
        (scenario, variant, seed)
        for scenario in scenarios
        for _name, variant in VARIANTS
        for seed in args.seeds
    ]
    print(
        f"[perturbation] {len(scenarios)} scenarios x {len(VARIANTS)} variants x "
        f"{len(args.seeds)} seeds = {len(work)} matches, workers={args.workers}",
        flush=True,
    )

    by_variant: dict[str | None, list[dict]] = {variant: [] for _name, variant in VARIANTS}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_run_one, w) for w in work]
        for i, future in enumerate(as_completed(futures), start=1):
            _scenario_id, variant, _seed, ranked = future.result()
            by_variant[variant].extend(ranked)
            if i % 10 == 0 or i == len(work):
                print(f"[perturbation] {i}/{len(work)} done", flush=True)

    print("\n=== rule-perturbation leaderboard (composite) ===")
    boards = {}
    for name, variant in VARIANTS:
        boards[name] = _leaderboard(by_variant.get(variant, []), roster)

    header = "rank | " + " | ".join(f"{name:>14}" for name in boards)
    print(header)
    print("-" * len(header))
    for rank in range(len(roster)):
        cells = []
        for name in boards:
            row = boards[name][rank]
            cells.append(f"{row['contestant'][:10]:>10} {row['composite']:.3f}")
        print(f"{rank + 1:>4} | " + " | ".join(cells))

    print("\n=== wins by variant ===")
    for name in boards:
        wins = {r["contestant"]: r["wins"] for r in boards[name]}
        top = sorted(wins.items(), key=lambda x: -x[1])[:3]
        print(f"  {name:>13}: " + ", ".join(f"{c}:{w}" for c, w in top))


if __name__ == "__main__":
    main()
