"""Plug the arena-hero-agent decider into the vendored FFA host.

The FFA host (``arena_hero_sim.ffa``) speaks the ahsim contract: each tick it
builds an ``Observation`` and expects back a ``Plan`` of the shape
``{"core": (action, kwargs) | None, "units": {uid: (action, kwargs)}}``. The
agent (``arena_hero_agent``) speaks its own application DTOs: it consumes a
``TurnObservation`` and produces a ``Decision``. This module is the bridge; it
lives in the Lab repository and never modifies either side.

Because ``arena_hero_agent`` (and its ``arena_hero`` SDK dependency) are not
installed in the Lab environment, in-process import is attempted first and,
when unavailable, the strategy falls back to a long-lived subprocess that keeps
one ``compose_decider`` instance alive. Keeping that single process across
ticks preserves the decider's cross-tick state (worker assignments, loop
trails, raid state) exactly as the live writer would.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import DIRECTIONS
from .observation import Observation

_RULES_VERSION = "v0.14"

# Agent ``UnitRole`` / ``CoreAction(SPAWN)`` values are lowercase; the FFA
# engine uses the uppercase type strings from ``arena_hero_sim.ffa.config``.
_ROLE_TO_DOMAIN = {
    "WORKER": "worker",
    "VANGUARD": "vanguard",
    "RANGER": "ranger",
}
_ROLE_TO_FFA = {domain: upper for upper, domain in _ROLE_TO_DOMAIN.items()}

# Agent ``Direction`` values -> FFA ``DIRECTIONS`` keys.
_DIRECTION_TO_FFA = {
    "north": "UP",
    "east": "RIGHT",
    "south": "DOWN",
    "west": "LEFT",
}

_BEACON_STATUS = {
    "UNKNOWN": "unknown",
    "GROUND": "ground",
    "CARRIED": "carried",
}


def _cell(position: tuple[int, int] | list[int]) -> list[int]:
    return [position[0], position[1]]


def _beacon_canonical(beacon: dict[str, Any]) -> dict[str, Any]:
    status = _BEACON_STATUS.get(beacon.get("status"), "unknown")
    carrier_id = beacon.get("carrier_id")
    return {
        "position": _cell(beacon.get("position", [0, 0])),
        "status": status,
        "carrier_id": None if carrier_id is None else str(carrier_id),
    }


def observation_to_canonical(observation: Observation) -> dict[str, Any]:
    """Map one ahsim ``Observation`` to the agent's canonical turn JSON.

    The output matches ``arena_hero_agent.adapters.replay.decode_observation``
    (snake_case dataclass fields, string enum values, ``[x, y]`` coordinates).
    Entity ids are the string form of the FFA sparse integer uid, so the
    inverse ``decision_to_plan`` can recover the raw integer uid with ``int()``.
    """
    lifecycle = (
        "active" if getattr(observation, "status", "ACTIVE") == "ACTIVE" else "respawning"
    )

    core: dict[str, Any] | None = None
    if observation.core is not None:
        migration = observation.core.get("migration")
        if migration is None:
            state = "normal"
            destination = None
        else:
            direction, _progress = migration
            dx, dy = DIRECTIONS[direction]
            state = "moving"
            destination = [observation.core["pos"][0] + dx, observation.core["pos"][1] + dy]
        core = {
            "id": str(observation.core["uid"]),
            "position": _cell(observation.core["pos"]),
            "state": state,
            "health": observation.core["hp"],
            "shield": observation.core["shield"],
            "owner": str(observation.player_id),
            "destination": destination,
        }

    units = [
        {
            "id": str(unit["uid"]),
            "position": _cell(unit["pos"]),
            "role": _ROLE_TO_DOMAIN[unit["utype"]],
            "health": unit["hp"],
            "cargo": unit["cargo"],
        }
        for unit in observation.units
    ]

    entities: list[dict[str, Any]] = []
    for enemy in observation.enemies:
        entities.append(
            {
                "id": str(enemy["uid"]),
                "kind": "unit",
                "position": _cell(enemy["pos"]),
                "health": enemy["hp"],
                "owner": None,
                "unit_role": _ROLE_TO_DOMAIN[enemy["utype"]],
            }
        )
    for enemy_core in observation.enemy_cores:
        entities.append(
            {
                "id": str(enemy_core["uid"]),
                "kind": "core",
                "position": _cell(enemy_core["pos"]),
                "health": enemy_core["hp"],
                "owner": str(enemy_core["owner"]),
                "unit_role": None,
            }
        )

    resources = [
        {"position": _cell(position), "remaining": None}
        for position in sorted(observation.resources)
    ]
    terrain = [
        {"position": _cell(position), "state": "blocked"}
        for position in sorted(observation.obstacles)
    ]

    respawn_at_tick = None
    if lifecycle == "respawning":
        respawn_at_tick = observation.respawn_at_tick or observation.tick

    return {
        "tick": observation.tick,
        "lifecycle": lifecycle,
        "resources": observation.core["resources"] if observation.core is not None else 0,
        "population": observation.population,
        "projection": {
            "tick": observation.tick,
            "rules_version": _RULES_VERSION,
            "core": core,
            "units": units,
            "entities": entities,
            "resources": resources,
            "terrain": terrain,
            "beacon": _beacon_canonical(observation.beacon),
        },
        "events": [],
        "respawn_at_tick": respawn_at_tick,
    }


def _unit_action(intent: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    action = intent["action"]
    if action == "wait":
        return None
    if action in ("move", "sweep"):
        return (action.upper(), {"direction": _DIRECTION_TO_FFA[intent["direction"]]})
    if action == "shoot":
        cell = intent["expected_cell"]
        return ("SHOOT", {"expected_cell": (cell[0], cell[1])})
    return (action.upper(), {})


def _core_action(core_intent: dict[str, Any] | None) -> tuple[str, dict[str, Any]] | None:
    if core_intent is None:
        return None
    action = core_intent["action"]
    if action == "wait":
        return None
    if action == "spawn":
        return ("SPAWN", {"unit_type": _ROLE_TO_FFA[core_intent["unit_role"]]})
    if action == "start_move":
        return ("START_MOVE", {"direction": _DIRECTION_TO_FFA[core_intent["direction"]]})
    return (action.upper(), {})


def decision_to_plan(decision: dict[str, Any]) -> dict[str, object]:
    """Map the agent's canonical ``Decision`` JSON back into an ahsim ``Plan``.

    ``unit_id`` values are the string form of the FFA integer uid, so the
    inverse is ``int(unit_id)``. ``wait`` intents and a ``wait`` core intent
    are omitted (semantically identical to no action in the FFA engine).
    """
    units: dict[int, tuple[str, dict[str, Any]]] = {}
    for intent in decision.get("unit_intents", []):
        action = _unit_action(intent)
        if action is None:
            continue
        units[int(intent["unit_id"])] = action

    return {"core": _core_action(decision.get("core_intent")), "units": units}


def _decision_to_canonical(decision: Any) -> dict[str, Any]:
    """Serialize an agent ``Decision`` object to the canonical snake_case JSON.

    Used by the in-process path; the subprocess runner contains the same shape.
    """
    return {
        "tick": decision.tick,
        "unit_intents": [
            {
                "unit_id": intent.unit_id.value,
                "action": intent.action.value,
                "direction": None if intent.direction is None else intent.direction.value,
                "target_id": None if intent.target_id is None else intent.target_id.value,
                "expected_cell": (
                    None if intent.expected_cell is None else [intent.expected_cell.x, intent.expected_cell.y]
                ),
            }
            for intent in decision.unit_intents
        ],
        "core_intent": (
            None
            if decision.core_intent is None
            else {
                "action": decision.core_intent.action.value,
                "direction": (
                    None
                    if decision.core_intent.direction is None
                    else decision.core_intent.direction.value
                ),
                "unit_role": (
                    None
                    if decision.core_intent.unit_role is None
                    else decision.core_intent.unit_role.value
                ),
            }
        ),
    }


def _try_inprocess_decider(
    movement_guard: bool = False,
    economy_budget: bool = False,
    raid_quota: bool = False,
):
    """Return an in-process ``canonical -> canonical decision`` callable, or None.

    The agent is imported dynamically so the sim package keeps no static
    ``arena_hero_agent`` dependency (the boundary test forbids that); the
    subprocess path below never imports it at all.
    """
    import importlib

    try:
        decode_observation = importlib.import_module(
            "arena_hero_agent.adapters.replay"
        ).decode_observation
        DeadlineBudget = importlib.import_module("arena_hero_agent.domain").DeadlineBudget
        composition = importlib.import_module("arena_hero_agent.strategies.composition")
        MissionConfig = importlib.import_module(
            "arena_hero_agent.planning.mission"
        ).MissionConfig
        WorkerTaskPlannerConfig = importlib.import_module(
            "arena_hero_agent.planning.worker_assignment"
        ).WorkerTaskPlannerConfig
    except Exception:
        return None

    # Enable one worker surveyor so the FFA contestant explores when no mine is
    # visible.  Without it the lone starting worker (parked on the core cell)
    # stays WAIT and permanently blocks the core's SPAWN via CELL_UNIT_LIMIT.
    decider = composition.compose_decider(
        composition.ComposedDeciderConfig(
            worker_config=WorkerTaskPlannerConfig(
                mission=MissionConfig(survey_worker_cap=1)
            ),
            movement_guard_enabled=movement_guard,
            economy_budget_enabled=economy_budget,
            raid_quota_enabled=raid_quota,
        )
    )
    budget = DeadlineBudget.from_milliseconds(1_000)

    def run(canonical: dict[str, Any]) -> dict[str, Any]:
        observation = decode_observation(canonical)
        return _decision_to_canonical(decider(observation, budget))

    return run


def _venv_python(agent_root: Path) -> Path | None:
    if sys.platform == "win32":
        return agent_root / ".venv" / "Scripts" / "python.exe"
    return agent_root / ".venv" / "bin" / "python"


def discover_agent_python() -> str | None:
    """Locate the arena-hero-agent venv python, or ``None`` if unavailable.

    Resolution order: the ``ARENA_HERO_AGENT_PYTHON`` environment variable,
    then an upward search from this file for a sibling ``arena-hero-agent``
    checkout with a ``.venv``. The Lab and agent repositories share the same
    parent (``.../arena``) in this workspace.
    """
    env = os.environ.get("ARENA_HERO_AGENT_PYTHON")
    if env:
        return env

    root = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = _venv_python(root / "arena-hero-agent")
        if candidate is not None and candidate.exists():
            return str(candidate)
        if root.parent == root:
            break
        root = root.parent
    return None


# Runs inside the agent's venv: decode one canonical turn per stdin line and
# print one canonical decision per line. The process stays alive across ticks.
_RUNNER_SOURCE = r'''
import json
import sys

from arena_hero_agent.adapters.replay import decode_observation
from arena_hero_agent.domain import DeadlineBudget
from arena_hero_agent.planning.mission import MissionConfig
from arena_hero_agent.planning.worker_assignment import WorkerTaskPlannerConfig
from arena_hero_agent.strategies.composition import ComposedDeciderConfig, compose_decider

# Enable one worker surveyor so the FFA contestant explores when no mine is
# visible.  Without it the lone starting worker (parked on the core cell)
# stays WAIT and permanently blocks the core's SPAWN via CELL_UNIT_LIMIT.
# Research switches are forwarded as argv flags (default all off).
_flags = frozenset(sys.argv[1:])
decider = compose_decider(
    ComposedDeciderConfig(
        worker_config=WorkerTaskPlannerConfig(
            mission=MissionConfig(survey_worker_cap=1)
        ),
        movement_guard_enabled="--movement-guard" in _flags,
        economy_budget_enabled="--economy-budget" in _flags,
        raid_quota_enabled="--raid-quota" in _flags,
    )
)
budget = DeadlineBudget.from_milliseconds(1_000)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    observation = decode_observation(json.loads(line))
    decision = decider(observation, budget)
    print(
        json.dumps(
            {
                "tick": decision.tick,
                "unit_intents": [
                    {
                        "unit_id": intent.unit_id.value,
                        "action": intent.action.value,
                        "direction": None if intent.direction is None else intent.direction.value,
                        "target_id": None if intent.target_id is None else intent.target_id.value,
                        "expected_cell": (
                            None
                            if intent.expected_cell is None
                            else [intent.expected_cell.x, intent.expected_cell.y]
                        ),
                    }
                    for intent in decision.unit_intents
                ],
                "core_intent": (
                    None
                    if decision.core_intent is None
                    else {
                        "action": decision.core_intent.action.value,
                        "direction": (
                            None
                            if decision.core_intent.direction is None
                            else decision.core_intent.direction.value
                        ),
                        "unit_role": (
                            None
                            if decision.core_intent.unit_role is None
                            else decision.core_intent.unit_role.value
                        ),
                    }
                ),
            },
            sort_keys=True,
        ),
        flush=True,
    )
'''


def _runner_argv(
    python: str,
    movement_guard: bool,
    economy_budget: bool,
    raid_quota: bool,
) -> list[str]:
    """Build the subprocess argv, forwarding research switches as flags."""
    argv = [python, "-c", _RUNNER_SOURCE]
    if movement_guard:
        argv.append("--movement-guard")
    if economy_budget:
        argv.append("--economy-budget")
    if raid_quota:
        argv.append("--raid-quota")
    return argv


class PythonAgentStrategy:
    """A FFA contestant backed by the arena-hero-agent composed decider."""

    def __init__(
        self,
        *,
        agent_python: str | os.PathLike[str] | None = None,
        movement_guard: bool = False,
        economy_budget: bool = False,
        raid_quota: bool = False,
    ) -> None:
        self._agent_python = agent_python
        self._movement_guard = movement_guard
        self._economy_budget = economy_budget
        self._raid_quota = raid_quota
        self._inprocess = _try_inprocess_decider(
            movement_guard=movement_guard,
            economy_budget=economy_budget,
            raid_quota=raid_quota,
        )
        self._proc: subprocess.Popen[str] | None = None

    def decide(self, observation: Observation) -> dict[str, object]:
        canonical = observation_to_canonical(observation)
        if self._inprocess is not None:
            decision = self._inprocess(canonical)
        else:
            decision = self._run_subprocess(canonical)
        return decision_to_plan(decision)

    def _run_subprocess(self, canonical: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None:
            python = self._agent_python or discover_agent_python()
            if not python:
                raise RuntimeError(
                    "arena-hero-agent is not importable and no agent venv python was "
                    "found; set ARENA_HERO_AGENT_PYTHON or pass agent_python="
                )
            self._proc = subprocess.Popen(
                _runner_argv(
                    str(python),
                    self._movement_guard,
                    self._economy_budget,
                    self._raid_quota,
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )

        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps(canonical, sort_keys=True) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            stderr = "" if self._proc.stderr is None else self._proc.stderr.read()
            raise RuntimeError(
                f"python agent subprocess exited unexpectedly: {stderr.strip()}"
            )
        return json.loads(line)

    def close(self) -> None:
        if self._proc is None:
            return
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None


__all__ = [
    "PythonAgentStrategy",
    "decision_to_plan",
    "discover_agent_python",
    "observation_to_canonical",
]

