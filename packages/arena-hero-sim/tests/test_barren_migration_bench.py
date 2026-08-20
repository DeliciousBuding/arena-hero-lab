"""Regression tests for scripts/bench_barren_migration.py (pure helpers).

The bench lives in the scripts directory (not a package), so it is imported
via a sys.path entry, mirroring test_state_seed_parser.py.  These tests pin
the ring/axis math against the engine's official quota formula, the chunk
drawing contract (rings 8-16, unique, deterministic), world sizing, the
nearest-EMPTY spiral placement, and the trace metric extraction.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, cast

from arena_hero_sim.ffa.config import EMPTY, resource_quota
from arena_hero_sim.ffa.orchestrator import FfaTerminal
from arena_hero_sim.ffa.world import World

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
bbm = importlib.import_module("bench_barren_migration")


def _terminal(**overrides: Any) -> FfaTerminal:
    fields: dict[str, Any] = {
        "contestant_id": "t1",
        "survival_alive": True,
        "core_hp": 5,
        "core_shield": 5,
        "final_resources": 11,
        "resource_growth": 6,
        "population_final": 2,
        "unit_count_final": 2,
        "cargo_final": 0,
        "respawn_count": 0,
        "ticks_alive": 5,
        "stats": {},
    }
    fields.update(overrides)
    return FfaTerminal(**fields)


def _frame(
    tick: int,
    pos: list[int] | None,
    harvested: int = 0,
    deposited: int = 0,
) -> dict[str, object]:
    core = None if pos is None else {"uid": "c1", "pos": pos, "resources": 5}
    return {
        "tick": tick,
        "players": {
            "t1": {
                "alive": pos is not None,
                "core": core,
                "units": [],
                "population": 1,
                "stats": {"harvested": harvested, "deposited": deposited},
            }
        },
    }


def test_axis_and_ring_match_official_quota_formula() -> None:
    """The script's ring math must agree with the engine's official quota."""
    for cx in range(-24, 25):
        for cy in range(-24, 25):
            ring = bbm._ring(cx, cy)
            expected_quota = max(2, 128 // (8 + ring))
            assert resource_quota(cx, cy) == expected_quota
    # Spot-check the formula shape directly (official: max(2, 128//(8+ring))).
    assert bbm._axis(0) == 0
    assert bbm._axis(3) == 3
    assert bbm._axis(-1) == 0
    assert bbm._axis(-4) == 3
    assert bbm._ring(8, 0) == 8
    assert bbm._ring(-9, -8) == 15
    assert bbm._ring(-9, -9) == 16


def test_choose_tenant_chunks_deterministic_unique_and_in_range() -> None:
    for seed in range(10):
        first = bbm.choose_tenant_chunks(seed, tenant_count=4, ring_min=8, ring_max=16)
        second = bbm.choose_tenant_chunks(seed, tenant_count=4, ring_min=8, ring_max=16)
        assert first == second  # deterministic
        chunks = [chunk for chunk, _ in first]
        assert len(chunks) == 4
        assert len(set(chunks)) == 4  # unique
        for (cx, cy), ring in first:
            assert 8 <= ring <= 16
            assert bbm._ring(cx, cy) == ring


def test_world_size_covers_all_chunk_cells() -> None:
    chunks = [(16, 0), (-17, -1), (8, 8), (0, -16)]
    size = bbm.world_size_for_chunks(chunks)
    assert size % 32 == 0
    offset = size // 2
    for cx, cy in chunks:
        for x in (cx * 32, cx * 32 + 31):
            for y in (cy * 32, cy * 32 + 31):
                assert -offset <= x < offset
                assert -offset <= y < offset


def test_nearest_empty_cell_prefers_free_center() -> None:
    world = World(size=64, seed=1, obstacles=[(7, 7)], resource_cells=[])

    assert bbm.nearest_empty_cell(world, (5, 5)) == (5, 5)


def test_nearest_empty_cell_skips_obstacles_and_resources() -> None:
    # Center blocked by obstacles; the nearest ring cells stay EMPTY.
    blocked = World(
        size=64,
        seed=1,
        obstacles=[(5, 5), (5, 4), (5, 6), (4, 5), (6, 5)],
        resource_cells=[],
    )
    pos = bbm.nearest_empty_cell(blocked, (5, 5))
    assert pos != (5, 5)
    assert blocked.terrain_kind(*pos) == EMPTY
    assert max(abs(pos[0] - 5), abs(pos[1] - 5)) == 1

    # Center holds a resource: the Core must not be placed on it.
    resourced = World(size=64, seed=1, obstacles=[], resource_cells=[(5, 5)])
    pos = bbm.nearest_empty_cell(resourced, (5, 5))
    assert pos != (5, 5)
    assert resourced.terrain_kind(*pos) == EMPTY


def test_extract_tenant_metrics_finds_first_events_and_moves() -> None:
    trace: list[dict[str, object]] = [
        _frame(0, [10, 10]),
        _frame(1, [10, 10], harvested=1),
        _frame(2, [10, 10], harvested=2, deposited=1),
        _frame(3, [10, 11], harvested=3, deposited=1),
        _frame(4, [10, 12], harvested=4, deposited=2),
    ]

    metrics = bbm.extract_tenant_metrics(trace, _terminal(), "t1", (0, 0), 0)

    assert metrics.first_harvest_tick == 1
    assert metrics.first_deposit_tick == 2
    assert metrics.first_migration_tick == 3
    assert metrics.core_move_ticks == 2
    assert metrics.core_start == (10, 10)
    assert metrics.final_resources == 11
    assert metrics.population_final == 2


def test_extract_tenant_metrics_none_when_no_events() -> None:
    trace: list[dict[str, object]] = [
        _frame(0, [3, 3]),
        _frame(1, [3, 3]),
        _frame(2, [3, 3]),
    ]

    metrics = bbm.extract_tenant_metrics(trace, _terminal(), "t1", (0, 0), 0)

    assert metrics.first_harvest_tick is None
    assert metrics.first_deposit_tick is None
    assert metrics.first_migration_tick is None
    assert metrics.core_move_ticks == 0


def test_extract_tenant_metrics_survives_dead_core_frames() -> None:
    trace: list[dict[str, object]] = [
        _frame(0, [0, 0], harvested=1),
        _frame(1, None),  # core destroyed; no position
        _frame(2, None),
    ]

    metrics = bbm.extract_tenant_metrics(trace, _terminal(survival_alive=False), "t1", (0, 0), 0)

    assert metrics.first_harvest_tick == 0
    assert metrics.first_deposit_tick is None
    assert metrics.core_move_ticks == 0
    assert metrics.alive is False


def test_metrics_quota_matches_official_formula() -> None:
    trace: list[dict[str, object]] = [_frame(0, [0, 0])]
    chunk = (8, 8)  # ring 16

    metrics = bbm.extract_tenant_metrics(trace, _terminal(), "t1", chunk, 16)

    assert metrics.quota == max(2, 128 // (8 + 16))
    assert metrics.ring == 16


def test_seed_result_json_shape_is_stable() -> None:
    metrics = bbm.extract_tenant_metrics([_frame(0, [1, 2])], _terminal(), "t1", (0, 0), 0)
    result = bbm.SeedResult(seed=0, ticks=3, world_size=512, sha="abc", tenants=(metrics,))

    payload = result.to_json()

    assert payload["seed"] == 0
    assert payload["sha"] == "abc"
    tenant = cast(dict[str, object], payload["tenants"][0])
    assert tenant["tenant"] == "t1"
    assert tenant["chunk"] == [0, 0]
    assert tenant["core_start"] == [1, 2]
    assert tenant["first_harvest_tick"] is None
    assert tenant["core_move_ticks"] == 0
