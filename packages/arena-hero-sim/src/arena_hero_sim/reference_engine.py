"""Deterministic reference engine for the bounded M4 harvest/deposit slice."""

from __future__ import annotations

from dataclasses import dataclass, replace

from arena_hero_sim.reference_contracts import (
    REFERENCE_RULES,
    JsonScalar,
    Position,
    ReferenceActionKind,
    ReferenceCommand,
    ReferenceDirection,
    ReferenceEpisodeStatus,
    ReferenceEvent,
    ReferenceObservation,
    ReferencePlayer,
    ReferenceReplay,
    ReferenceReplayFrame,
    ReferenceRules,
    ReferenceScenario,
    ReferenceTerrain,
    ReferenceTurn,
    ReferenceUnit,
    ReferenceWorld,
    uuid_sort_key,
)

_DIRECTION_DELTA: dict[ReferenceDirection, Position] = {
    ReferenceDirection.UP: (0, -1),
    ReferenceDirection.DOWN: (0, 1),
    ReferenceDirection.LEFT: (-1, 0),
    ReferenceDirection.RIGHT: (1, 0),
}
_MASK_64 = (1 << 64) - 1


class UnsupportedReferenceSliceError(ValueError):
    """Raised before mutation when a scenario needs an unimplemented official rule."""


@dataclass(frozen=True, slots=True)
class ReferenceRng:
    """Counter-based SplitMix64 stream; state is the explicit draw position."""

    seed: int
    position: int = 0

    def next_u64(self) -> tuple[int, ReferenceRng]:
        value = (self.seed + (self.position + 1) * 0x9E3779B97F4A7C15) & _MASK_64
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK_64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK_64
        value ^= value >> 31
        return value & _MASK_64, ReferenceRng(self.seed, self.position + 1)


@dataclass(frozen=True, slots=True)
class ReferenceEpisodeResult:
    status: ReferenceEpisodeStatus
    final_world: ReferenceWorld
    replay: ReferenceReplay
    ticks_completed: int
    events: tuple[ReferenceEvent, ...]
    metrics: dict[str, float]


def _supercover_line(start: Position, end: Position) -> tuple[Position, ...]:
    cells: list[Position] = [start]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    nx = abs(dx)
    ny = abs(dy)
    sx = 0 if dx == 0 else (1 if dx > 0 else -1)
    sy = 0 if dy == 0 else (1 if dy > 0 else -1)
    x, y = start
    ix = iy = 0
    while ix < nx or iy < ny:
        next_x = (2 * ix + 1) * ny
        next_y = (2 * iy + 1) * nx
        if next_x < next_y:
            x += sx
            ix += 1
            cells.append((x, y))
        elif next_x > next_y:
            y += sy
            iy += 1
            cells.append((x, y))
        else:
            cells.extend(((x + sx, y), (x, y + sy)))
            x += sx
            y += sy
            ix += 1
            iy += 1
            cells.append((x, y))
    return tuple(dict.fromkeys(cells))


def _visible_from(origin: Position, radius: int, obstacles: frozenset[Position]) -> set[Position]:
    visible: set[Position] = set()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if abs(dx) + abs(dy) > radius:
                continue
            target = (origin[0] + dx, origin[1] + dy)
            line = _supercover_line(origin, target)
            if not any(cell in obstacles for cell in line[:-1]):
                visible.add(target)
    return visible


def observe_world(
    world: ReferenceWorld, rules: ReferenceRules = REFERENCE_RULES
) -> tuple[ReferenceObservation, ...]:
    """Project deterministic visibility and schema-level legal actions."""

    observations: list[ReferenceObservation] = []
    for player in world.players:
        visible = _visible_from(
            player.core.position, rules.core_vision_radius, world.terrain.obstacles
        )
        for unit in player.units:
            visible.update(
                _visible_from(unit.position, rules.worker_vision_radius, world.terrain.obstacles)
            )
        legal = tuple(
            (
                unit.id,
                tuple(action.value for action in ReferenceActionKind),
            )
            for unit in player.units
        )
        observations.append(ReferenceObservation(player.id, tuple(sorted(visible)), legal))
    return tuple(observations)


def _player_lookup(world: ReferenceWorld) -> dict[str, ReferencePlayer]:
    return {player.id: player for player in world.players}


