from __future__ import annotations

from dataclasses import dataclass

from arena_hero_sim import (
    REFERENCE_RULES,
    ReferenceActionKind,
    ReferenceCommand,
    ReferenceCore,
    ReferenceDirection,
    ReferencePlayer,
    ReferenceTerrain,
    ReferenceTurn,
    ReferenceUnit,
    ReferenceWorld,
    settle_reference_turn,
)

Position = tuple[int, int]

CORE_ALPHA = "10000000-0000-4000-8000-000000000001"
CORE_BETA = "20000000-0000-4000-8000-000000000001"
UNIT_1 = "00000000-0000-4000-8000-000000000001"
UNIT_2 = "00000000-0000-4000-8000-000000000002"
UNIT_3 = "00000000-0000-4000-8000-000000000003"
UNIT_4 = "00000000-0000-4000-8000-000000000004"
UNIT_5 = "00000000-0000-4000-8000-000000000005"
UNIT_6 = "00000000-0000-4000-8000-000000000006"


@dataclass(frozen=True, slots=True)
class PlayerSpec:
    player_id: str
    core_id: str
    core_position: Position
    units: tuple[tuple[str, Position], ...]


def _world(*players: PlayerSpec, obstacles: frozenset[Position] = frozenset()) -> ReferenceWorld:
    return ReferenceWorld(
        tick=1,
        resolved_tick_count=0,
        rules_sha256=REFERENCE_RULES.sha256,
        seed=17,
        rng_stream_position=0,
        players=tuple(
            ReferencePlayer(
                id=spec.player_id,
                username=spec.player_id.title(),
                resources=5,
                core=ReferenceCore(spec.core_id, spec.core_position),
                units=tuple(
                    ReferenceUnit(unit_id, spec.player_id, position)
                    for unit_id, position in spec.units
                ),
            )
            for spec in players
        ),
        terrain=ReferenceTerrain(obstacles=obstacles),
    )


def _move(actor_id: str, direction: ReferenceDirection) -> ReferenceCommand:
    return ReferenceCommand(actor_id, ReferenceActionKind.MOVE, direction)


def _settle(
    world: ReferenceWorld, *commands: ReferenceCommand
) -> tuple[ReferenceWorld, dict[str, str | None], tuple[str | None, ...]]:
    committed, frame = settle_reference_turn(world, ReferenceTurn(world.tick, commands))
    reasons: dict[str, str | None] = {}
    event_order: list[str] = []
    for event in frame.events:
        assert event.actor_id is not None
        reasons[event.actor_id] = event.reason_code
        event_order.append(event.actor_id)
    return committed, reasons, tuple(event_order)


def _positions(world: ReferenceWorld) -> dict[str, Position]:
    return {unit.id: unit.position for player in world.players for unit in player.units}


def test_independent_moves_and_linear_dependency_chain_commit_atomically() -> None:
    world = _world(
        PlayerSpec(
            "alpha",
            CORE_ALPHA,
            (-5, 0),
            (
                (UNIT_1, (1, 0)),
                (UNIT_2, (1, 0)),
                (UNIT_3, (2, 0)),
                (UNIT_4, (2, 0)),
                (UNIT_5, (10, 0)),
            ),
        )
    )
    before = world.to_dict()
    before_sha256 = world.sha256

    committed, reasons, _ = _settle(
        world,
        _move(UNIT_5, ReferenceDirection.UP),
        _move(UNIT_4, ReferenceDirection.RIGHT),
        _move(UNIT_2, ReferenceDirection.RIGHT),
        _move(UNIT_3, ReferenceDirection.RIGHT),
        _move(UNIT_1, ReferenceDirection.RIGHT),
    )

    assert _positions(committed) == {
        UNIT_1: (2, 0),
        UNIT_2: (2, 0),
        UNIT_3: (3, 0),
        UNIT_4: (3, 0),
        UNIT_5: (10, -1),
    }
    assert set(reasons.values()) == {None}
    assert world.to_dict() == before
    assert world.sha256 == before_sha256


