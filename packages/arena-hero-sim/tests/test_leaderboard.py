"""Unit tests for the fair public leaderboard ranking and composite scoring."""

from __future__ import annotations

from arena_hero_sim.ffa.leaderboard import (
    RANK_CHAIN,
    aggregate_leaderboard,
    rank_metrics,
    terminal_metrics,
)
from arena_hero_sim.ffa.orchestrator import FfaTerminal


def _terminal(
    contestant: str, *, alive: bool = True, kills: int = 0, res: int = 0, pop: int = 1
) -> FfaTerminal:
    return FfaTerminal(
        contestant_id=contestant,
        survival_alive=alive,
        core_hp=5 if alive else 0,
        core_shield=0,
        final_resources=res,
        resource_growth=res - 5,
        population_final=pop,
        unit_count_final=pop,
        cargo_final=0,
        respawn_count=0,
        ticks_alive=0,
        stats={"core_kills": kills, "deposited": 0, "harvested": 0, "damage_dealt": 0},
    )


def test_rank_metrics_orders_by_chain_and_ties_share_rank() -> None:
    entries = [
        terminal_metrics(_terminal("a", alive=False, kills=0, res=0)),
        terminal_metrics(_terminal("b", alive=True, kills=0, res=10)),
        terminal_metrics(_terminal("c", alive=True, kills=0, res=10)),
        terminal_metrics(_terminal("d", alive=True, kills=1, res=0)),
    ]
    for entry, cid in zip(entries, ("a", "b", "c", "d"), strict=True):
        entry["contestant"] = cid

    ranked = rank_metrics(entries)
    by_id = {m["contestant"]: m for m in ranked}
    assert by_id["d"]["rank"] == 1.0  # only survivor-killer wins
    assert by_id["b"]["rank"] == 2.0  # equal resources share rank
    assert by_id["c"]["rank"] == 2.0
    assert by_id["a"]["rank"] == 4.0  # dead ranks last


def test_aggregate_leaderboard_puts_dominant_contestant_first() -> None:
    roster = ("weak", "strong")
    records = []
    for _ in range(5):
        strong = terminal_metrics(_terminal("strong", alive=True, kills=2, res=100, pop=20))
        weak = terminal_metrics(_terminal("weak", alive=True, kills=0, res=5, pop=1))
        strong["contestant"] = "strong"
        weak["contestant"] = "weak"
        records.extend(rank_metrics([strong, weak]))

    rows = aggregate_leaderboard(records, roster)
    assert rows[0].contestant == "strong"
    assert rows[0].composite > rows[1].composite
    assert all(row.matches == 5 for row in rows)


def test_terminal_metrics_reads_kills_and_deposits_from_stats() -> None:
    entry = _terminal("x", alive=True, kills=3, res=40)
    metrics = terminal_metrics(entry)
    assert metrics["core_kills"] == 3.0
    assert metrics["deposited"] == 0.0
    assert metrics["survival_alive"] == 1.0
    assert set(RANK_CHAIN).issubset(metrics)
