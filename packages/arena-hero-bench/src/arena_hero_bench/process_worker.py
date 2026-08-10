"""Child-process worker entry point for the bounded local process executor.

Run as ``python -m arena_hero_bench.process_worker``. Reads one versioned
``arena.process.work.v1`` envelope line from stdin, executes every request with
the declared backend, and writes one ``arena.process.result.v1`` envelope line
to stdout. Scenarios arrive once in a top-level map keyed by their content
SHA-256; each request entry only references the digest it needs. Hard failures
(invalid envelope, unknown backend, reconstruction errors, missing scenario
references) are reported on stderr with a non-zero exit so the parent fails
closed.

The worker is not a security sandbox: it never touches the network, shells,
secrets, or arbitrary imports, and it only reconstructs data carried inside the
work envelope.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence

from arena_hero_bench.process_executor import (
    RESULT_ENVELOPE_VERSION,
    WORK_ENVELOPE_VERSION,
    ProcessExecutorError,
    request_from_json,
    result_to_json,
)
from arena_hero_sim.backend import SimulatorBackend
from arena_hero_sim.contracts import SimulationRequest
from arena_hero_sim.reference import REFERENCE_BACKEND_ID, ReferenceEngineBackend
from arena_hero_sim.reference_contracts import (
    ReferenceActionKind,
    ReferenceCommand,
    ReferenceCore,
    ReferenceDirection,
    ReferencePlayer,
    ReferenceScenario,
    ReferenceTerrain,
    ReferenceTurn,
    ReferenceUnit,
    ReferenceWorld,
)
from arena_hero_sim.serialization import canonical_json_bytes

_SCENARIO_SCHEMA = "arena.reference.scenario.v1"
_WORLD_SCHEMA = "arena.reference.world.v1"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProcessExecutorError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ProcessExecutorError(f"{field_name} must not be empty")
    return normalized


def _int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProcessExecutorError(f"{field_name} must be an integer")
    return value


def _position(value: object, field_name: str) -> tuple[int, int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise ProcessExecutorError(f"{field_name} must be an [x, y] pair")
    x, y = value
    if (
        isinstance(x, bool)
        or not isinstance(x, int)
        or isinstance(y, bool)
        or not isinstance(y, int)
    ):
        raise ProcessExecutorError(f"{field_name} must contain integers")
    return (x, y)


def _position_list(value: object, field_name: str) -> list[tuple[int, int]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProcessExecutorError(f"{field_name} must be an array")
    return [_position(item, field_name) for item in value]


def _core_from_dict(payload: Mapping[str, object]) -> ReferenceCore:
    return ReferenceCore(
        id=_text(payload.get("id"), "core id"),
        position=_position(payload.get("position"), "core position"),
    )


def _unit_from_dict(payload: Mapping[str, object]) -> ReferenceUnit:
    return ReferenceUnit(
        id=_text(payload.get("id"), "unit id"),
        owner_id=_text(payload.get("ownerId"), "unit owner_id"),
        position=_position(payload.get("position"), "unit position"),
        hp=_int(payload.get("hp", 2), "unit hp"),
        cargo=_int(payload.get("cargo", 0), "unit cargo"),
        unit_type=_text(payload.get("unitType", "WORKER"), "unit type"),
    )


def _player_from_dict(payload: Mapping[str, object]) -> ReferencePlayer:
    core_payload = payload.get("core")
    if not isinstance(core_payload, Mapping):
        raise ProcessExecutorError("player core must be an object")
    units_payload = payload.get("units")
    if not isinstance(units_payload, Sequence) or isinstance(units_payload, (str, bytes)):
        raise ProcessExecutorError("player units must be an array")
    units: list[ReferenceUnit] = []
    for item in units_payload:
        if not isinstance(item, Mapping):
            raise ProcessExecutorError("player unit must be an object")
        units.append(_unit_from_dict(item))
    return ReferencePlayer(
        id=_text(payload.get("id"), "player id"),
        username=_text(payload.get("username"), "username"),
        resources=_int(payload.get("resources"), "resources"),
        core=_core_from_dict(core_payload),
        units=tuple(units),
    )


def _terrain_from_dict(payload: Mapping[str, object]) -> ReferenceTerrain:
    return ReferenceTerrain(
        obstacles=frozenset(_position_list(payload.get("obstacles", []), "obstacles")),
        resource_cells=frozenset(_position_list(payload.get("resourceCells", []), "resourceCells")),
    )


def _world_from_dict(payload: Mapping[str, object]) -> ReferenceWorld:
    if payload.get("schemaVersion") != _WORLD_SCHEMA:
        raise ProcessExecutorError("unsupported world schema")
    players_payload = payload.get("players")
    if not isinstance(players_payload, Sequence) or isinstance(players_payload, (str, bytes)):
        raise ProcessExecutorError("world players must be an array")
    terrain_payload = payload.get("terrain")
    if not isinstance(terrain_payload, Mapping):
        raise ProcessExecutorError("world terrain must be an object")
    players: list[ReferencePlayer] = []
    for item in players_payload:
        if not isinstance(item, Mapping):
            raise ProcessExecutorError("world player must be an object")
        players.append(_player_from_dict(item))
    return ReferenceWorld(
        tick=_int(payload.get("tick"), "world tick"),
        resolved_tick_count=_int(payload.get("resolvedTickCount"), "resolved_tick_count"),
        rules_sha256=_text(payload.get("rulesSha256"), "rules_sha256"),
        seed=_int(payload.get("seed"), "world seed"),
        rng_stream_position=_int(payload.get("rngStreamPosition"), "rng_stream_position"),
        players=tuple(players),
        terrain=_terrain_from_dict(terrain_payload),
    )


def _command_from_dict(payload: Mapping[str, object]) -> ReferenceCommand:
    direction = payload.get("direction")
    return ReferenceCommand(
        actor_id=_text(payload.get("actorId"), "actor_id"),
        action=ReferenceActionKind(_text(payload.get("action"), "action")),
        direction=None if direction is None else ReferenceDirection(_text(direction, "direction")),
    )


def _turn_from_dict(payload: Mapping[str, object]) -> ReferenceTurn:
    commands_payload = payload.get("commands")
    if not isinstance(commands_payload, Sequence) or isinstance(commands_payload, (str, bytes)):
        raise ProcessExecutorError("turn commands must be an array")
    commands: list[ReferenceCommand] = []
    for item in commands_payload:
        if not isinstance(item, Mapping):
            raise ProcessExecutorError("turn command must be an object")
        commands.append(_command_from_dict(item))
    return ReferenceTurn(tick=_int(payload.get("tick"), "turn tick"), commands=tuple(commands))


def scenario_from_dict(payload: Mapping[str, object]) -> ReferenceScenario:
    """Reconstruct a registered reference scenario from its canonical payload."""
    if payload.get("schemaVersion") != _SCENARIO_SCHEMA:
        raise ProcessExecutorError("unsupported scenario schema")
    world_payload = payload.get("initialWorld")
    if not isinstance(world_payload, Mapping):
        raise ProcessExecutorError("scenario initialWorld must be an object")
    contestants = payload.get("contestantIds")
    if not isinstance(contestants, Sequence) or isinstance(contestants, (str, bytes)):
        raise ProcessExecutorError("scenario contestantIds must be an array")
    turns_payload = payload.get("turns")
    if not isinstance(turns_payload, Sequence) or isinstance(turns_payload, (str, bytes)):
        raise ProcessExecutorError("scenario turns must be an array")
    turns: list[ReferenceTurn] = []
    for item in turns_payload:
        if not isinstance(item, Mapping):
            raise ProcessExecutorError("scenario turn must be an object")
        turns.append(_turn_from_dict(item))
    return ReferenceScenario(
        scenario_id=_text(payload.get("scenarioId"), "scenario_id"),
        initial_world=_world_from_dict(world_payload),
        contestant_ids=tuple(_text(item, "contestant_id") for item in contestants),
        turns=tuple(turns),
    )


def _work_from_json(
    payload: Mapping[str, object],
) -> tuple[
    Mapping[str, object], tuple[SimulationRequest, ...], tuple[ReferenceScenario | None, ...]
]:
    if payload.get("schema_version") != WORK_ENVELOPE_VERSION:
        raise ProcessExecutorError("unsupported work envelope schema")
    scenarios_payload = payload.get("scenarios", {})
    if not isinstance(scenarios_payload, Mapping):
        raise ProcessExecutorError("work scenarios must be an object")
    scenario_by_digest: dict[str, ReferenceScenario] = {}
    for digest, item in scenarios_payload.items():
        digest_text = _text(digest, "scenario digest")
        if not isinstance(item, Mapping):
            raise ProcessExecutorError("work scenario must be an object")
        scenario = scenario_from_dict(item)
        if scenario.sha256 != digest_text:
            raise ProcessExecutorError("work scenario digest does not match its content")
        scenario_by_digest[digest_text] = scenario
    requests_payload = payload.get("requests")
    if not isinstance(requests_payload, Sequence) or isinstance(requests_payload, (str, bytes)):
        raise ProcessExecutorError("work requests must be an array")
    if not requests_payload:
        raise ProcessExecutorError("work requests must not be empty")
    requests: list[SimulationRequest] = []
    scenarios: list[ReferenceScenario | None] = []
    for entry in requests_payload:
        if not isinstance(entry, Mapping):
            raise ProcessExecutorError("work request entry must be an object")
        requests.append(request_from_json(entry))
        reference = entry.get("scenario_sha256")
        if reference is None:
            scenarios.append(None)
        else:
            digest = _text(reference, "scenario_sha256")
            scenario = scenario_by_digest.get(digest)
            if scenario is None:
                raise ProcessExecutorError(f"work scenario is missing: {digest}")
            scenarios.append(scenario)
    return payload, tuple(requests), tuple(scenarios)


def _build_backend(
    envelope: Mapping[str, object],
    requests: Sequence[SimulationRequest],
    scenarios: Sequence[ReferenceScenario | None],
) -> SimulatorBackend:
    backend_id = envelope.get("backend_id")
    for request in requests:
        if request.config.backend_id != backend_id:
            raise ProcessExecutorError("work item mixes backend ids")
    if backend_id == REFERENCE_BACKEND_ID:
        registered: dict[str, ReferenceScenario] = {}
        for scenario in scenarios:
            if scenario is None or scenario.sha256 in registered:
                continue
            registered[scenario.sha256] = scenario
        return ReferenceEngineBackend(tuple(registered.values()))
    raise ProcessExecutorError(f"worker cannot execute backend: {backend_id}")


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    line = sys.stdin.buffer.readline()
    if not line:
        print("process_worker: no work envelope on stdin", file=sys.stderr)
        return 2
    try:
        payload = json.loads(line.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ProcessExecutorError("work envelope must be an object")
        envelope, requests, scenarios = _work_from_json(payload)
        backend = _build_backend(envelope, requests, scenarios)
        results = [result_to_json(backend.simulate(request)) for request in requests]
        output = {
            "schema_version": RESULT_ENVELOPE_VERSION,
            "operation_id": envelope.get("operation_id"),
            "shard_id": envelope.get("shard_id"),
            "plan_sha256": envelope.get("plan_sha256"),
            "backend_id": envelope.get("backend_id"),
            "engine_version": envelope.get("engine_version"),
            "protocol_version": envelope.get("protocol_version"),
            "results": results,
            "errors": [],
        }
        sys.stdout.buffer.write(canonical_json_bytes(output) + b"\n")
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        print(f"process_worker: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