def _unit_lookup(world: ReferenceWorld) -> dict[str, tuple[str, ReferenceUnit]]:
    return {unit.id: (player.id, unit) for player in world.players for unit in player.units}


def _replace_player(world: ReferenceWorld, updated: ReferencePlayer) -> ReferenceWorld:
    players = tuple(updated if player.id == updated.id else player for player in world.players)
    return replace(world, players=players)


def _replace_unit(world: ReferenceWorld, owner_id: str, updated: ReferenceUnit) -> ReferenceWorld:
    player = _player_lookup(world)[owner_id]
    units = tuple(updated if unit.id == updated.id else unit for unit in player.units)
    return _replace_player(world, replace(player, units=units))


def _occupancy(world: ReferenceWorld) -> dict[Position, list[tuple[str, str]]]:
    cells: dict[Position, list[tuple[str, str]]] = {}
    for player in world.players:
        cells.setdefault(player.core.position, []).append((player.id, player.core.id))
        for unit in player.units:
            cells.setdefault(unit.position, []).append((player.id, unit.id))
    return cells


def _event(
    events: list[tuple[str, str, str | None, str | None, Position | None, dict[str, JsonScalar]]],
    *,
    phase: str,
    event_type: str,
    actor_id: str | None = None,
    reason_code: str | None = None,
    position: Position | None = None,
    values: dict[str, JsonScalar] | None = None,
) -> None:
    scalar_values: dict[str, str | int | float | bool | None] = {}
    for key, value in (values or {}).items():
        if value is None or isinstance(value, str | int | float | bool):
            scalar_values[key] = value
        else:
            raise TypeError(f"event value {key} is not scalar")
    events.append((phase, event_type, actor_id, reason_code, position, scalar_values))


def _commands_for_turn(world: ReferenceWorld, turn: ReferenceTurn) -> dict[str, ReferenceCommand]:
    units = _unit_lookup(world)
    commands = {command.actor_id: command for command in turn.commands}
    unknown = sorted(set(commands) - set(units))
    if unknown:
        raise UnsupportedReferenceSliceError(f"commands reference unknown actors: {unknown}")
    return commands


def _movement_phase(
    world: ReferenceWorld,
    commands: dict[str, ReferenceCommand],
    rules: ReferenceRules,
    events: list[tuple[str, str, str | None, str | None, Position | None, dict[str, JsonScalar]]],
) -> ReferenceWorld:
    phase = "P05-global-movement"
    units = _unit_lookup(world)
    occupancy = _occupancy(world)
    intents: list[tuple[str, str, Position, Position]] = []
    for actor_id, command in commands.items():
        if command.action is not ReferenceActionKind.MOVE:
            continue
        owner_id, unit = units[actor_id]
        assert command.direction is not None
        dx, dy = _DIRECTION_DELTA[command.direction]
        destination = (unit.position[0] + dx, unit.position[1] + dy)
        intents.append((actor_id, owner_id, unit.position, destination))
    intents.sort(key=lambda item: uuid_sort_key(item[0]))

    moving_sources = {source for _, _, source, _ in intents}
    if any(destination in moving_sources for _, _, _, destination in intents):
        raise UnsupportedReferenceSliceError(
            "movement dependency chains, swaps, and cycles are outside the M4 reference slice"
        )

    failures: dict[str, str] = {}
    groups: dict[Position, list[tuple[str, str, Position, Position]]] = {}
    for intent in intents:
        actor_id, owner_id, _source, destination = intent
        if destination in world.terrain.obstacles:
            failures[actor_id] = "MOVE_BLOCKED_TERRAIN"
            continue
        occupants = occupancy.get(destination, [])
        if any(occupant_owner != owner_id for occupant_owner, _ in occupants):
            failures[actor_id] = "MOVE_DESTINATION_OCCUPIED"
            continue
        groups.setdefault(destination, []).append(intent)

    for destination in sorted(groups):
        arrivals = [intent for intent in groups[destination] if intent[0] not in failures]
        if len({intent[1] for intent in arrivals}) > 1:
            for actor_id, _, _, _ in arrivals:
                failures[actor_id] = "MOVE_CONTESTED"
            continue
        room = max(0, rules.cell_entity_capacity - len(occupancy.get(destination, [])))
        for actor_id, _, _, _ in sorted(arrivals, key=lambda item: uuid_sort_key(item[0]))[room:]:
            failures[actor_id] = "CELL_UNIT_LIMIT"

    updated = world
    for actor_id, owner_id, source, destination in intents:
        if actor_id in failures:
            _event(
                events,
                phase=phase,
                event_type="UNIT_MOVE_FAILED",
                actor_id=actor_id,
                reason_code=failures[actor_id],
                position=source,
            )
            continue
        unit = _unit_lookup(updated)[actor_id][1]
        updated = _replace_unit(updated, owner_id, replace(unit, position=destination))
        _event(
            events,
            phase=phase,
            event_type="UNIT_MOVE_SUCCEEDED",
            actor_id=actor_id,
            position=destination,
        )
    return updated


