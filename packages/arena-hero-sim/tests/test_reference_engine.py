from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from arena_hero_sim import (
    REFERENCE_BACKEND_ID,
    REFERENCE_ENGINE_VERSION,
    REFERENCE_FEATURES,
    REFERENCE_PROTOCOL_VERSION,
    REFERENCE_RULES,
    REFERENCE_RULESET,
    BackendRegistry,
    ReferenceActionKind,
    ReferenceCommand,
    ReferenceCore,
    ReferenceDirection,
    ReferenceEngineBackend,
    ReferenceEpisodeStatus,
    ReferencePlayer,
    ReferenceReplay,
    ReferenceRng,
    ReferenceScenario,
    ReferenceTerrain,
    ReferenceTurn,
    ReferenceUnit,
    ReferenceWorld,
    RulesetRef,
    SimulationRequest,
    SimulationStatus,
    SimulatorConfig,
    content_sha256,
    observe_world,
    run_reference_episode,
    verify_reference_replay,
)

CORE_1 = "10000000-0000-4000-8000-000000000001"
CORE_2 = "20000000-0000-4000-8000-000000000001"
UNIT_1 = "00000000-0000-4000-8000-000000000001"
UNIT_2 = "00000000-0000-4000-8000-000000000002"
UNIT_3 = "00000000-0000-4000-8000-000000000003"
UNIT_4 = "00000000-0000-4000-8000-000000000004"


def harvest_deposit_scenario() -> ReferenceScenario:
    world = ReferenceWorld(
        tick=1,
        resolved_tick_count=0,
        rules_sha256=REFERENCE_RULES.sha256,
        seed=7,
        rng_stream_position=0,
        players=(
            ReferencePlayer(
                id="alpha",
                username="Alpha",
                resources=5,
                core=ReferenceCore(CORE_1, (0, 0)),
                units=(ReferenceUnit(UNIT_1, "alpha", (3, 0)),),
            ),
        ),
        terrain=ReferenceTerrain(resource_cells=frozenset({(3, 0)})),
    )
    return ReferenceScenario(
        scenario_id="harvest-deposit-golden",
        initial_world=world,
        contestant_ids=("alpha",),
        turns=(
            ReferenceTurn(1, (ReferenceCommand(UNIT_1, ReferenceActionKind.HARVEST),)),
            ReferenceTurn(
                2,
                (ReferenceCommand(UNIT_1, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),),
            ),
            ReferenceTurn(
                3,
                (ReferenceCommand(UNIT_1, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),),
            ),
            ReferenceTurn(
                4,
                (ReferenceCommand(UNIT_1, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),),
            ),
            ReferenceTurn(5, (ReferenceCommand(UNIT_1, ReferenceActionKind.DEPOSIT),)),
        ),
    )


def request_for(
    scenario: ReferenceScenario,
    *,
    request_id: str = "request-1",
    episode_id: str = "episode-1",
    max_ticks: int | None = None,
    ruleset: RulesetRef = REFERENCE_RULESET,
    input_sha256: str | None = None,
    initial_sha256: str | None = None,
    parameters: dict[str, str] | None = None,
) -> SimulationRequest:
    return SimulationRequest(
        request_id=request_id,
        episode_id=episode_id,
        config=SimulatorConfig(
            backend_id=REFERENCE_BACKEND_ID,
            engine_version=REFERENCE_ENGINE_VERSION,
            ruleset=ruleset,
            seed=scenario.initial_world.seed,
            max_ticks=len(scenario.turns) if max_ticks is None else max_ticks,
            protocol_version=REFERENCE_PROTOCOL_VERSION,
            requested_features=REFERENCE_FEATURES,
            parameters={} if parameters is None else parameters,
        ),
        initial_state_sha256=(
            scenario.initial_world.sha256 if initial_sha256 is None else initial_sha256
        ),
        contestant_ids=scenario.contestant_ids,
        input_artifact_sha256=scenario.sha256 if input_sha256 is None else input_sha256,
    )


def registry_for(scenario: ReferenceScenario) -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(ReferenceEngineBackend((scenario,)))
    return registry


