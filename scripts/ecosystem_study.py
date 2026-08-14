"""Phase 2a ecosystem-validity study: do the control bots distort the ranking?

The public battery pools rand/wait control bots with the third parties ("同池
一条龙").  This study re-runs the lab scenarios WITHOUT the two controls and
compares the third-party ordering against the pooled baseline, testing whether
the 0-baseline controls materially change who beats whom.

Usage:
    python scripts/ecosystem_study.py --seeds 0 1 2 --workers 4
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed

from arena_hero_sim.ffa.leaderboard import (
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

# The 7 third-party ids (PUBLIC_ROSTER minus the rand/wait controls).
THIRD_PARTY_IDS: tuple[str, ...] = tuple(c for c in PUBLIC_ROSTER if c not in ("rand", "wait"))


def _lab_scenarios() -> tuple[ScenarioPreset, ...]:
    from arena_hero_sim.ffa.leaderboard import SCENARIOS

    return tuple(s for s in SCENARIOS if s.size == 256)


def _run_one(args: tuple[ScenarioPreset, bool, int]) -> tuple[str, bool, int, list[dict]]:
    scenario, with_controls, seed = args
    contestants, sdk = build_public_leaderboard_contestants()
    if not with_controls:
        contestants = {cid: contestants[cid] for cid in THIRD_PARTY_IDS}
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
    return scenario.id, with_controls, seed, ranked


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
    args = ap.parse_args()
    scenarios = _lab_scenarios()

    work = [
        (scenario, with_controls, seed)
        for scenario in scenarios
        for with_controls in (True, False)
        for seed in args.seeds
    ]
    print(
        f"[ecosystem] {len(scenarios)} scenarios x 2 rosters x {len(args.seeds)} seeds "
        f"= {len(work)} matches, workers={args.workers}",
        flush=True,
    )

    by_roster: dict[bool, list[dict]] = {True: [], False: []}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_run_one, w) for w in work]
        for i, future in enumerate(as_completed(futures), start=1):
            _scenario_id, with_controls, _seed, ranked = future.result()
            by_roster[with_controls].extend(ranked)
            if i % 10 == 0 or i == len(work):
                print(f"[ecosystem] {i}/{len(work)} done", flush=True)

    pooled = _leaderboard(by_roster[True], PUBLIC_ROSTER)
    solo = _leaderboard(by_roster[False], THIRD_PARTY_IDS)

    print("\n=== ecosystem validity (composite) ===")
    print("rank |  pooled (9, with controls) |  third-party-only (7)")
    print("-" * 56)
    for rank in range(len(THIRD_PARTY_IDS)):
        pr = pooled[rank]
        sr = solo[rank]
        print(
            f"{rank + 1:>4} |  {pr['contestant']:>8} {pr['composite']:.3f} |  {sr['contestant']:>8} {sr['composite']:.3f}"
        )

    print("\n=== ordering shift ===")
    pooled_order = [r["contestant"] for r in pooled if r["contestant"] in THIRD_PARTY_IDS]
    solo_order = [r["contestant"] for r in solo]
    for a, b in zip(pooled_order, solo_order, strict=True):
        marker = " " if a == b else " <->"
        print(f"  {a:>9} {marker} {b}")


if __name__ == "__main__":
    main()