def _economy_phase(
    world: ReferenceWorld,
    commands: dict[str, ReferenceCommand],
    rules: ReferenceRules,
    events: list[tuple[str, str, str | None, str | None, Position | None, dict[str, JsonScalar]]],
) -> ReferenceWorld:
    phase = "P08-harvest-and-deposit"
    updated = world
    units = _unit_lookup(updated)
    harvest_by_cell: dict[Position, list[str]] = {}
    for actor_id, command in commands.items():
        if command.action is ReferenceActionKind.HARVEST:
            harvest_by_cell.setdefault(units[actor_id][1].position, []).append(actor_id)

    resource_cells = set(updated.terrain.resource_cells)
    for cell in sorted(harvest_by_cell):
        candidates: list[str] = []
        for actor_id in sorted(harvest_by_cell[cell], key=uuid_sort_key):
            unit = _unit_lookup(updated)[actor_id][1]
            if unit.cargo > 0:
                _event(
                    events,
                    phase=phase,
                    event_type="HARVEST_FAILED",
                    actor_id=actor_id,
                    reason_code="CARGO_FULL",
                    position=cell,
                )
            else:
                candidates.append(actor_id)
        if not candidates:
            continue
        if cell not in resource_cells:
            for actor_id in candidates:
                _event(
                    events,
                    phase=phase,
                    event_type="HARVEST_FAILED",
                    actor_id=actor_id,
                    reason_code="NOT_RESOURCE_CELL",
                    position=cell,
                )
            continue
        winner = candidates[0]
        owner_id, unit = _unit_lookup(updated)[winner]
        amount = min(rules.worker_cargo_capacity, rules.harvest_amount)
        updated = _replace_unit(updated, owner_id, replace(unit, cargo=amount))
        resource_cells.remove(cell)
        _event(
            events,
            phase=phase,
            event_type="HARVEST_SUCCEEDED",
            actor_id=winner,
            position=cell,
            values={"amount": amount, "source": "RESOURCE_NODE"},
        )
        for actor_id in candidates[1:]:
            _event(
                events,
                phase=phase,
                event_type="HARVEST_FAILED",
                actor_id=actor_id,
                reason_code="RESOURCE_DEPLETED",
                position=cell,
            )
    updated = replace(
        updated,
        terrain=ReferenceTerrain(updated.terrain.obstacles, frozenset(resource_cells)),
    )

    for actor_id in sorted(commands, key=uuid_sort_key):
        if commands[actor_id].action is not ReferenceActionKind.DEPOSIT:
            continue
        owner_id, unit = _unit_lookup(updated)[actor_id]
        player = _player_lookup(updated)[owner_id]
        if unit.cargo <= 0:
            _event(
                events,
                phase=phase,
                event_type="DEPOSIT_FAILED",
                actor_id=actor_id,
                reason_code="WORKER_EMPTY",
                position=unit.position,
            )
            continue
        if unit.position != player.core.position:
            _event(
                events,
                phase=phase,
                event_type="DEPOSIT_FAILED",
                actor_id=actor_id,
                reason_code="CORE_NOT_PRESENT",
                position=unit.position,
            )
            continue
        capacity = max(rules.core_min_capacity, len(player.units) * rules.core_capacity_per_unit)
        space = max(0, capacity - player.resources)
        if space == 0:
            _event(
                events,
                phase=phase,
                event_type="DEPOSIT_FAILED",
                actor_id=actor_id,
                reason_code="CORE_RESOURCE_FULL",
                position=unit.position,
                values={"capacity": capacity},
            )
            continue
        amount = min(unit.cargo, space)
        remaining = unit.cargo - amount
        updated = _replace_player(updated, replace(player, resources=player.resources + amount))
        current_unit = _unit_lookup(updated)[actor_id][1]
        updated = _replace_unit(updated, owner_id, replace(current_unit, cargo=remaining))
        _event(
            events,
            phase=phase,
            event_type="DEPOSIT_SUCCEEDED",
            actor_id=actor_id,
            position=unit.position,
            values={"amount": amount, "capacity": capacity, "remaining": remaining},
        )
    return updated


