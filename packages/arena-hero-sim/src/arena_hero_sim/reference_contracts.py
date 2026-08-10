"""Immutable contracts for the deterministic M4 reference-engine slice."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from arena_hero_sim.serialization import canonical_json_bytes, content_sha256

Position = tuple[int, int]
JsonScalar = str | int | float | bool | None
_SAFE_INTEGER = 9_007_199_254_740_991
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase portable identifier")
    return normalized


def _sha256(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _safe_int(value: int, field_name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if abs(value) > _SAFE_INTEGER:
        raise ValueError(f"{field_name} exceeds the portable safe-integer range")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


def _position(value: Position, field_name: str) -> Position:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"{field_name} must be an immutable (x, y) tuple")
    return (_safe_int(value[0], f"{field_name}.x"), _safe_int(value[1], f"{field_name}.y"))


def _uuid4(value: str, field_name: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{field_name} must be a canonical UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field_name} must be a canonical UUIDv4")
    return value


def uuid_sort_key(value: str) -> bytes:
    return uuid.UUID(value).bytes


def _frozen_scalars(value: Mapping[str, JsonScalar]) -> Mapping[str, JsonScalar]:
    normalized: dict[str, JsonScalar] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("event value keys must be non-empty strings")
        if item is not None and not isinstance(item, str | int | float | bool):
            raise ValueError("event values must be JSON scalars")
        normalized[key] = item
    return MappingProxyType(dict(sorted(normalized.items())))


class ReferenceDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class ReferenceActionKind(StrEnum):
    WAIT = "WAIT"
    MOVE = "MOVE"
    HARVEST = "HARVEST"
    DEPOSIT = "DEPOSIT"


class ReferenceEpisodeStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


REFERENCE_EVENT_TYPES = frozenset(
    {
        "UNIT_MOVE_SUCCEEDED",
        "UNIT_MOVE_FAILED",
        "HARVEST_SUCCEEDED",
        "HARVEST_FAILED",
        "DEPOSIT_SUCCEEDED",
        "DEPOSIT_FAILED",
    }
)


@dataclass(frozen=True, slots=True)
class ReferenceRules:
    """Only the publicly attributable rules implemented by this slice."""

    schema_version: str = "arena.reference.rules.v1"
    rules_version: str = "v0.14-reference-harvest-v1"
    cell_entity_capacity: int = 2
    max_cells_per_tick: int = 1
    core_min_capacity: int = 10
    core_capacity_per_unit: int = 5
    core_vision_radius: int = 5
    worker_hp: int = 2
    worker_cargo_capacity: int = 2
    worker_vision_radius: int = 3
    harvest_amount: int = 1
    phase_order: tuple[str, ...] = (
        "P05-global-movement",
        "P08-harvest-and-deposit",
        "P15-invariant-check-and-commit",
        "P16-next-observation",
    )

    def __post_init__(self) -> None:
        for field_name in (
            "cell_entity_capacity",
            "max_cells_per_tick",
            "core_min_capacity",
            "core_capacity_per_unit",
            "core_vision_radius",
            "worker_hp",
            "worker_cargo_capacity",
            "worker_vision_radius",
            "harvest_amount",
        ):
            _safe_int(getattr(self, field_name), field_name, minimum=1)
        if len(self.phase_order) != len(set(self.phase_order)):
            raise ValueError("phase_order entries must be unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "rulesVersion": self.rules_version,
            "cellEntityCapacity": self.cell_entity_capacity,
            "maxCellsPerTick": self.max_cells_per_tick,
            "coreMinCapacity": self.core_min_capacity,
            "coreCapacityPerUnit": self.core_capacity_per_unit,
            "coreVisionRadius": self.core_vision_radius,
            "workerHp": self.worker_hp,
            "workerCargoCapacity": self.worker_cargo_capacity,
            "workerVisionRadius": self.worker_vision_radius,
            "harvestAmount": self.harvest_amount,
            "phaseOrder": list(self.phase_order),
            "supportedSlice": "world-visibility-basic-move-harvest-deposit",
        }

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())


REFERENCE_RULES = ReferenceRules()


@dataclass(frozen=True, slots=True)
class ReferenceCore:
    id: str
    position: Position

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid4(self.id, "core id"))
        object.__setattr__(self, "position", _position(self.position, "core position"))

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "position": list(self.position)}


@dataclass(frozen=True, slots=True)
class ReferenceUnit:
    id: str
    owner_id: str
    position: Position
    hp: int = REFERENCE_RULES.worker_hp
    cargo: int = 0
    unit_type: str = "WORKER"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid4(self.id, "unit id"))
        object.__setattr__(self, "owner_id", _identifier(self.owner_id, "unit owner_id"))
        object.__setattr__(self, "position", _position(self.position, "unit position"))
        _safe_int(self.hp, "unit hp", minimum=1)
        _safe_int(self.cargo, "unit cargo", minimum=0)
        if self.unit_type != "WORKER":
            raise ValueError("the M4 reference slice supports WORKER units only")
        if self.hp > REFERENCE_RULES.worker_hp:
            raise ValueError("worker hp exceeds the supported maximum")
        if self.cargo > REFERENCE_RULES.worker_cargo_capacity:
            raise ValueError("worker cargo exceeds the supported maximum")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "ownerId": self.owner_id,
            "position": list(self.position),
            "hp": self.hp,
            "cargo": self.cargo,
            "unitType": self.unit_type,
        }


@dataclass(frozen=True, slots=True)
class ReferencePlayer:
    id: str
    username: str
    resources: int
    core: ReferenceCore
    units: tuple[ReferenceUnit, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "player id"))
        username = self.username.strip()
        if not username:
            raise ValueError("username must not be empty")
        object.__setattr__(self, "username", username)
        _safe_int(self.resources, "player resources", minimum=0)
        units = tuple(sorted(self.units, key=lambda unit: uuid_sort_key(unit.id)))
        if len({unit.id for unit in units}) != len(units):
            raise ValueError("unit ids must be unique within a player")
        if any(unit.owner_id != self.id for unit in units):
            raise ValueError("unit owner_id must match its player")
        object.__setattr__(self, "units", units)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "username": self.username,
            "resources": self.resources,
            "core": self.core.to_dict(),
            "units": [unit.to_dict() for unit in self.units],
        }


@dataclass(frozen=True, slots=True)
class ReferenceTerrain:
    obstacles: frozenset[Position] = field(default_factory=frozenset)
    resource_cells: frozenset[Position] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        obstacles = frozenset(_position(item, "obstacle") for item in self.obstacles)
        resources = frozenset(_position(item, "resource cell") for item in self.resource_cells)
        if (0, 0) in obstacles:
            raise ValueError("[0,0] must remain passable")
        if obstacles & resources:
            raise ValueError("resource cells must not be obstacles")
        object.__setattr__(self, "obstacles", obstacles)
        object.__setattr__(self, "resource_cells", resources)

    def to_dict(self) -> dict[str, object]:
        return {
            "obstacles": [list(item) for item in sorted(self.obstacles)],
            "resourceCells": [list(item) for item in sorted(self.resource_cells)],
        }


@dataclass(frozen=True, slots=True)
class ReferenceWorld:
    tick: int
    resolved_tick_count: int
    rules_sha256: str
    seed: int
    rng_stream_position: int
    players: tuple[ReferencePlayer, ...]
    terrain: ReferenceTerrain

    def __post_init__(self) -> None:
        _safe_int(self.tick, "world tick", minimum=1)
        _safe_int(self.resolved_tick_count, "resolved_tick_count", minimum=0)
        if self.resolved_tick_count > self.tick:
            raise ValueError("resolved_tick_count must not exceed tick")
        object.__setattr__(self, "rules_sha256", _sha256(self.rules_sha256, "rules_sha256"))
        _safe_int(self.seed, "world seed", minimum=0)
        _safe_int(self.rng_stream_position, "rng_stream_position", minimum=0)
        players = tuple(sorted(self.players, key=lambda player: player.id))
        if not players or len({player.id for player in players}) != len(players):
            raise ValueError("world requires unique players")
        entity_ids = [player.core.id for player in players]
        entity_ids.extend(unit.id for player in players for unit in player.units)
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("entity ids must be globally unique")
        occupancy: dict[Position, int] = {}
        for player in players:
            occupancy[player.core.position] = occupancy.get(player.core.position, 0) + 1
            for unit in player.units:
                occupancy[unit.position] = occupancy.get(unit.position, 0) + 1
        if any(count > REFERENCE_RULES.cell_entity_capacity for count in occupancy.values()):
            raise ValueError("cell entity capacity exceeded")
        if any(cell in self.terrain.obstacles for cell in occupancy):
            raise ValueError("entities must not occupy obstacle cells")
        object.__setattr__(self, "players", players)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": "arena.reference.world.v1",
            "tick": self.tick,
            "resolvedTickCount": self.resolved_tick_count,
            "rulesSha256": self.rules_sha256,
            "seed": self.seed,
            "rngStreamPosition": self.rng_stream_position,
            "players": [player.to_dict() for player in self.players],
            "terrain": self.terrain.to_dict(),
        }

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class ReferenceCommand:
    actor_id: str
    action: ReferenceActionKind
    direction: ReferenceDirection | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_id", _uuid4(self.actor_id, "actor_id"))
        if self.action is ReferenceActionKind.MOVE and self.direction is None:
            raise ValueError("MOVE requires a direction")
        if self.action is not ReferenceActionKind.MOVE and self.direction is not None:
            raise ValueError("only MOVE accepts a direction")

    def to_dict(self) -> dict[str, object]:
        return {
            "actorId": self.actor_id,
            "action": self.action.value,
            "direction": None if self.direction is None else self.direction.value,
        }


@dataclass(frozen=True, slots=True)
class ReferenceTurn:
    tick: int
    commands: tuple[ReferenceCommand, ...]

    def __post_init__(self) -> None:
        _safe_int(self.tick, "turn tick", minimum=1)
        commands = tuple(sorted(self.commands, key=lambda command: uuid_sort_key(command.actor_id)))
        if len({command.actor_id for command in commands}) != len(commands):
            raise ValueError("a turn may contain at most one command per actor")
        object.__setattr__(self, "commands", commands)

    def to_dict(self) -> dict[str, object]:
        return {"tick": self.tick, "commands": [command.to_dict() for command in self.commands]}


@dataclass(frozen=True, slots=True)
class ReferenceScenario:
    scenario_id: str
    initial_world: ReferenceWorld
    contestant_ids: tuple[str, ...]
    turns: tuple[ReferenceTurn, ...]
    schema_version: str = "arena.reference.scenario.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _identifier(self.scenario_id, "scenario_id"))
        contestants = tuple(_identifier(item, "contestant_id") for item in self.contestant_ids)
        if contestants != tuple(player.id for player in self.initial_world.players):
            raise ValueError("contestant_ids must match initial-world player order")
        expected = tuple(range(self.initial_world.tick, self.initial_world.tick + len(self.turns)))
        if tuple(turn.tick for turn in self.turns) != expected:
            raise ValueError("turn ticks must be contiguous from initial_world.tick")
        object.__setattr__(self, "contestant_ids", contestants)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "scenarioId": self.scenario_id,
            "initialWorld": self.initial_world.to_dict(),
            "contestantIds": list(self.contestant_ids),
            "turns": [turn.to_dict() for turn in self.turns],
        }

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class ReferenceObservation:
    player_id: str
    visible_cells: tuple[Position, ...]
    legal_actions: tuple[tuple[str, tuple[str, ...]], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "player_id", _identifier(self.player_id, "player_id"))
        cells = tuple(sorted({_position(cell, "visible cell") for cell in self.visible_cells}))
        actions: list[tuple[str, tuple[str, ...]]] = []
        for actor_id, allowed in self.legal_actions:
            canonical_actor = _uuid4(actor_id, "legal action actor_id")
            names = tuple(dict.fromkeys(str(item) for item in allowed))
            if not set(names).issubset({action.value for action in ReferenceActionKind}):
                raise ValueError("observation contains an unknown legal action")
            actions.append((canonical_actor, names))
        actions.sort(key=lambda item: uuid_sort_key(item[0]))
        if len({actor_id for actor_id, _ in actions}) != len(actions):
            raise ValueError("legal action actors must be unique")
        object.__setattr__(self, "visible_cells", cells)
        object.__setattr__(self, "legal_actions", tuple(actions))

    def to_dict(self) -> dict[str, object]:
        return {
            "playerId": self.player_id,
            "visibleCells": [list(cell) for cell in self.visible_cells],
            "legalActions": [
                {"actorId": actor_id, "actions": list(actions)}
                for actor_id, actions in self.legal_actions
            ],
        }


@dataclass(frozen=True, slots=True)
class ReferenceEvent:
    sequence: int
    tick: int
    phase: str
    event_type: str
    actor_id: str | None = None
    reason_code: str | None = None
    position: Position | None = None
    values: Mapping[str, JsonScalar] = field(default_factory=dict)
    schema_version: str = "arena.reference.event.v1"

    def __post_init__(self) -> None:
        _safe_int(self.sequence, "event sequence", minimum=0)
        _safe_int(self.tick, "event tick", minimum=1)
        if self.phase not in REFERENCE_RULES.phase_order:
            raise ValueError("event phase is not part of the reference pipeline")
        if self.event_type not in REFERENCE_EVENT_TYPES:
            raise ValueError("event type is not implemented by the reference slice")
        if self.actor_id is not None:
            object.__setattr__(self, "actor_id", _uuid4(self.actor_id, "event actor_id"))
        if self.position is not None:
            object.__setattr__(self, "position", _position(self.position, "event position"))
        object.__setattr__(self, "values", _frozen_scalars(self.values))

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "sequence": self.sequence,
            "tick": self.tick,
            "phase": self.phase,
            "eventType": self.event_type,
            "actorId": self.actor_id,
            "reasonCode": self.reason_code,
            "position": None if self.position is None else list(self.position),
            "values": dict(self.values),
        }


@dataclass(frozen=True, slots=True)
class ReferenceReplayFrame:
    tick: int
    pre_world_sha256: str
    post_world_sha256: str
    rng_position_before: int
    rng_position_after: int
    observations: tuple[ReferenceObservation, ...]
    events: tuple[ReferenceEvent, ...]

    def __post_init__(self) -> None:
        _safe_int(self.tick, "frame tick", minimum=1)
        object.__setattr__(self, "pre_world_sha256", _sha256(self.pre_world_sha256, "pre hash"))
        object.__setattr__(self, "post_world_sha256", _sha256(self.post_world_sha256, "post hash"))
        if self.rng_position_after < self.rng_position_before:
            raise ValueError("rng stream position must be monotonic")
        if tuple(event.sequence for event in self.events) != tuple(range(len(self.events))):
            raise ValueError("event sequence must be contiguous within a frame")

    def to_dict(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "preWorldSha256": self.pre_world_sha256,
            "postWorldSha256": self.post_world_sha256,
            "rngPositionBefore": self.rng_position_before,
            "rngPositionAfter": self.rng_position_after,
            "observations": [item.to_dict() for item in self.observations],
            "events": [event.to_dict() for event in self.events],
        }


@dataclass(frozen=True, slots=True)
class ReferenceReplay:
    """Versioned canonical replay envelope with an integrity digest."""

    payload: Mapping[str, object]
    payload_sha256: str

    def __post_init__(self) -> None:
        payload = dict(self.payload)
        if payload.get("schemaVersion") != "arena.reference.replay.v1":
            raise ValueError("unsupported replay schema")
        required = {
            "schemaVersion",
            "requestId",
            "episodeId",
            "scenarioSha256",
            "rulesSha256",
            "initialWorldSha256",
            "finalWorldSha256",
            "status",
            "frames",
        }
        if set(payload) != required:
            raise ValueError("replay payload fields mismatch")
        _identifier(str(payload["requestId"]), "replay requestId")
        _identifier(str(payload["episodeId"]), "replay episodeId")
        for key in ("scenarioSha256", "rulesSha256", "initialWorldSha256", "finalWorldSha256"):
            _sha256(str(payload[key]), key)
        if str(payload["status"]) not in {status.value for status in ReferenceEpisodeStatus}:
            raise ValueError("unsupported replay status")
        frames = payload["frames"]
        if not isinstance(frames, list):
            raise ValueError("replay frames must be a list")
        previous = str(payload["initialWorldSha256"])
        expected_tick: int | None = None
        frame_fields = {
            "tick",
            "preWorldSha256",
            "postWorldSha256",
            "rngPositionBefore",
            "rngPositionAfter",
            "observations",
            "events",
        }
        event_fields = {
            "schemaVersion",
            "sequence",
            "tick",
            "phase",
            "eventType",
            "actorId",
            "reasonCode",
            "position",
            "values",
        }
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict) or set(frame) != frame_fields:
                raise ValueError("replay frame fields mismatch")
            tick = _safe_int(frame["tick"], "frame tick", minimum=1)
            if expected_tick is not None and tick != expected_tick:
                raise ValueError("replay frame ticks must be contiguous")
            expected_tick = tick + 1
            pre_hash = _sha256(str(frame["preWorldSha256"]), "frame pre hash")
            post_hash = _sha256(str(frame["postWorldSha256"]), "frame post hash")
            if pre_hash != previous:
                raise ValueError(f"replay frame hash chain breaks at index {index}")
            before = _safe_int(frame["rngPositionBefore"], "rng before", minimum=0)
            after = _safe_int(frame["rngPositionAfter"], "rng after", minimum=0)
            if after < before:
                raise ValueError("replay RNG position must be monotonic")
            observations = frame["observations"]
            events = frame["events"]
            if not isinstance(observations, list) or not isinstance(events, list):
                raise ValueError("replay observations/events must be lists")
            for sequence, event in enumerate(events):
                if not isinstance(event, dict) or set(event) != event_fields:
                    raise ValueError("replay event fields mismatch")
                if event["schemaVersion"] != "arena.reference.event.v1":
                    raise ValueError("unsupported replay event schema")
                if event["sequence"] != sequence or event["tick"] != tick:
                    raise ValueError("replay event ordering is invalid")
                if event["phase"] not in REFERENCE_RULES.phase_order:
                    raise ValueError("replay event phase is invalid")
                if event["eventType"] not in REFERENCE_EVENT_TYPES:
                    raise ValueError("replay event type is invalid")
            previous = post_hash
        if previous != str(payload["finalWorldSha256"]):
            raise ValueError("replay final hash is not bound to the final frame")
        object.__setattr__(self, "payload_sha256", _sha256(self.payload_sha256, "payload_sha256"))
        if content_sha256(payload) != self.payload_sha256:
            raise ValueError("replay payload digest mismatch")
        object.__setattr__(self, "payload", MappingProxyType(payload))

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        episode_id: str,
        scenario_sha256: str,
        rules_sha256: str,
        initial_world_sha256: str,
        final_world_sha256: str,
        status: ReferenceEpisodeStatus,
        frames: tuple[ReferenceReplayFrame, ...],
    ) -> ReferenceReplay:
        payload: dict[str, object] = {
            "schemaVersion": "arena.reference.replay.v1",
            "requestId": request_id,
            "episodeId": episode_id,
            "scenarioSha256": scenario_sha256,
            "rulesSha256": rules_sha256,
            "initialWorldSha256": initial_world_sha256,
            "finalWorldSha256": final_world_sha256,
            "status": status.value,
            "frames": [frame.to_dict() for frame in frames],
        }
        return cls(payload, content_sha256(payload))

    @property
    def final_world_sha256(self) -> str:
        return str(self.payload["finalWorldSha256"])

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(
            {"payload": dict(self.payload), "payloadSha256": self.payload_sha256}
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ReferenceReplay:
        raw = json.loads(data)
        if not isinstance(raw, dict) or set(raw) != {"payload", "payloadSha256"}:
            raise ValueError("replay envelope fields mismatch")
        payload = raw["payload"]
        if not isinstance(payload, dict):
            raise ValueError("replay payload must be an object")
        return cls(payload, str(raw["payloadSha256"]))