def test_multi_hop_dependency_failure_reaches_chain_head_with_oracle_precedence() -> None:
    world = _world(
        PlayerSpec(
            "alpha",
            CORE_ALPHA,
            (-5, 0),
            (
                (UNIT_1, (1, 0)),
                (UNIT_2, (1, 0)),
                (UNIT_3, (2, 0)),
                (UNIT_4, (2, 0)),
                (UNIT_5, (3, 0)),
                (UNIT_6, (3, 0)),
            ),
        ),
        obstacles=frozenset({(4, 0)}),
    )

    committed, reasons, _ = _settle(
        world,
        _move(UNIT_1, ReferenceDirection.RIGHT),
        _move(UNIT_2, ReferenceDirection.RIGHT),
        _move(UNIT_3, ReferenceDirection.RIGHT),
        _move(UNIT_4, ReferenceDirection.RIGHT),
        _move(UNIT_5, ReferenceDirection.DOWN),
        _move(UNIT_6, ReferenceDirection.RIGHT),
    )

    assert _positions(committed) == {
        UNIT_1: (1, 0),
        UNIT_2: (1, 0),
        UNIT_3: (2, 0),
        UNIT_4: (2, 0),
        UNIT_5: (3, 1),
        UNIT_6: (3, 0),
    }
    assert reasons == {
        UNIT_1: "CELL_UNIT_LIMIT",
        UNIT_2: "CELL_UNIT_LIMIT",
        UNIT_3: "MOVE_DEPENDENCY_FAILED",
        UNIT_4: "MOVE_DEPENDENCY_FAILED",
        UNIT_5: None,
        UNIT_6: "MOVE_BLOCKED_TERRAIN",
    }


def test_same_player_swap_and_cycle_are_legal() -> None:
    swap_world = _world(
        PlayerSpec(
            "alpha",
            CORE_ALPHA,
            (-5, 0),
            ((UNIT_1, (1, 0)), (UNIT_2, (2, 0))),
        )
    )
    swapped, swap_reasons, _ = _settle(
        swap_world,
        _move(UNIT_2, ReferenceDirection.LEFT),
        _move(UNIT_1, ReferenceDirection.RIGHT),
    )
    assert _positions(swapped) == {UNIT_1: (2, 0), UNIT_2: (1, 0)}
    assert swap_reasons == {UNIT_1: None, UNIT_2: None}

    cycle_world = _world(
        PlayerSpec(
            "alpha",
            CORE_ALPHA,
            (-5, 0),
            (
                (UNIT_1, (1, 0)),
                (UNIT_2, (2, 0)),
                (UNIT_3, (2, 1)),
                (UNIT_4, (1, 1)),
            ),
        )
    )
    cycled, cycle_reasons, _ = _settle(
        cycle_world,
        _move(UNIT_4, ReferenceDirection.UP),
        _move(UNIT_2, ReferenceDirection.DOWN),
        _move(UNIT_1, ReferenceDirection.RIGHT),
        _move(UNIT_3, ReferenceDirection.LEFT),
    )
    assert _positions(cycled) == {
        UNIT_1: (2, 0),
        UNIT_2: (2, 1),
        UNIT_3: (1, 1),
        UNIT_4: (1, 0),
    }
    assert set(cycle_reasons.values()) == {None}


