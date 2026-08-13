"""Tests for the Python-agent FFA shim (mapping round-trip + one real match)."""

from __future__ import annotations

import pytest

from arena_hero_sim.ffa import RandomBot, WaitStrategy, run_ffa
from arena_hero_sim.ffa.observation import Observation
from arena_hero_sim.ffa.python_agent_shim import (
    PythonAgentStrategy,
    decision_to_plan,
    discover_agent_python,
    observation_to_canonical,
)


def _observation() -> Observation:
    return Observation(
        player_id=0,
        tick=7,
        core={
            "uid": 7001,
            "pos": (0, 0),
            "hp": 5,
            "shield": 5,
            "resources": 9,
            "migration": None,
            "capacity": 15,
        },
        units=[
            {"uid": 7002, "utype": "WORKER", "pos": (0, 0), "hp": 2, "cargo": 1, "carries_beacon": True},
            {"uid": 7003, "utype": "VANGUARD", "pos": (1, 0), "hp": 4, "cargo": 0, "carries_beacon": False},
        ],
        enemies=[
            {"uid": 8001, "utype": "RANGER", "pos": (3, 0), "hp": 2},
        ],
        enemy_cores=[
            {"uid": 8002, "pos": (5, 5), "hp": 5, "shield": 5, "owner": 1},
        ],
        resources={(2, 0), (0, 2)},
        obstacles={(4, 4)},
        beacon={"position": [0, 0], "status": "CARRIED", "carrier_id": 7002},
        population=2,
    )


def test_observation_to_canonical_maps_entity_ids_and_enums() -> None:
    canonical = observation_to_canonical(_observation())

    assert canonical["tick"] == 7
    assert canonical["lifecycle"] == "active"
    assert canonical["resources"] == 9
    assert canonical["population"] == 2

    projection = canonical["projection"]
    assert projection["rules_version"] == "v0.14"
    assert projection["tick"] == 7

    core = projection["core"]
    assert core["id"] == "7001"
    assert core["position"] == [0, 0]
    assert core["state"] == "normal"
    assert core["owner"] == "0"

    assert [u["id"] for u in projection["units"]] == ["7002", "7003"]
    assert [u["role"] for u in projection["units"]] == ["worker", "vanguard"]

    # Enemy units become UNIT entities; enemy cores become CORE entities.
    assert projection["entities"] == [
        {
            "id": "8001",
            "kind": "unit",
            "position": [3, 0],
            "health": 2,
            "owner": None,
            "unit_role": "ranger",
        },
        {
            "id": "8002",
            "kind": "core",
            "position": [5, 5],
            "health": 5,
            "owner": "1",
            "unit_role": None,
        },
    ]

    assert [r["position"] for r in projection["resources"]] == [[0, 2], [2, 0]]
    assert projection["terrain"] == [{"position": [4, 4], "state": "blocked"}]
    assert projection["beacon"] == {
        "position": [0, 0],
        "status": "carried",
        "carrier_id": "7002",
    }


def test_decision_to_plan_roundtrips_uid_and_vocabulary() -> None:
    canonical = observation_to_canonical(_observation())
    unit_ids = [unit["uid"] for unit in _observation().units]

    decision = {
        "tick": 7,
        "unit_intents": [
            {"unit_id": str(unit_ids[0]), "action": "move", "direction": "east", "target_id": None, "expected_cell": None},
            {"unit_id": str(unit_ids[1]), "action": "sweep", "direction": "south", "target_id": None, "expected_cell": None},
        ],
        "core_intent": {"action": "spawn", "direction": None, "unit_role": "ranger"},
    }
    plan = decision_to_plan(decision)

    assert plan["core"] == ("SPAWN", {"unit_type": "RANGER"})
    assert plan["units"][unit_ids[0]] == ("MOVE", {"direction": "RIGHT"})
    assert plan["units"][unit_ids[1]] == ("SWEEP", {"direction": "DOWN"})
    assert set(plan["units"]) == set(unit_ids)

    # A shoot intent carries the expected cell through; wait intents are omitted.
    shoot = {
        "tick": 7,
        "unit_intents": [
            {"unit_id": str(unit_ids[1]), "action": "shoot", "direction": None, "target_id": None, "expected_cell": [4, 4]},
            {"unit_id": str(unit_ids[0]), "action": "wait", "direction": None, "target_id": None, "expected_cell": None},
        ],
        "core_intent": None,
    }
    plan = decision_to_plan(shoot)
    assert plan["core"] is None
    assert plan["units"] == {unit_ids[1]: ("SHOOT", {"expected_cell": (4, 4)})}

    assert canonical["tick"] == 7  # keep the mapping referenced by this test


def test_python_agent_plays_a_real_ffa_match() -> None:
    if discover_agent_python() is None:
        pytest.skip("arena-hero-agent venv not found; set ARENA_HERO_AGENT_PYTHON")

    strategy = PythonAgentStrategy()
    try:
        report = run_ffa(
            {"python": strategy, "rand": RandomBot(), "wait": WaitStrategy()},
            seed=7,
            ticks=30,
        )
    finally:
        strategy.close()

    assert report.contestant_ids == ("python", "rand", "wait")
    assert report.ticks == 30

    by_id = {entry.contestant_id: entry for entry in report.terminal}
    python_entry = by_id["python"]

    assert python_entry.survival_alive is True
    assert python_entry.final_resources >= 0
    # The default composed decider starts with 5 resources and spends them to
    # spawn at least one extra worker during the match.
    assert python_entry.population_final >= 2
    # The decider must actually resolve its SPAWN and then collect resources:
    # a lone worker parked on the core cell would otherwise block every spawn
    # and leave the contestant indistinguishable from WaitStrategy.
    assert python_entry.stats["spawn_cost"] > 0
    assert python_entry.stats["harvested"] > 0