def test_reference_contracts_are_deeply_immutable_and_canonical() -> None:
    scenario = harvest_deposit_scenario()
    with pytest.raises(FrozenInstanceError):
        scenario.initial_world.__setattr__("tick", 2)
    with pytest.raises(AttributeError):
        scenario.initial_world.terrain.resource_cells.add((4, 0))  # type: ignore[attr-defined]

    reordered = replace(
        scenario.initial_world,
        players=tuple(reversed(scenario.initial_world.players)),
    )
    assert reordered.sha256 == scenario.initial_world.sha256
    assert REFERENCE_RULES.sha256 == content_sha256(REFERENCE_RULES.to_dict())


def test_reference_rng_has_stable_known_answer_and_explicit_position() -> None:
    rng = ReferenceRng(7)
    values: list[int] = []
    for _ in range(3):
        value, rng = rng.next_u64()
        values.append(value)
    assert values == [7191089600892374487, 309689372594955804, 16616101746815609346]
    assert rng.position == 3
    assert values == [ReferenceRng(7).next_u64()[0], *values[1:]]


def test_visibility_matches_ts_manhattan_supercover_boundary() -> None:
    world = ReferenceWorld(
        tick=1,
        resolved_tick_count=0,
        rules_sha256=REFERENCE_RULES.sha256,
        seed=1,
        rng_stream_position=0,
        players=(
            ReferencePlayer(
                "alpha",
                "Alpha",
                5,
                ReferenceCore(CORE_1, (0, 0)),
                (),
            ),
        ),
        terrain=ReferenceTerrain(obstacles=frozenset({(1, 0)})),
    )
    visible = set(observe_world(world)[0].visible_cells)
    assert (1, 0) in visible
    assert (2, 0) not in visible
    assert (0, 5) in visible
    assert (0, 6) not in visible


def test_ts_derived_harvest_move_deposit_golden() -> None:
    scenario = harvest_deposit_scenario()
    result = run_reference_episode(
        scenario,
        request_id="request-1",
        episode_id="episode-1",
        max_ticks=5,
    )
    player = result.final_world.players[0]
    assert result.status is ReferenceEpisodeStatus.COMPLETE
    assert result.ticks_completed == 5
    assert result.final_world.tick == 6
    assert result.final_world.resolved_tick_count == 5
    assert player.resources == 6
    assert player.units[0].position == (0, 0)
    assert player.units[0].cargo == 0
    assert result.final_world.terrain.resource_cells == frozenset()
    assert result.final_world.rng_stream_position == 0
    assert [event.event_type for event in result.events] == [
        "HARVEST_SUCCEEDED",
        "UNIT_MOVE_SUCCEEDED",
        "UNIT_MOVE_SUCCEEDED",
        "UNIT_MOVE_SUCCEEDED",
        "DEPOSIT_SUCCEEDED",
    ]
    assert [event.tick for event in result.events] == [1, 2, 3, 4, 5]
    assert result.events[0].values == {"amount": 1, "source": "RESOURCE_NODE"}
    assert result.events[-1].values == {"amount": 1, "capacity": 10, "remaining": 0}
    # Filled with stable known answers after the implementation is formatted.
    assert (
        result.final_world.sha256
        == "925922c82b45de82e75f2f078f747f7f96d12564aea326f1af4946a9a2a8efb8"
    )
    assert (
        result.replay.payload_sha256
        == "3098238b3acdd922ff01827de0d73d1188c680e9e9501eb4c33cbd830afa8cbc"
    )


def test_repeat_runs_are_byte_deterministic() -> None:
    scenario = harvest_deposit_scenario()
    first = run_reference_episode(
        scenario, request_id="request-1", episode_id="episode-1", max_ticks=5
    )
    second = run_reference_episode(
        scenario, request_id="request-1", episode_id="episode-1", max_ticks=5
    )
    assert first.final_world.sha256 == second.final_world.sha256
    assert first.replay.to_bytes() == second.replay.to_bytes()
    assert first.events == second.events