def test_cross_player_destination_contest_and_hostile_swap_fail_explicitly() -> None:
    contested_world = _world(
        PlayerSpec("alpha", CORE_ALPHA, (-5, 0), ((UNIT_1, (1, 0)),)),
        PlayerSpec("beta", CORE_BETA, (5, 0), ((UNIT_4, (2, 1)),)),
    )
    contested, reasons, _ = _settle(
        contested_world,
        _move(UNIT_4, ReferenceDirection.UP),
        _move(UNIT_1, ReferenceDirection.RIGHT),
    )
    assert _positions(contested) == {UNIT_1: (1, 0), UNIT_4: (2, 1)}
    assert reasons == {UNIT_1: "MOVE_CONTESTED", UNIT_4: "MOVE_CONTESTED"}

    swap_world = _world(
        PlayerSpec("alpha", CORE_ALPHA, (-5, 0), ((UNIT_1, (1, 0)),)),
        PlayerSpec("beta", CORE_BETA, (5, 0), ((UNIT_4, (2, 0)),)),
    )
    swapped, swap_reasons, _ = _settle(
        swap_world,
        _move(UNIT_1, ReferenceDirection.RIGHT),
        _move(UNIT_4, ReferenceDirection.LEFT),
    )
    assert _positions(swapped) == {UNIT_1: (1, 0), UNIT_4: (2, 0)}
    assert swap_reasons == {
        UNIT_1: "MOVE_SWAP_BLOCKED",
        UNIT_4: "MOVE_SWAP_BLOCKED",
    }


def test_stationary_and_failed_departures_block_dependents() -> None:
    stationary_world = _world(
        PlayerSpec(
            "alpha",
            CORE_ALPHA,
            (2, 0),
            ((UNIT_1, (1, 0)), (UNIT_2, (2, 0))),
        )
    )
    stationary, stationary_reasons, _ = _settle(
        stationary_world,
        _move(UNIT_1, ReferenceDirection.RIGHT),
    )
    assert _positions(stationary)[UNIT_1] == (1, 0)
    assert stationary_reasons[UNIT_1] == "CELL_UNIT_LIMIT"

    hostile_world = _world(
        PlayerSpec("alpha", CORE_ALPHA, (-5, 0), ((UNIT_1, (1, 0)),)),
        PlayerSpec("beta", CORE_BETA, (5, 0), ((UNIT_4, (2, 0)),)),
    )
    hostile, hostile_reasons, _ = _settle(
        hostile_world,
        _move(UNIT_1, ReferenceDirection.RIGHT),
    )
    assert _positions(hostile)[UNIT_1] == (1, 0)
    assert hostile_reasons[UNIT_1] == "MOVE_DESTINATION_OCCUPIED"

    dependency_world = _world(
        PlayerSpec(
            "alpha",
            CORE_ALPHA,
            (-5, 0),
            (
                (UNIT_1, (1, 0)),
                (UNIT_2, (2, 0)),
                (UNIT_3, (2, 0)),
            ),
        ),
        obstacles=frozenset({(3, 0)}),
    )
    dependency, dependency_reasons, _ = _settle(
        dependency_world,
        _move(UNIT_1, ReferenceDirection.RIGHT),
        _move(UNIT_2, ReferenceDirection.DOWN),
        _move(UNIT_3, ReferenceDirection.RIGHT),
    )
    assert _positions(dependency) == {
        UNIT_1: (1, 0),
        UNIT_2: (2, 1),
        UNIT_3: (2, 0),
    }
    assert dependency_reasons == {
        UNIT_1: "MOVE_DEPENDENCY_FAILED",
        UNIT_2: None,
        UNIT_3: "MOVE_BLOCKED_TERRAIN",
    }


def test_capacity_uses_stable_raw_uuid_tie_break_and_event_order() -> None:
    world = _world(
        PlayerSpec(
            "alpha",
            CORE_ALPHA,
            (-5, 0),
            (
                (UNIT_3, (3, 0)),
                (UNIT_1, (1, 0)),
                (UNIT_2, (2, 1)),
            ),
        )
    )
    committed, reasons, event_order = _settle(
        world,
        _move(UNIT_3, ReferenceDirection.LEFT),
        _move(UNIT_2, ReferenceDirection.UP),
        _move(UNIT_1, ReferenceDirection.RIGHT),
    )

    assert _positions(committed) == {
        UNIT_1: (2, 0),
        UNIT_2: (2, 0),
        UNIT_3: (3, 0),
    }
    assert reasons == {UNIT_1: None, UNIT_2: None, UNIT_3: "CELL_UNIT_LIMIT"}
    assert event_order == (UNIT_1, UNIT_2, UNIT_3)
