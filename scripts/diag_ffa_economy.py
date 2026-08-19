"""FFA economy diagnostic: per-tick worker/cargo/core trace for one match.

Prints a compact per-tick line for the python contestant in a 2-player match
against WaitStrategy: core resources/migration, harvest/deposit stats, and each
unit's uid/role/position/cargo. Useful to spot deposit stalls, terrain-trap
mis-kills, and long worker round-trips without instrumenting the engine.

Usage (from the arena-hero-lab repo root):
    uv run python scripts/diag_ffa_economy.py --seed 7 --ticks 70
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, cast

from arena_hero_sim.ffa import WaitStrategy, run_ffa
from arena_hero_sim.ffa.python_agent_shim import PythonAgentStrategy

DEFAULT_SEED = 7
DEFAULT_TICKS = 70


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    args = parser.parse_args()

    strategy = PythonAgentStrategy(
        movement_guard=True,
        economy_budget=True,
        raid_quota=True,
        economy_expansion=True,
        exploration_v2=True,
        respawn_recovery=True,
    )
    try:
        report = run_ffa(
            {"python": strategy, "wait": WaitStrategy()},
            seed=args.seed,
            ticks=args.ticks,
        )
    finally:
        strategy.close()

    python_cid = "python"
    for frame in report.trace:
        tick = cast(int, frame["tick"])
        players = cast(dict[str, Any], frame["players"])
        player = players.get(python_cid, {})
        core = player.get("core") or {}
        resources = core.get("resources")
        core_pos = tuple(core["pos"]) if core else None
        migration = core.get("migration")
        units = player.get("units") or []
        unit_desc = " ".join(
            f"{u['uid']}:{u['utype'][0]}@{tuple(u['pos'])}c{u.get('cargo', 0)}" for u in units
        )
        stats = player.get("stats") or {}
        print(
            f"tick={tick:3d} res={resources} core={core_pos} mig={migration} "
            f"harvested={stats.get('harvested')} deposited={stats.get('deposited')} | {unit_desc}"
        )

    terminal = next(t for t in report.terminal if t.contestant_id == python_cid)
    print("\nterminal stats:", terminal.stats)
    print("final_resources:", terminal.final_resources, "cargo_final:", terminal.cargo_final)
    print("strategy_errors:", terminal.strategy_errors, terminal.strategy_last_error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
