"""FFA head-to-head benchmark: python agent vs evolve champion vs baselines.

Deterministic, content-addressed; prints per-seed terminal and aggregate.
Usage:
    uv run python scripts/bench_ffa.py --seeds 0 1 2 --ticks 500
    uv run python scripts/bench_ffa.py --hunter --no-python --ticks 500
"""

from __future__ import annotations

import argparse

from arena_hero_sim.ffa.contestants import HunterBot, RandomBot, WaitStrategy
from arena_hero_sim.ffa.evolve_shim import EvolveHeuristicStrategy
from arena_hero_sim.ffa.orchestrator import run_ffa
from arena_hero_sim.ffa.python_agent_shim import PythonAgentStrategy


def build_contestants(
    with_python: bool = True,
    with_evolve: bool = True,
    with_python_switches: bool = False,
    with_python_expansion: bool = False,
    with_python_exploration_v2: bool = False,
    with_python_production: bool = False,
    with_python_respawn_recovery: bool = False,
    with_hunter: bool = False,
):
    out: dict[str, object] = {}
    if with_python:
        out["python"] = PythonAgentStrategy()
    if with_python_switches:
        out["python+switches"] = PythonAgentStrategy(
            movement_guard=True,
            economy_budget=True,
            raid_quota=True,
        )
    if with_python_expansion:
        out["python+expansion"] = PythonAgentStrategy(economy_expansion=True)
    if with_python_exploration_v2:
        out["python+exploration-v2"] = PythonAgentStrategy(exploration_v2=True)
    if with_python_production:
        out["python+production"] = PythonAgentStrategy(
            exploration_v2=True,
            economy_expansion=True,
        )
    if with_python_respawn_recovery:
        out["python+production+recovery"] = PythonAgentStrategy(
            exploration_v2=True,
            economy_expansion=True,
            respawn_recovery=True,
        )
    if with_evolve:
        out["evolve"] = EvolveHeuristicStrategy()
    if with_hunter:
        out["hunter"] = HunterBot()
    out["rand"] = RandomBot()
    out["wait"] = WaitStrategy()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--ticks", type=int, default=500)
    ap.add_argument("--no-python", action="store_true")
    ap.add_argument("--no-evolve", action="store_true")
    ap.add_argument(
        "--python-switches",
        action="store_true",
        help="also run python with movement/economy/raid research switches on",
    )
    ap.add_argument(
        "--python-expansion",
        action="store_true",
        help="also run python with the economy-expansion research switch on",
    )
    ap.add_argument(
        "--python-exploration-v2",
        action="store_true",
        help="also run python with the exploration-v2 ring-quota research switch on",
    )
    ap.add_argument(
        "--python-production",
        action="store_true",
        help="also run python with exploration-v2 + economy-expansion (production candidate)",
    )
    ap.add_argument(
        "--python-respawn-recovery",
        action="store_true",
        help="also run python production candidate + respawn-recovery",
    )
    ap.add_argument(
        "--hunter",
        action="store_true",
        help="also run the deterministic HunterBot aggressor",
    )
    ap.add_argument(
        "--size",
        type=int,
        default=256,
        help="world side length (default 256)",
    )
    ap.add_argument(
        "--density",
        type=float,
        default=0.225,
        help="base obstacle density; 0.5 reproduces the t1 maze profile",
    )
    ap.add_argument(
        "--spawn",
        type=int,
        nargs=2,
        default=(0, 0),
        metavar=("X", "Y"),
        help="spawn center (default 0 0); remote spawns reproduce far-ring birth",
    )
    ap.add_argument(
        "--maze",
        action="store_true",
        help="t1 maze stress preset: --density 0.5 --spawn -96 128",
    )
    ap.add_argument(
        "--barren",
        action="store_true",
        help="barren far-respawn preset: size 1024, sparse, no replenish, far respawn",
    )

    args = ap.parse_args()

    density = args.density
    spawn = tuple(args.spawn)
    size = args.size
    resource_scale = 1.0
    resource_replenish_every = 4
    respawn_style = "ring"
    if args.maze:
        density = 0.5
        spawn = (-96, 128)
    if args.barren:
        size = 512
        density = 0.225
        resource_scale = 0.25
        resource_replenish_every = 0
        respawn_style = "barren"

    rows = []
    for seed in args.seeds:
        rep = run_ffa(
            build_contestants(
                not args.no_python,
                not args.no_evolve,
                args.python_switches,
                args.python_expansion,
                args.python_exploration_v2,
                args.python_production,
                args.python_respawn_recovery,
                args.hunter,
            ),
            seed=seed,
            ticks=args.ticks,
            size=size,
            obstacle_density=density,
            spawn_center=spawn,
            resource_scale=resource_scale,
            resource_replenish_every=resource_replenish_every,
            respawn_style=respawn_style,
        )
        print(f"seed={seed} sha={rep.artifact_sha256[:16]}")
        for t in rep.terminal:
            print(
                f"  {t.contestant_id:8s} alive={int(t.survival_alive)} hp={t.core_hp} "
                f"res={t.final_resources:3d} pop={t.population_final:2d} "
                f"growth={t.resource_growth:+3d} "
                f"harvest={t.stats.get('harvested', 0)} dep={t.stats.get('deposited', 0)} "
                f"spawn_cost={t.stats.get('spawn_cost', 0)} respawn={t.respawn_count} "
                f"dmg={t.stats.get('damage_dealt', 0)}"
            )
            rows.append(
                (
                    seed,
                    t.contestant_id,
                    t.survival_alive,
                    t.final_resources,
                    t.population_final,
                    t.resource_growth,
                    t.stats.get("harvested", 0),
                    t.stats.get("deposited", 0),
                    t.stats.get("spawn_cost", 0),
                    t.respawn_count,
                    t.stats.get("damage_dealt", 0),
                )
            )

    # aggregate
    print("\naggregate (mean over seeds)")
    ids = sorted({r[1] for r in rows})
    for cid in ids:
        sub = [r for r in rows if r[1] == cid]
        n = len(sub)
        print(
            f"  {cid:8s} alive={sum(r[2] for r in sub)}/{n} "
            f"res={sum(r[3] for r in sub) / n:.1f} pop={sum(r[4] for r in sub) / n:.1f} "
            f"growth={sum(r[5] for r in sub) / n:.1f} harvest={sum(r[6] for r in sub) / n:.1f} "
            f"dep={sum(r[7] for r in sub) / n:.1f} spawn_cost={sum(r[8] for r in sub) / n:.1f} "
            f"respawn={sum(r[9] for r in sub) / n:.2f} dmg={sum(r[10] for r in sub) / n:.1f}"
        )


if __name__ == "__main__":
    main()
