"""Pure Observation <-> SDK JSON mapping for third-party SDK-agent bridges."""

from __future__ import annotations

from typing import Any

from arena_hero_sim.ffa.observation import Observation
from arena_hero_sim.ffa.sdk_bridge import (
    observation_to_sdk_state,
    sdk_plan_to_ffa,
    uid_to_uuid,
    uuid_to_uid,
)


def _core(**overrides) -> dict:
    core = {"uid": 123, "pos": (5, 5), "hp": 5, "shield": 5, "resources": 12, "migration": None}
    core.update(overrides)
    return core


def _worker(uid: int, pos=(5, 5), cargo=3) -> dict:
    return {
        "uid": uid,
        "utype": "WORKER",
        "pos": pos,
        "hp": 2,
        "cargo": cargo,
        "carries_beacon": False,
    }


def _obs(**overrides) -> Observation:
    kwargs: dict[str, Any] = dict(
        player_id=0,
        tick=10,
        core=_core(),
        units=[_worker(456)],
        enemies=[],
        enemy_cores=[],
        resources={(9, 9)},
        obstacles={(1, 1)},
        beacon={"position": [0, 0], "status": "UNKNOWN"},
        population=1,
    )
    kwargs.update(overrides)
    return Observation(**kwargs)


def test_uid_uuid_round_trip() -> None:
    for uid in (0, 1, 456, 1 << 64, (1 << 120) - 1):
        assert uuid_to_uid(uid_to_uuid(uid)) == uid


def test_observation_to_sdk_state_own_entities() -> None:
    payload = observation_to_sdk_state(_obs())
    assert payload["tick"] == 10
    state = payload["state"]
    assert state["status"] == "ACTIVE"
    assert state["resources"] == 12
    assert state["population"] == 1
    assert state["champion_beacon"]["status"] is None
    assert state["events"] == []

    kinds = {obj["kind"] for obj in state["objects"]}
    assert kinds == {"CORE", "UNIT", "OBSTACLE", "RESOURCE"}

    own_core = next(obj for obj in state["objects"] if obj["kind"] == "CORE")
    assert own_core["controlled"] is True
    assert own_core["owner_username"] == "player0"
    assert own_core["id"] == uid_to_uuid(123)

    own_unit = next(obj for obj in state["objects"] if obj["kind"] == "UNIT")
    assert own_unit["controlled"] is True
    assert own_unit["unit_type"] == "WORKER"
    assert own_unit["cargo"] == 3
    assert own_unit["id"] == uid_to_uuid(456)


def test_observation_to_sdk_state_enemies_and_beacon_carried() -> None:
    obs = _obs(
        enemies=[{"uid": 789, "utype": "RANGER", "pos": (7, 7), "hp": 2}],
        enemy_cores=[{"uid": 321, "pos": (8, 8), "hp": 5, "shield": 5, "owner": 2}],
        beacon={"position": [5, 5], "status": "CARRIED", "carrier_id": 456},
    )
    state = observation_to_sdk_state(obs)["state"]

    enemy_unit = next(
        obj for obj in state["objects"] if obj["kind"] == "UNIT" and obj["controlled"] is False
    )
    assert enemy_unit["id"] == uid_to_uuid(789)
    assert "cargo" not in enemy_unit

    enemy_core = next(
        obj for obj in state["objects"] if obj["kind"] == "CORE" and obj["controlled"] is False
    )
    assert enemy_core["owner_username"] == "player2"

    assert state["champion_beacon"]["status"] == "CARRIED"
    assert state["champion_beacon"]["carrier_id"] == uid_to_uuid(456)


def test_observation_to_sdk_state_moving_core() -> None:
    obs = _obs(core=_core(migration=("RIGHT", 1)))
    core = next(
        obj for obj in observation_to_sdk_state(obs)["state"]["objects"] if obj["kind"] == "CORE"
    )
    assert core["state"] == "MOVING"
    assert core["move_direction"] == "RIGHT"
    assert core["destination"] == [6, 5]


def test_sdk_plan_to_ffa_maps_actions() -> None:
    plan = {
        "tick": 10,
        "unit_actions": {
            uid_to_uuid(456): {"type": "MOVE", "direction": "RIGHT"},
            uid_to_uuid(999): {"type": "DEPOSIT"},
        },
        "core_action": {"type": "SPAWN", "unit_type": "VANGUARD"},
    }
    ffa = sdk_plan_to_ffa(plan)
    assert ffa["core"] == ("SPAWN", {"unit_type": "VANGUARD"})
    assert ffa["units"][456] == ("MOVE", {"direction": "RIGHT"})
    assert ffa["units"][999] == ("DEPOSIT", {})


def test_sdk_plan_to_ffa_round_trips_unit_ids() -> None:
    plan = {
        "tick": 1,
        "unit_actions": {uid_to_uuid(456): {"type": "SHOOT", "expected_cell": [7, 7]}},
        "core_action": None,
    }
    ffa = sdk_plan_to_ffa(plan)
    assert ffa["core"] is None
    assert ffa["units"][456] == ("SHOOT", {"expected_cell": [7, 7]})