def test_replay_round_trip_binds_hash_chain_and_rejects_tamper() -> None:
    scenario = harvest_deposit_scenario()
    result = run_reference_episode(
        scenario, request_id="request-1", episode_id="episode-1", max_ticks=5
    )
    encoded = result.replay.to_bytes()
    decoded = ReferenceReplay.from_bytes(encoded)
    assert decoded.to_bytes() == encoded
    assert decoded.final_world_sha256 == result.final_world.sha256

    tampered = json.loads(encoded)
    tampered["payload"]["finalWorldSha256"] = "f" * 64
    with pytest.raises(ValueError, match=r"final hash|payload digest"):
        ReferenceReplay.from_bytes(json.dumps(tampered).encode())

    chain_tamper = json.loads(encoded)
    chain_tamper["payload"]["frames"][2]["preWorldSha256"] = "e" * 64
    chain_tamper["payloadSha256"] = content_sha256(chain_tamper["payload"])
    with pytest.raises(ValueError, match="hash chain"):
        ReferenceReplay.from_bytes(json.dumps(chain_tamper).encode())


def test_backend_complete_is_exactly_scoped_and_batch_matches_single() -> None:
    scenario = harvest_deposit_scenario()
    registry = registry_for(scenario)
    first_request = request_for(scenario)
    second_request = request_for(scenario, request_id="request-2", episode_id="episode-2")
    first = registry.simulate(first_request)
    batch = registry.simulate_batch((first_request, second_request))

    assert first.status is SimulationStatus.COMPLETE
    assert first.publishable is True
    assert first.final_world_sha256 is not None
    assert batch[0] == first
    assert tuple(result.request_id for result in batch) == ("request-1", "request-2")
    assert batch[1].final_world_sha256 == first.final_world_sha256


def test_partial_tick_budget_is_never_publishable() -> None:
    scenario = harvest_deposit_scenario()
    result = registry_for(scenario).simulate(request_for(scenario, max_ticks=3))
    assert result.status is SimulationStatus.PARTIAL
    assert result.publishable is False
    assert result.ticks_completed == 3
    assert result.final_world_sha256 is not None
    assert "tick budget" in result.errors[0]


def test_backend_unsupported_cases_fail_closed() -> None:
    scenario = harvest_deposit_scenario()
    requests = (
        request_for(
            scenario,
            ruleset=RulesetRef("arena-hero", "v0.14", "f" * 64),
        ),
        request_for(scenario, input_sha256="f" * 64),
        request_for(scenario, initial_sha256="e" * 64),
        request_for(scenario, parameters={"unversioned": "value"}),
    )
    for request in requests:
        result = registry_for(scenario).simulate(request)
        assert result.status is SimulationStatus.UNSUPPORTED
        assert result.publishable is False
        assert result.final_world_sha256 is None
        assert result.ticks_completed == 0
        assert result.errors


def test_movement_capacity_uses_raw_uuid_tie_break() -> None:
    world = ReferenceWorld(
        tick=1,
        resolved_tick_count=0,
        rules_sha256=REFERENCE_RULES.sha256,
        seed=3,
        rng_stream_position=0,
        players=(
            ReferencePlayer(
                "alpha",
                "Alpha",
                5,
                ReferenceCore(CORE_1, (0, 0)),
                (
                    ReferenceUnit(UNIT_3, "alpha", (2, 1)),
                    ReferenceUnit(UNIT_1, "alpha", (0, 1)),
                    ReferenceUnit(UNIT_2, "alpha", (1, 0)),
                ),
            ),
        ),
        terrain=ReferenceTerrain(),
    )
    scenario = ReferenceScenario(
        "same-player-capacity",
        world,
        ("alpha",),
        (
            ReferenceTurn(
                1,
                (
                    ReferenceCommand(UNIT_3, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),
                    ReferenceCommand(UNIT_1, ReferenceActionKind.MOVE, ReferenceDirection.RIGHT),
                    ReferenceCommand(UNIT_2, ReferenceActionKind.MOVE, ReferenceDirection.DOWN),
                ),
            ),
        ),
    )
    result = run_reference_episode(
        scenario, request_id="capacity", episode_id="capacity", max_ticks=1
    )
    positions = {unit.id: unit.position for unit in result.final_world.players[0].units}
    assert positions[UNIT_1] == (1, 1)
    assert positions[UNIT_2] == (1, 1)
    assert positions[UNIT_3] == (2, 1)
    assert result.events[-1].reason_code == "CELL_UNIT_LIMIT"


