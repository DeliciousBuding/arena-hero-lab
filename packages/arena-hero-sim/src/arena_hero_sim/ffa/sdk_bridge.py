"""Pure Observation <-> official SDK JSON mapping for third-party SDK bridges.

The four non-evolve third-party agents (drew-z / waaiging / guide / tactic) are
written against the official ``arena-hero`` SDK: they read a ``Turn`` (built from
``PlayerState``) and queue actions that are serialized as a ``CommandPlan``.  This
module owns the *pure* half of that bridge so it can be unit-tested in the Lab
environment without installing ``arena_hero``:

- :func:`observation_to_sdk_state` maps an FFA :class:`Observation` to the JSON
  dict the runner feeds into ``PlayerState.model_validate`` (plus the tick).
- :func:`sdk_plan_to_ffa` maps a ``CommandPlan`` JSON dict back to the FFA plan
  shape ``{"core": (action, kwargs) | None, "units": {uid: (action, kwargs)}}``.

The runner itself (which imports ``arena_hero`` and constructs the real ``Turn``
for a specific agent entrypoint) lives in the bench orchestration layer and runs
in each third-party repo's own ``.venv``.
"""

from __future__ import annotations

from uuid import UUID

from .config import CORE_MIGRATION_TICKS, DIRECTIONS
from .observation import Observation

_SDK_DIRECTIONS = {"UP", "DOWN", "LEFT", "RIGHT"}
_SDK_UNIT_TYPES = {"WORKER", "VANGUARD", "RANGER"}
_SDK_BEACON_STATUS = {"GROUND": "GROUND", "CARRIED": "CARRIED"}

# SDK UnitAction.type -> FFA action type (kwargs are mapped per-action below).
_UNIT_ACTION_TYPES = {
    "WAIT",
    "MOVE",
    "HARVEST",
    "DEPOSIT",
    "SWEEP",
    "SHOOT",
    "PICKUP_BEACON",
    "DROP_BEACON",
    "SELF_DESTRUCT",
    "HEAL",
}
_CORE_ACTION_TYPES = {
    "WAIT",
    "SPAWN",
    "REPAIR_SHIELD",
    "START_MOVE",
    "CANCEL_MOVE",
    "PICKUP_BEACON",
    "DROP_BEACON",
    "SELF_DESTRUCT",
    "HEAL",
}

# Bounded bidirectional uid<->UUID string caches.  Each tick converts every own
# unit/core id plus every visible enemy id in both directions (~100+ per agent
# per tick), and UUID(int=...).str / UUID(str).int are pure functions, so a
# flat cache is a safe ~10x speedup at ~1M conversions per match.  Cleared in
# place at the cap to stay deterministic and memory-bounded across matches.
_UID_TO_UUID: dict[int, str] = {}
_UUID_TO_UID: dict[str, int] = {}
_ID_CACHE_MAX = 16384


def uid_to_uuid(uid: int) -> str:
    """Map a 64-120 bit FFA uid to the canonical UUID string the SDK expects."""

    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise TypeError("uid must be a non-negative integer")
    cached = _UID_TO_UUID.get(uid)
    if cached is None:
        cached = str(UUID(int=uid))
        if len(_UID_TO_UUID) >= _ID_CACHE_MAX:
            _UID_TO_UUID.clear()
        _UID_TO_UUID[uid] = cached
    return cached


def uuid_to_uid(uuid_str: str) -> int:
    """Invert :func:`uid_to_uuid` so SDK CommandPlan keys map back to FFA uids."""

    if not isinstance(uuid_str, str) or not uuid_str.strip():
        raise TypeError("uuid_str must be a non-empty string")
    cached = _UUID_TO_UID.get(uuid_str)
    if cached is None:
        cached = UUID(uuid_str).int
        if len(_UUID_TO_UID) >= _ID_CACHE_MAX:
            _UUID_TO_UID.clear()
        _UUID_TO_UID[uuid_str] = cached
    return cached


def _beacon_sdk_status(ffa_status: str | None) -> str | None:
    if ffa_status in _SDK_BEACON_STATUS:
        return _SDK_BEACON_STATUS[ffa_status]
    return None


def _owner_username(player_id: int) -> str:
    return f"player{player_id}"


def _core_view(core: dict, *, controlled: bool, owner: int) -> dict:
    pos = core["pos"]
    view: dict = {
        "kind": "CORE",
        "id": uid_to_uuid(core["uid"]),
        "controlled": controlled,
        "owner_username": _owner_username(owner),
        "position": [pos[0], pos[1]],
        "hp": core["hp"],
        "shield": core["shield"],
        "state": "NORMAL",
    }
    migration = core.get("migration")
    if migration is not None:
        direction, progress = migration
        dx, dy = DIRECTIONS[direction]
        view["state"] = "MOVING"
        view["move_direction"] = direction
        view["move_progress"] = progress
        view["move_required_ticks"] = CORE_MIGRATION_TICKS
        view["destination"] = [pos[0] + dx, pos[1] + dy]
    return view


