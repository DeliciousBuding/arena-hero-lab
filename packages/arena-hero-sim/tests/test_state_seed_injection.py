"""Regression tests for the state-seed replay injection hooks.

The injection is additive: ``World`` gains ``obstacles`` / ``resource_cells``,
``Game`` gains ``initial_state`` / ``world_obstacles`` / ``world_resource_cells``,
and ``run_ffa`` forwards them.  Default (all-None) behavior must stay identical
to the classic generated-world path.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from arena_hero_sim.ffa import WaitStrategy, run_ffa
from arena_hero_sim.ffa.config import CORE_HP, CORE_SHIELD
from arena_hero_sim.ffa.game import Game
from arena_hero_sim.ffa.world import World


# ----------------------------------------------------------------------
# World injection
# ----------------------------------------------------------------------
def test_world_custom_obstacles_and_resources() -> None:
    world = World(
        size=64,
        seed=1,
        obstacles=[(3, 3), (4, 4), (200, 200)],  # (200, 200) is out of bounds
        resource_cells=[(5, 5), (-100, 0)],
    )

    assert world.is_obstacle(3, 3)
    assert world.is_obstacle(4, 4)
    assert not world.is_obstacle(0, 0)
    assert not world.is_obstacle(5, 5)
    assert world.terrain_kind(5, 5) == 1  # RESOURCE
    assert (5, 5) in world.resources
    assert (200, 200) not in world.resources
    # Out-of-bounds resource cell ignored; custom layout seeds nothing else.
    assert world.resources == {(5, 5)}
    # Custom layout is empty everywhere except the injected cells.
    terrain = world.terrain
    assert terrain is not None
    obstacle_count = sum(row.count(2) for row in terrain)
    assert obstacle_count == 2


def test_world_custom_obstacles_without_resources_leaves_world_sterile() -> None:
    world = World(size=64, seed=1, obstacles=[(1, 1)])

    assert world.resources == set()
    assert world.is_obstacle(1, 1)


def test_world_default_generation_is_unchanged() -> None:
    """The default path (obstacles=None) still generates the classic world."""
    world = World(size=64, seed=3)

    # Generated worlds contain both open cells and obstacles, plus resources.
    terrain = world.terrain
    assert terrain is not None
    assert world.resources
    assert any(2 in row for row in terrain)
    assert any(row.count(0) > 0 for row in terrain)


# ----------------------------------------------------------------------
# Game injection
# ----------------------------------------------------------------------
def _initial_state() -> dict[str, object]:
    return {
        "players": {
            0: {
                "core": {"pos": (10, 10), "hp": 3, "shield": 7, "resources": 42},
                "units": [
                    {"utype": "WORKER", "pos": (11, 10), "hp": 2, "cargo": 5},
                    {"utype": "RANGER", "pos": (9, 10), "hp": 1, "cargo": 0},
                ],
            }
        },
        "beacon": ("ground", -5, -5),
    }


def test_game_initial_state_injection() -> None:
    game = Game(
        {0: WaitStrategy(), 1: WaitStrategy()},
        size=64,
        seed=2,
        max_ticks=5,
        initial_state=_initial_state(),
    )
    try:
        player = game.players[0]
        assert player.core is not None
        assert player.core.pos == (10, 10)
        assert player.core.hp == 3
        assert player.core.shield == 7
        assert player.core.resources == 42
        assert player.population == 2
        by_type = {u.utype: u for u in player.units.values()}
        assert set(by_type) == {"WORKER", "RANGER"}
        assert by_type["WORKER"].pos == (11, 10)
        assert by_type["WORKER"].cargo == 5
        assert by_type["WORKER"].hp == 2
        assert all(not u.just_spawned for u in player.units.values())
        assert game.beacon == ("ground", -5, -5)
        # The injected units act from tick 1 (no just_spawned lock).
        game.run()
    finally:
        game.close()


def test_game_initial_state_replaces_default_spawn() -> None:
    """The default core/worker of the injected player is replaced, not merged."""
    game = Game(
        {0: WaitStrategy(), 1: WaitStrategy()},
        size=64,
        seed=2,
        max_ticks=1,
        initial_state={
            "players": {0: {"core": {"pos": (20, 20)}, "units": []}},
        },
    )
    try:
        player = game.players[0]
        assert player.core is not None and player.core.pos == (20, 20)
        assert player.units == {}
        # Untouched opponent keeps its default spawn.
        opponent = game.players[1]
        assert opponent.core is not None
        assert len(opponent.units) == 1
    finally:
        game.close()


def test_game_initial_state_beacon_carried() -> None:
    game = Game(
        {0: WaitStrategy(), 1: WaitStrategy()},
        size=64,
        seed=2,
        max_ticks=1,
        initial_state={
            "players": {0: {"core": {"pos": (3, 3)}, "units": []}},
            "beacon": ("carried", 12345),
        },
    )
    try:
        assert game.beacon == ("carried", 12345)
    finally:
        game.close()


def test_game_initial_state_missing_core_fields_default() -> None:
    game = Game(
        {0: WaitStrategy(), 1: WaitStrategy()},
        size=64,
        seed=2,
        max_ticks=1,
        world_obstacles=[(30, 30)],
        initial_state={"players": {0: {"core": {"pos": (4, 4)}, "units": []}}},
    )
    try:
        core = game.players[0].core
        assert core is not None
        assert core.hp == CORE_HP
        assert core.shield == CORE_SHIELD
        assert core.resources == 0
    finally:
        game.close()


def test_game_initial_state_rejects_out_of_bounds_core() -> None:
    with pytest.raises(ValueError, match="out of bounds"):
        Game(
            {0: WaitStrategy(), 1: WaitStrategy()},
            size=64,
            seed=2,
            initial_state={"players": {0: {"core": {"pos": (999, 0)}, "units": []}}},
        )


def test_game_initial_state_rejects_core_on_obstacle() -> None:
    with pytest.raises(ValueError, match="obstacle"):
        Game(
            {0: WaitStrategy(), 1: WaitStrategy()},
            size=64,
            seed=2,
            world_obstacles=[(7, 7)],
            initial_state={"players": {0: {"core": {"pos": (7, 7)}, "units": []}}},
        )


def test_game_initial_state_rejects_unknown_player() -> None:
    with pytest.raises(ValueError, match="not a contestant"):
        Game(
            {0: WaitStrategy(), 1: WaitStrategy()},
            size=64,
            seed=2,
            initial_state={"players": {9: {"core": {"pos": (1, 1)}, "units": []}}},
        )


# ----------------------------------------------------------------------
# run_ffa pass-through
# ----------------------------------------------------------------------
def test_run_ffa_forwards_state_seed_injection() -> None:
    report = run_ffa(
        {"python": WaitStrategy(), "wait": WaitStrategy()},
        seed=4,
        ticks=3,
        size=64,
        world_obstacles=[(6, 6), (7, 6)],
        world_resource_cells=[(2, 2)],
        initial_state={
            "players": {
                0: {
                    "core": {"pos": (5, 5), "hp": 4, "shield": 6, "resources": 30},
                    "units": [{"utype": "WORKER", "pos": (5, 6), "hp": 2, "cargo": 1}],
                }
            },
            "beacon": ("ground", 0, 0),
        },
    )

    frame0 = report.trace[0]
    players = cast(Any, frame0["players"])
    python_player = players["python"]
    assert python_player["core"]["pos"] == [5, 5]
    assert python_player["core"]["hp"] == 4
    assert python_player["core"]["shield"] == 6
    assert python_player["core"]["resources"] == 30
    assert python_player["units"] == [
        {
            "uid": python_player["units"][0]["uid"],
            "utype": "WORKER",
            "pos": [5, 6],
            "hp": 2,
            "cargo": 1,
        }
    ]
    assert frame0["beacon"] == {"status": "ground", "position": [0, 0], "carrier_uid": None}


def test_run_ffa_without_injection_stays_default() -> None:
    """No-injection runs keep the classic generated world and default spawn."""
    report = run_ffa({"wait": WaitStrategy(), "rand": WaitStrategy()}, seed=4, ticks=2, size=64)

    frame0 = report.trace[0]
    for contestant in ("wait", "rand"):
        player = cast(Any, frame0["players"])[contestant]
        assert player["core"] is not None
        assert len(player["units"]) == 1
        assert player["units"][0]["utype"] == "WORKER"