def test_cross_player_contest_fails_for_all_arrivals() -> None:
    world = ReferenceWorld(
        tick=1,
        resolved_tick_count=0,
        rules_sha256=REFERENCE_RULES.sha256,
        seed=3,
        rng_stream_position=0,
        players=(
            ReferencePlayer(
                "alpha",
                "Alpha",
                5,
                ReferenceCore(CORE_1, (-2, 0)),
                (ReferenceUnit(UNIT_1, "alpha", (-1, 0)),),
            ),
            ReferencePlayer(
                "beta",
                "Beta",
                5,
                ReferenceCore(CORE_2, (2, 0)),
                (ReferenceUnit(UNIT_4, "beta", (1, 0)),),
            ),
        ),
        terrain=ReferenceTerrain(),
    )
    scenario = ReferenceScenario(
        "cross-player-contest",
        world,
        ("alpha", "beta"),
        (
            ReferenceTurn(
                1,
                (
                    ReferenceCommand(UNIT_1, ReferenceActionKind.MOVE, ReferenceDirection.RIGHT),
                    ReferenceCommand(UNIT_4, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),
                ),
            ),
        ),
    )
    result = run_reference_episode(
        scenario, request_id="contest", episode_id="contest", max_ticks=1
    )
    positions = {
        unit.id: unit.position for player in result.final_world.players for unit in player.units
    }
    assert positions == {UNIT_1: (-1, 0), UNIT_4: (1, 0)}
    assert {event.reason_code for event in result.events} == {"MOVE_CONTESTED"}


def test_unimplemented_movement_dependency_returns_unsupported() -> None:
    world = ReferenceWorld(
        tick=1,
        resolved_tick_count=0,
        rules_sha256=REFERENCE_RULES.sha256,
        seed=5,
        rng_stream_position=0,
        players=(
            ReferencePlayer(
                "alpha",
                "Alpha",
                5,
                ReferenceCore(CORE_1, (0, 0)),
                (
                    ReferenceUnit(UNIT_1, "alpha", (1, 0)),
                    ReferenceUnit(UNIT_2, "alpha", (2, 0)),
                ),
            ),
        ),
        terrain=ReferenceTerrain(),
    )
    scenario = ReferenceScenario(
        "movement-chain-unsupported",
        world,
        ("alpha",),
        (
            ReferenceTurn(
                1,
                (
                    ReferenceCommand(UNIT_1, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),
                    ReferenceCommand(UNIT_2, ReferenceActionKind.MOVE, ReferenceDirection.LEFT),
                ),
            ),
        ),
    )
    result = registry_for(scenario).simulate(request_for(scenario))
    assert result.status is SimulationStatus.UNSUPPORTED
    assert result.publishable is False
    assert "chains" in result.errors[0]


def test_full_world_hash_properties_without_incremental_claim() -> None:
    scenario = harvest_deposit_scenario()
    base = scenario.initial_world
    assert (
        ReferenceEngineBackend((scenario,)).descriptor.capabilities.supports_incremental_world_hash
        is False
    )
    for x in range(-10, 11):
        moved_unit = replace(base.players[0].units[0], position=(x, 3), cargo=abs(x) % 2)
        changed_player = replace(base.players[0], resources=5 + abs(x), units=(moved_unit,))
        changed = replace(base, players=(changed_player,))
        assert changed.sha256 == content_sha256(changed.to_dict())
        assert changed.sha256 == changed.sha256
        assert changed.sha256 != base.sha256


