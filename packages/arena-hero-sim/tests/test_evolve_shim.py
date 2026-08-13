"""Acceptance for wiring the evolve champion heuristic into the FFA host.

The evolve heuristic is stateful (multi-tick memory + A* cache), so the shim
wraps one HeuristicStrategy instance and forwards each tick's observation. These
tests pin the two things that matter for a comparison contestant: it runs inside
``run_ffa`` without erroring into the WAIT-bot fallback, and it produces a real,
deterministic terminal row with decisions actually exercised.
"""

from __future__ import annotations

from arena_hero_sim.ffa import RandomBot, run_ffa
from arena_hero_sim.ffa.evolve_shim import EvolveHeuristicStrategy


def _smoke(ticks: int = 50):
    return run_ffa(
        {"evolve": EvolveHeuristicStrategy(), "rand": RandomBot()},
        seed=7,
        ticks=ticks,
    )


def test_evolve_shim_loads_v7_best_genes() -> None:
    strategy = EvolveHeuristicStrategy()

    # evolve_v7_best.json is a flat 27-gene dict and the shim surfaces it as-is.
    assert len(strategy.genes) == 27
    assert strategy.genes["worker_ratio"] == 0.5463523563273454
    assert strategy.genes["max_population"] == 30.0


def test_evolve_shim_run_is_deterministic() -> None:
    first = _smoke()
    second = _smoke()

    assert first.artifact_sha256 == second.artifact_sha256
    assert first.artifact == second.artifact
    assert first.terminal == second.terminal


def test_evolve_shim_smoke_produces_real_terminal_row() -> None:
    report = _smoke()

    assert report.contestant_ids == ("evolve", "rand")
    assert len(report.terminal) == 2
    by_id = {entry.contestant_id: entry for entry in report.terminal}

    evolve = by_id["evolve"]
    # Survived the match and actually played: it harvested resources and grew
    # population, which the WAIT-bot fallback (or a crashed decide) never does.
    assert evolve.survival_alive is True
    assert evolve.stats["harvested"] >= 1
    assert evolve.unit_count_final >= 2
    assert evolve.final_resources >= 0