def settle_reference_turn(
    world: ReferenceWorld,
    turn: ReferenceTurn,
    rules: ReferenceRules = REFERENCE_RULES,
) -> tuple[ReferenceWorld, ReferenceReplayFrame]:
    """Run the explicit supported phases atomically for one tick."""

    if world.rules_sha256 != rules.sha256:
        raise UnsupportedReferenceSliceError("world rules digest is not implemented")
    if turn.tick != world.tick:
        raise ValueError("turn tick does not match world tick")
    commands = _commands_for_turn(world, turn)
    pre_hash = world.sha256
    rng_before = world.rng_stream_position
    raw_events: list[
        tuple[str, str, str | None, str | None, Position | None, dict[str, JsonScalar]]
    ] = []

    draft = _movement_phase(world, commands, rules, raw_events)
    draft = _economy_phase(draft, commands, rules, raw_events)
    committed = replace(
        draft,
        tick=draft.tick + 1,
        resolved_tick_count=draft.resolved_tick_count + 1,
    )
    observations = observe_world(committed, rules)
    events = tuple(
        ReferenceEvent(
            sequence=index,
            tick=world.tick,
            phase=phase,
            event_type=event_type,
            actor_id=actor_id,
            reason_code=reason_code,
            position=position,
            values=values,
        )
        for index, (phase, event_type, actor_id, reason_code, position, values) in enumerate(
            raw_events
        )
    )
    frame = ReferenceReplayFrame(
        tick=world.tick,
        pre_world_sha256=pre_hash,
        post_world_sha256=committed.sha256,
        rng_position_before=rng_before,
        rng_position_after=committed.rng_stream_position,
        observations=observations,
        events=events,
    )
    return committed, frame


def run_reference_episode(
    scenario: ReferenceScenario,
    *,
    request_id: str,
    episode_id: str,
    max_ticks: int,
    rules: ReferenceRules = REFERENCE_RULES,
) -> ReferenceEpisodeResult:
    if max_ticks < 1:
        raise ValueError("max_ticks must be positive")
    if scenario.initial_world.rules_sha256 != rules.sha256:
        raise UnsupportedReferenceSliceError("scenario rules digest is not implemented")
    world = scenario.initial_world
    initial_resources = {player.id: player.resources for player in world.players}
    frames: list[ReferenceReplayFrame] = []
    all_events: list[ReferenceEvent] = []
    selected_turns = scenario.turns[:max_ticks]
    for turn in selected_turns:
        world, frame = settle_reference_turn(world, turn, rules)
        frames.append(frame)
        all_events.extend(frame.events)
    status = (
        ReferenceEpisodeStatus.COMPLETE
        if len(selected_turns) == len(scenario.turns)
        else ReferenceEpisodeStatus.PARTIAL
    )
    replay = ReferenceReplay.create(
        request_id=request_id,
        episode_id=episode_id,
        scenario_sha256=scenario.sha256,
        rules_sha256=rules.sha256,
        initial_world_sha256=scenario.initial_world.sha256,
        final_world_sha256=world.sha256,
        status=status,
        frames=tuple(frames),
    )
    metrics: dict[str, float] = {
        "events": float(len(all_events)),
        "rng_draws": float(world.rng_stream_position - scenario.initial_world.rng_stream_position),
    }
    for player in world.players:
        metrics[f"final_resources.{player.id}"] = float(player.resources)
        metrics[f"resource_delta.{player.id}"] = float(
            player.resources - initial_resources[player.id]
        )
    return ReferenceEpisodeResult(
        status=status,
        final_world=world,
        replay=replay,
        ticks_completed=len(frames),
        events=tuple(all_events),
        metrics=metrics,
    )