def test_command_input_order_is_canonical() -> None:
    scenario = harvest_deposit_scenario()
    world = replace(
        scenario.initial_world,
        players=(
            replace(
                scenario.initial_world.players[0],
                units=(
                    ReferenceUnit(UNIT_2, "alpha", (4, 0)),
                    scenario.initial_world.players[0].units[0],
                ),
            ),
        ),
    )
    forward = ReferenceScenario(
        "command-order",
        world,
        ("alpha",),
        (
            ReferenceTurn(
                1,
                (
                    ReferenceCommand(UNIT_1, ReferenceActionKind.HARVEST),
                    ReferenceCommand(UNIT_2, ReferenceActionKind.WAIT),
                ),
            ),
        ),
    )
    reverse = replace(
        forward,
        turns=(ReferenceTurn(1, tuple(reversed(forward.turns[0].commands))),),
    )
    assert forward.sha256 == reverse.sha256
    left = run_reference_episode(forward, request_id="order", episode_id="order", max_ticks=1)
    right = run_reference_episode(reverse, request_id="order", episode_id="order", max_ticks=1)
    assert left.final_world.sha256 == right.final_world.sha256
    assert left.replay.to_bytes() == right.replay.to_bytes()


def test_replay_rejects_recomputed_event_tamper() -> None:
    result = run_reference_episode(
        harvest_deposit_scenario(),
        request_id="tamper",
        episode_id="tamper",
        max_ticks=5,
    )
    raw = json.loads(result.replay.to_bytes())
    raw["payload"]["frames"][0]["events"][0]["schemaVersion"] = "arena.reference.event.v99"
    raw["payloadSha256"] = content_sha256(raw["payload"])
    with pytest.raises(ValueError, match="event schema"):
        ReferenceReplay.from_bytes(json.dumps(raw).encode())

    raw = json.loads(result.replay.to_bytes())
    raw["payload"]["frames"][1]["events"][0]["sequence"] = 7
    raw["payloadSha256"] = content_sha256(raw["payload"])
    with pytest.raises(ValueError, match="event ordering"):
        ReferenceReplay.from_bytes(json.dumps(raw).encode())


def test_backend_registration_and_capabilities_are_fail_closed() -> None:
    scenario = harvest_deposit_scenario()
    with pytest.raises(ValueError, match="duplicate reference scenario id"):
        ReferenceEngineBackend((scenario, replace(scenario, turns=())))
    with pytest.raises(ValueError, match="duplicate reference scenario digest"):
        ReferenceEngineBackend((scenario, scenario))

    capabilities = ReferenceEngineBackend((scenario,)).descriptor.capabilities
    assert capabilities.supports_batch is True
    assert capabilities.supports_incremental_world_hash is False
    assert capabilities.supports_zero_copy is False
    assert "reference-harvest-deposit-v1" in capabilities.features
    assert "combat" not in capabilities.features


def test_observation_legal_actions_and_hash_tamper_are_explicit() -> None:
    scenario = harvest_deposit_scenario()
    observation = observe_world(scenario.initial_world)[0]
    assert observation.legal_actions == ((UNIT_1, ("WAIT", "MOVE", "HARVEST", "DEPOSIT")),)
    changed = replace(
        scenario.initial_world,
        players=(replace(scenario.initial_world.players[0], resources=6),),
    )
    assert changed.sha256 != scenario.initial_world.sha256


def test_reference_rng_and_rules_reject_invalid_state() -> None:
    with pytest.raises(ValueError, match="RNG seed"):
        ReferenceRng(-1)
    with pytest.raises(ValueError, match="cell_entity_capacity"):
        replace(REFERENCE_RULES, cell_entity_capacity=0)


def test_semantic_replay_verification_reexecutes_registered_scenario() -> None:
    scenario = harvest_deposit_scenario()
    result = run_reference_episode(
        scenario,
        request_id="semantic-replay",
        episode_id="semantic-replay",
        max_ticks=5,
    )
    reproduced = verify_reference_replay(
        scenario,
        ReferenceReplay.from_bytes(result.replay.to_bytes()),
    )
    assert reproduced.final_world.sha256 == result.final_world.sha256

    tampered = json.loads(result.replay.to_bytes())
    tampered["payload"]["frames"][0]["events"][0]["values"]["amount"] = 2
    tampered["payloadSha256"] = content_sha256(tampered["payload"])
    accepted_envelope = ReferenceReplay.from_bytes(json.dumps(tampered).encode())
    with pytest.raises(ValueError, match="does not reproduce"):
        verify_reference_replay(scenario, accepted_envelope)