def _unit_view(unit: dict, *, controlled: bool) -> dict:
    cargo = unit.get("cargo")
    view: dict = {
        "kind": "UNIT",
        "id": uid_to_uuid(unit["uid"]),
        "controlled": controlled,
        "position": [unit["pos"][0], unit["pos"][1]],
        "hp": unit["hp"],
        "unit_type": unit["utype"],
    }
    if controlled and unit.get("utype") == "WORKER":
        view["cargo"] = 0 if cargo is None else cargo
    return view


def observation_to_sdk_state(observation: Observation) -> dict:
    """Return ``{"tick": int, "state": {...}}`` for one FFA Observation.

    ``state`` is the JSON form of the SDK ``PlayerState``.  Entity ids are the
    canonical UUID strings of the FFA sparse uids so the runner can round-trip
    them back with :func:`uuid_to_uid`.
    """

    if not isinstance(observation, Observation):
        raise TypeError("observation must be an Observation")

    objects: list[dict] = []
    core = observation.core
    if core is not None:
        objects.append(_core_view(core, controlled=True, owner=observation.player_id))
    for unit in observation.units:
        objects.append(_unit_view(unit, controlled=True))
    for enemy_core in observation.enemy_cores:
        objects.append(
            _core_view(
                {
                    "uid": enemy_core["uid"],
                    "pos": enemy_core["pos"],
                    "hp": enemy_core["hp"],
                    "shield": enemy_core["shield"],
                },
                controlled=False,
                owner=enemy_core["owner"],
            )
        )
    for enemy in observation.enemies:
        objects.append(_unit_view(enemy, controlled=False))

    if observation.obstacles:
        objects.append(
            {
                "kind": "OBSTACLE",
                "positions": [[x, y] for x, y in sorted(observation.obstacles)],
            }
        )
    if observation.resources:
        objects.append(
            {
                "kind": "RESOURCE",
                "positions": [[x, y] for x, y in sorted(observation.resources)],
            }
        )

    beacon = observation.beacon or {}
    beacon_position = beacon.get("position", [0, 0])
    beacon_status = _beacon_sdk_status(beacon.get("status"))
    carrier_id = None
    if beacon_status == "CARRIED" and beacon.get("carrier_id") is not None:
        carrier_id = uid_to_uuid(beacon["carrier_id"])

    state = {
        "status": observation.status,
        "respawn_at_tick": observation.respawn_at_tick,
        "resources": core["resources"] if core is not None else 0,
        "population": observation.population,
        "champion_beacon": {
            "position": [beacon_position[0], beacon_position[1]],
            "status": beacon_status,
            "carrier_id": carrier_id,
        },
        "objects": objects,
        "events": [],
    }
    return {"tick": observation.tick, "state": state}


def _direction_kwargs(action: dict) -> dict:
    direction = action.get("direction")
    if direction not in _SDK_DIRECTIONS:
        raise ValueError(f"unsupported direction: {direction!r}")
    return {"direction": direction}


def _unit_action_to_ffa(action: dict) -> tuple[str, dict]:
    atype = action.get("type")
    if atype not in _UNIT_ACTION_TYPES:
        raise ValueError(f"unsupported SDK unit action: {atype!r}")
    if atype == "MOVE":
        return ("MOVE", _direction_kwargs(action))
    if atype == "SWEEP":
        return ("SWEEP", _direction_kwargs(action))
    if atype == "SHOOT":
        return ("SHOOT", {"expected_cell": action["expected_cell"]})
    return (atype, {})


def _core_action_to_ffa(action: dict) -> tuple[str, dict]:
    atype = action.get("type")
    if atype not in _CORE_ACTION_TYPES:
        raise ValueError(f"unsupported SDK core action: {atype!r}")
    if atype == "SPAWN":
        unit_type = action.get("unit_type")
        if unit_type not in _SDK_UNIT_TYPES:
            raise ValueError(f"unsupported spawn unit_type: {unit_type!r}")
        return ("SPAWN", {"unit_type": unit_type})
    if atype == "START_MOVE":
        return ("START_MOVE", _direction_kwargs(action))
    return (atype, {})


def sdk_plan_to_ffa(plan: dict) -> dict:
    """Map a SDK ``CommandPlan`` JSON dict to the FFA plan shape.

    The FFA engine expects ``{"core": (action, kwargs) | None, "units": {uid:
    (action, kwargs)}}`` with integer unit ids.
    """

    if not isinstance(plan, dict):
        raise TypeError("plan must be a mapping")

    units: dict[int, tuple[str, dict]] = {}
    for uuid_str, action in (plan.get("unit_actions") or {}).items():
        units[uuid_to_uid(uuid_str)] = _unit_action_to_ffa(action)

    core = plan.get("core_action")
    core_plan = None if core is None else _core_action_to_ffa(core)
    return {"core": core_plan, "units": units}


__all__ = [
    "observation_to_sdk_state",
    "sdk_plan_to_ffa",
    "uid_to_uuid",
    "uuid_to_uid",
]
