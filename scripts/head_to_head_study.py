"""Phase 2b: 1v1 head-to-head vs FFA-ranking consistency.

Question: does the 9-player FFA composite ranking predict 1v1 duel strength?
Run all C(7,2)=21 unordered pairs of third-party agents head-to-head over the
lab scenarios, build a 1v1 win-rate ranking, and report the Spearman rank
correlation against the pooled FFA ordering.

Usage:
    python scripts/head_to_head_study.py --seeds 0 1 2 --workers 6
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import combinations

from arena_hero_sim.ffa.leaderboard import (
    ScenarioPreset,
    rank_metrics,
    terminal_metrics,
)
from arena_hero_sim.ffa.orchestrator import run_ffa
from arena_hero_sim.ffa.public_contestants import (
    PUBLIC_ROSTER,
    build_public_leaderboard_contestants,
)

THIRD_PARTY_IDS: tuple[str, ...] = tuple(c for c in PUBLIC_ROSTER if c not in ("rand", "wait"))
PAIRS: tuple[tuple[str, str], ...] = tuple(combinations(THIRD_PARTY_IDS, 2))

# Lab-only FFA ordering from Phase 2a (ecosystem_study.py pilot).
FFA_RANK_LAB: tuple[str, ...] = (
    "waaiging",
    "massarmy",
    "evolve",
    "tactic",
    "guide",
    "drew-z",
    "wuwd",
)


def _lab_scenarios() -> tuple[ScenarioPreset, ...]:
    from arena_hero_sim.ffa.leaderboard import SCENARIOS

    return tuple(s for s in SCENARIOS if s.size == 256)


def _run_one(
    args: tuple[ScenarioPreset, tuple[str, str], int],
) -> tuple[str, str, str | None]:
    scenario, (left, right), seed = args
    contestants, sdk = build_public_leaderboard_contestants()
    contestants = {cid: contestants[cid] for cid in (left, right)}
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
    ranked = rank_metrics(
        [{**terminal_metrics(t), "contestant": t.contestant_id} for t in report.terminal]
    )
    top = [str(r["contestant"]) for r in ranked if r["rank"] == 1.0]
    return left, right, top[0] if len(top) == 1 else None


def _spearman(order_a: list[str], order_b: list[str]) -> float:
    ids = list(order_a)
    rank_a = {cid: i for i, cid in enumerate(order_a)}
    rank_b = {cid: i for i, cid in enumerate(order_b)}
    n = len(ids)
    d2 = sum((rank_a[cid] - rank_b[cid]) ** 2 for cid in ids)
    return 1.0 - 6.0 * d2 / (n * (n * n - 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    scenarios = _lab_scenarios()

    work = [
        (scenario, pair, seed) for scenario in scenarios for pair in PAIRS for seed in args.seeds
    ]
    total = len(work)
    print(
        f"[1v1] {len(scenarios)} scenarios x {len(PAIRS)} pairs x {len(args.seeds)} seeds "
        f"= {total} duels, workers={args.workers}",
        flush=True,
    )

    # points per agent: win=1, draw=0.5, loss=0
    points: dict[str, float] = {cid: 0.0 for cid in THIRD_PARTY_IDS}
    matches: dict[str, int] = {cid: 0 for cid in THIRD_PARTY_IDS}
    draws = 0

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_run_one, w) for w in work]
        for i, future in enumerate(as_completed(futures), start=1):
            left, right, winner = future.result()
            matches[left] += 1
            matches[right] += 1
            if winner is None:
                draws += 1
                points[left] += 0.5
                points[right] += 0.5
            else:
                points[winner] += 1.0
            if i % 40 == 0 or i == total:
                print(f"[1v1] {i}/{total} done", flush=True)

    scored = [
        (cid, round(points[cid] / matches[cid], 3), matches[cid], points[cid])
        for cid in THIRD_PARTY_IDS
    ]
    scored.sort(key=lambda x: (-x[1], -x[3]))
    duel_order = [cid for cid, _, _, _ in scored]

    print(f"\n=== 1v1 duel ranking ({total} duels, draws={draws}) ===")
    print(f"{'rank':>4} | {'agent':>9} | {'win-rate':>8} | duels")
    print("-" * 42)
    for rank, (cid, rate, count, _pts) in enumerate(scored, start=1):
        print(f"{rank:>4} | {cid:>9} | {rate:>8.3f} | {count}")

    ffa = list(FFA_RANK_LAB)
    rho = _spearman(ffa, duel_order)
    print("\n=== FFA (lab) vs 1v1 ordering ===")
    print(f"{'rank':>4} | {'FFA-lab':>9} | {'1v1':>9}")
    print("-" * 28)
    for rank in range(len(ffa)):
        print(f"{rank + 1:>4} | {ffa[rank]:>9} | {duel_order[rank]:>9}")
    print(f"\nSpearman rho = {rho:+.3f} (FFA-lab vs 1v1)")


if __name__ == "__main__":
    main()
