"""Canonical real-engine workloads, known answers, and differential gates."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from arena_hero_sim.backend import BackendDescriptor, SimulatorBackend
from arena_hero_sim.contracts import (
    RulesetRef,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
)
from arena_hero_sim.reference import (
    REFERENCE_MOVEMENT_RULE_IDENTITY,
    REFERENCE_PROTOCOL_VERSION,
    REFERENCE_RULESET,
    ReferenceEngineBackend,
)
from arena_hero_sim.reference_contracts import (
    REFERENCE_RULES,
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
    ReplayArtifactIdentity,
)
from arena_hero_sim.registry import BackendRegistry
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value
from arena_hero_sim.workload import WorkloadCase, WorkloadManifest

REFERENCE_WORKLOAD_RUN_SCHEMA = "arena.sim.workload-run.v1"
DIFFERENTIAL_REPORT_SCHEMA = "arena.sim.differential-report.v1"
CANONICAL_REFERENCE_WORKLOAD_ID = "reference-movement-dependency"
CANONICAL_REFERENCE_WORKLOAD_VERSION = "2026-08-10.v1"
CANONICAL_REFERENCE_WORKLOAD_SHA256 = (
    "7b7267499afa6032585f40d069e605cc767bbb473e889cbb8ebfc63c4193fc0c"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_CORE_ALPHA = "10000000-0000-4000-8000-000000000001"
_CORE_BETA = "20000000-0000-4000-8000-000000000001"
_UNIT_1 = "00000000-0000-4000-8000-000000000001"
_UNIT_2 = "00000000-0000-4000-8000-000000000002"
_UNIT_3 = "00000000-0000-4000-8000-000000000003"
_UNIT_4 = "00000000-0000-4000-8000-000000000004"
_UNIT_5 = "00000000-0000-4000-8000-000000000005"

_MOVEMENT_FEATURES = frozenset(
    {
        "full-world-hash",
        REFERENCE_MOVEMENT_RULE_IDENTITY,
        "versioned-replay-v1",
    }
)
_HARVEST_FEATURES = _MOVEMENT_FEATURES | frozenset({"reference-harvest-deposit-v1"})


class ReferenceWorkloadError(RuntimeError):
    """Raised when a reference workload cannot produce verified complete evidence."""


def _sha256(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _frozen_float_map(value: Mapping[str, float]) -> Mapping[str, float]:
    return MappingProxyType(dict(sorted((str(key), float(item)) for key, item in value.items())))


def _metric_json(value: float) -> JsonValue:
    if math.isfinite(value):
        return value
    if math.isnan(value):
        return {"invalid_non_finite": "nan"}
    return {"invalid_non_finite": "positive-infinity" if value > 0 else "negative-infinity"}


def _player(
    player_id: str,
    core_id: str,
    core_position: tuple[int, int],
    units: Sequence[tuple[str, tuple[int, int]]],
    *,
    resources: int = 5,
) -> ReferencePlayer:
    return ReferencePlayer(
        id=player_id,
        username=player_id.title(),
        resources=resources,
        core=ReferenceCore(core_id, core_position),
        units=tuple(ReferenceUnit(unit_id, player_id, position) for unit_id, position in units),
    )


def _world(
    *players: ReferencePlayer,
    seed: int,
    obstacles: frozenset[tuple[int, int]] = frozenset(),
    resource_cells: frozenset[tuple[int, int]] = frozenset(),
) -> ReferenceWorld:
    return ReferenceWorld(
        tick=1,
        resolved_tick_count=0,
        rules_sha256=REFERENCE_RULES.sha256,
        seed=seed,
        rng_stream_position=0,
        players=tuple(players),
        terrain=ReferenceTerrain(obstacles=obstacles, resource_cells=resource_cells),
    )


def _move(actor_id: str, direction: ReferenceDirection) -> ReferenceCommand:
    return ReferenceCommand(actor_id, ReferenceActionKind.MOVE, direction)


def _scenario(
    scenario_id: str,
    world: ReferenceWorld,
    *turns: Sequence[ReferenceCommand],
) -> ReferenceScenario:
    return ReferenceScenario(
        scenario_id=scenario_id,
        initial_world=world,
        contestant_ids=tuple(player.id for player in world.players),
        turns=tuple(
            ReferenceTurn(world.tick + index, tuple(commands))
            for index, commands in enumerate(turns)
        ),
    )


def _build_scenarios() -> tuple[ReferenceScenario, ...]:
    independent = _scenario(
        "independent-moves",
        _world(
            _player(
                "alpha",
                _CORE_ALPHA,
                (-5, 0),
                ((_UNIT_1, (1, 0)), (_UNIT_2, (8, 0))),
            ),
            seed=101,
        ),
        (
            _move(_UNIT_1, ReferenceDirection.RIGHT),
            _move(_UNIT_2, ReferenceDirection.UP),
        ),
    )
    linear_chain = _scenario(
        "linear-dependency-chain",
        _world(
            _player(
                "alpha",
                _CORE_ALPHA,
                (-5, 0),
                ((_UNIT_1, (1, 0)), (_UNIT_2, (2, 0)), (_UNIT_3, (3, 0))),
            ),
            seed=102,
        ),
        (
            _move(_UNIT_1, ReferenceDirection.RIGHT),
            _move(_UNIT_2, ReferenceDirection.RIGHT),
            _move(_UNIT_3, ReferenceDirection.RIGHT),
        ),
    )
    friendly_swap = _scenario(
        "friendly-swap",
        _world(
            _player(
                "alpha",
                _CORE_ALPHA,
                (-5, 0),
                ((_UNIT_1, (1, 0)), (_UNIT_2, (2, 0))),
            ),
            seed=103,
        ),
        (
            _move(_UNIT_1, ReferenceDirection.RIGHT),
            _move(_UNIT_2, ReferenceDirection.LEFT),
        ),
    )
    friendly_three_cycle = _scenario(
        "friendly-three-unit-cycle",
        _world(
            _player(
                "alpha",
                _CORE_ALPHA,
                (-5, 0),
                ((_UNIT_1, (1, 0)), (_UNIT_2, (1, 0)), (_UNIT_3, (2, 0))),
            ),
            seed=104,
        ),
        (
            _move(_UNIT_1, ReferenceDirection.RIGHT),
            _move(_UNIT_2, ReferenceDirection.RIGHT),
            _move(_UNIT_3, ReferenceDirection.LEFT),
        ),
    )
    hostile_swap = _scenario(
        "hostile-swap-rejection",
        _world(
            _player("alpha", _CORE_ALPHA, (-5, 0), ((_UNIT_1, (1, 0)),)),
            _player("beta", _CORE_BETA, (5, 0), ((_UNIT_4, (2, 0)),)),
            seed=105,
        ),
        (
            _move(_UNIT_1, ReferenceDirection.RIGHT),
            _move(_UNIT_4, ReferenceDirection.LEFT),
        ),
    )
    contested_target = _scenario(
        "cross-player-contested-target",
        _world(
            _player("alpha", _CORE_ALPHA, (-5, 0), ((_UNIT_1, (1, 0)),)),
            _player("beta", _CORE_BETA, (5, 0), ((_UNIT_4, (2, 1)),)),
            seed=106,
        ),
        (
            _move(_UNIT_1, ReferenceDirection.RIGHT),
            _move(_UNIT_4, ReferenceDirection.UP),
        ),
    )
    failed_occupant = _scenario(
        "failed-occupant-blocks-dependent",
        _world(
            _player(
                "alpha",
                _CORE_ALPHA,
                (-5, 0),
                ((_UNIT_1, (1, 0)), (_UNIT_2, (2, 0))),
            ),
            seed=107,
            obstacles=frozenset({(3, 0)}),
        ),
        (
            _move(_UNIT_1, ReferenceDirection.RIGHT),
            _move(_UNIT_2, ReferenceDirection.RIGHT),
        ),
    )
    uuid_tie_break = _scenario(
        "uuid-raw-byte-tie-break",
        _world(
            _player(
                "alpha",
                _CORE_ALPHA,
                (-5, 0),
                ((_UNIT_1, (1, 0)), (_UNIT_2, (2, 1)), (_UNIT_3, (3, 0))),
            ),
            seed=108,
        ),
        (
            _move(_UNIT_1, ReferenceDirection.RIGHT),
            _move(_UNIT_2, ReferenceDirection.UP),
            _move(_UNIT_3, ReferenceDirection.LEFT),
        ),
    )
    harvest_deposit = _scenario(
        "harvest-deposit-golden",
        _world(
            _player("alpha", _CORE_ALPHA, (0, 0), ((_UNIT_1, (3, 0)),)),
            seed=109,
            resource_cells=frozenset({(3, 0)}),
        ),
        (ReferenceCommand(_UNIT_1, ReferenceActionKind.HARVEST),),
        (_move(_UNIT_1, ReferenceDirection.LEFT),),
        (_move(_UNIT_1, ReferenceDirection.LEFT),),
        (_move(_UNIT_1, ReferenceDirection.LEFT),),
        (ReferenceCommand(_UNIT_1, ReferenceActionKind.DEPOSIT),),
    )
    return (
        independent,
        linear_chain,
        friendly_swap,
        friendly_three_cycle,
        hostile_swap,
        contested_target,
        failed_occupant,
        uuid_tie_break,
        harvest_deposit,
    )


@dataclass(frozen=True, slots=True)
class KnownAnswer:
    """Frozen semantic answer for one content-addressed public scenario."""

    scenario_id: str
    scenario_sha256: str
    initial_world_sha256: str
    status: SimulationStatus
    ticks_completed: int
    final_world_sha256: str
    metrics: Mapping[str, float]
    required_artifact_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_sha256", _sha256(self.scenario_sha256, "scenario"))
        object.__setattr__(
            self,
            "initial_world_sha256",
            _sha256(self.initial_world_sha256, "initial world"),
        )
        object.__setattr__(
            self,
            "final_world_sha256",
            _sha256(self.final_world_sha256, "final world"),
        )
        if self.status is not SimulationStatus.COMPLETE:
            raise ValueError("known answers must be complete")
        if self.ticks_completed < 1:
            raise ValueError("known answers must complete at least one tick")
        metrics = _frozen_float_map(self.metrics)
        if not all(math.isfinite(value) for value in metrics.values()):
            raise ValueError("known-answer metrics must be finite")
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "required_artifact_refs", tuple(self.required_artifact_refs))


# Generated once from the readable reference engine and intentionally frozen. Any change must be
# reviewed as a rules/scenario contract change rather than silently refreshing benchmark answers.
_KNOWN_ANSWERS: Mapping[str, KnownAnswer] = MappingProxyType(
    {
        "independent-moves": KnownAnswer(
            scenario_id="independent-moves",
            scenario_sha256="b5bdb4d31a6c40a09c4d070c4f143056ba297b57d9cd6c78f5e80c641bb125fd",
            initial_world_sha256="28dbce3268e5b891760bb9aac55a0e02ea29b2b73ec86e53a40dd2de872b0c91",
            status=SimulationStatus.COMPLETE,
            ticks_completed=1,
            final_world_sha256="0ef6598b12e7e98cc4de3012e50d2dcccbc11c5259105b1e64ad9b23d6757abc",
            metrics={
                "events": 2.0,
                "final_resources.alpha": 5.0,
                "resource_delta.alpha": 0.0,
                "rng_draws": 0.0,
            },
            required_artifact_refs=(
                "replay-sha256:27a9cb2d2f202bc879de25c0b89f0e42f0eeb97877a1e01eaf67dc971783c839",
            ),
        ),
        "linear-dependency-chain": KnownAnswer(
            scenario_id="linear-dependency-chain",
            scenario_sha256="46b07a9d11f37a73a38e6d654e4192994c1205dcff5ae6f8263dda939728d4d0",
            initial_world_sha256="554b3f7f751387a566e26f615e506b8ebf2ed56fcc07be20cef7ea6620c0692e",
            status=SimulationStatus.COMPLETE,
            ticks_completed=1,
            final_world_sha256="5819747db093915a16c57bd045bdcefd7bf3c77df46cd3a7864db0dfbc32e552",
            metrics={
                "events": 3.0,
                "final_resources.alpha": 5.0,
                "resource_delta.alpha": 0.0,
                "rng_draws": 0.0,
            },
            required_artifact_refs=(
                "replay-sha256:b2c5442fb049a84f130577b81f36e381283049e98de331a630fdffc5c5ca1bff",
            ),
        ),
        "friendly-swap": KnownAnswer(
            scenario_id="friendly-swap",
            scenario_sha256="1feef40bb0ef0844e4d5b8761463467241708334e76c5a29f9c6c0a6563ee74e",
            initial_world_sha256="7adeba40297b2dbd606b12ee0ae47858e70b70a28cbb37d55c275cd828c0a09b",
            status=SimulationStatus.COMPLETE,
            ticks_completed=1,
            final_world_sha256="a75efca161508ba60d7ee52c4a36f4b8cca3f5082284f45bc85e7ef11b5541a9",
            metrics={
                "events": 2.0,
                "final_resources.alpha": 5.0,
                "resource_delta.alpha": 0.0,
                "rng_draws": 0.0,
            },
            required_artifact_refs=(
                "replay-sha256:8cd5c6b28f2d3cdd8e396f9de3d13f876565a9742252154944bfa76dc38d0a56",
            ),
        ),
        "friendly-three-unit-cycle": KnownAnswer(
            scenario_id="friendly-three-unit-cycle",
            scenario_sha256="2f10fff3eadfb165303ab64441f39e86e297de7a592be5a54705bd69c98a9c0f",
            initial_world_sha256="ff879252219a59147bbd7f1cce62364565a7be3ec074a744b7303883f42bca1e",
            status=SimulationStatus.COMPLETE,
            ticks_completed=1,
            final_world_sha256="f4b74d2007d3c6113d00ce301b6bb7369293a0a88f47af67936ea3e024841216",
            metrics={
                "events": 3.0,
                "final_resources.alpha": 5.0,
                "resource_delta.alpha": 0.0,
                "rng_draws": 0.0,
            },
            required_artifact_refs=(
                "replay-sha256:dfcc6a1ad0fccca4f351b877147196b9a6f4c0138bd15a0eea4c5f24ca1a7c59",
            ),
        ),
        "hostile-swap-rejection": KnownAnswer(
            scenario_id="hostile-swap-rejection",
            scenario_sha256="13229bf01805a23bbb00c0d2aaea9dee122e1783470b1edff7751f17b8d7b270",
            initial_world_sha256="9e90b73d0d800abb04415a2c9eb8fc49e0aba8f93093fe7b0ede3de8f7ca7cea",
            status=SimulationStatus.COMPLETE,
            ticks_completed=1,
            final_world_sha256="0a5fb032b8966d26cc5051d8e55a18d8ed7afa4f69b90ed870dc89eab8fff6b7",
            metrics={
                "events": 2.0,
                "final_resources.alpha": 5.0,
                "final_resources.beta": 5.0,
                "resource_delta.alpha": 0.0,
                "resource_delta.beta": 0.0,
                "rng_draws": 0.0,
            },
            required_artifact_refs=(
                "replay-sha256:cfc29746066bc67304ed9d2e145025c8ac67d7e1bdc80f6c82677bc9d0a2bd9c",
            ),
        ),
        "cross-player-contested-target": KnownAnswer(
            scenario_id="cross-player-contested-target",
            scenario_sha256="32ecb3cbafb4902303a0fe295d881446895752f8b7824690ec7532dda8c8e1f1",
            initial_world_sha256="4ef61df5235f610858f20e26f3b403814649e3dfa523d5a401da2dd25c2b9d89",
            status=SimulationStatus.COMPLETE,
            ticks_completed=1,
            final_world_sha256="0bf8b8990edf48745ef5840f771a1f3bd1449e2dd5ae23d82d254d6f2d5117f3",
            metrics={
                "events": 2.0,
                "final_resources.alpha": 5.0,
                "final_resources.beta": 5.0,
                "resource_delta.alpha": 0.0,
                "resource_delta.beta": 0.0,
                "rng_draws": 0.0,
            },
            required_artifact_refs=(
                "replay-sha256:170bca049c5bff04cef68a8444be5c15b93d8b11f5b2fdb65d14df0e0955cb9e",
            ),
        ),
        "failed-occupant-blocks-dependent": KnownAnswer(
            scenario_id="failed-occupant-blocks-dependent",
            scenario_sha256="e8bad15465aa3ebd90d603cf4ff445f5ae07aafa9ba9ab3d6f3404ef39d22280",
            initial_world_sha256="bb6f56f49de5d5394f4a69ddb5b3b03a93fdf1df15666aa6cf7b91de04d74556",
            status=SimulationStatus.COMPLETE,
            ticks_completed=1,
            final_world_sha256="9cb6a95d829b11f502c03262bbf59aeafcae97b30a6188e041b291d96b1feb2d",
            metrics={
                "events": 2.0,
                "final_resources.alpha": 5.0,
                "resource_delta.alpha": 0.0,
                "rng_draws": 0.0,
            },
            required_artifact_refs=(
                "replay-sha256:11fdf860bef86b35c4415bd01199079d6fe5eb093993bdc513c4e3db782a10c0",
            ),
        ),
        "uuid-raw-byte-tie-break": KnownAnswer(
            scenario_id="uuid-raw-byte-tie-break",
            scenario_sha256="54133d3ea08f3115e92b1936abd9c758cbff6209ddffa6386ed2c516e79c387d",
            initial_world_sha256="b37c561cabcd0ab3282bdee301b4567bcbaf19ff7150bc14e1c258cf05ced397",
            status=SimulationStatus.COMPLETE,
            ticks_completed=1,
            final_world_sha256="b370e18df55d13fca4776406dad9ee9db5d879aa5674d0b569f2f9aa96deb420",
            metrics={
                "events": 3.0,
                "final_resources.alpha": 5.0,
                "resource_delta.alpha": 0.0,
                "rng_draws": 0.0,
            },
            required_artifact_refs=(
                "replay-sha256:8d21f462482ed18c77617b8639a71e5b4df3b97e4f5473c09e244c2bedff8c58",
            ),
        ),
        "harvest-deposit-golden": KnownAnswer(
            scenario_id="harvest-deposit-golden",
            scenario_sha256="0a11029df332cc457a85745db60ce2dadf3748433aa7441f8a41f2f2d1f9131f",
            initial_world_sha256="85aee93dd6843b934820f6259dad4889e1f98d4efd2d294bcd09acfc850a9cb0",
            status=SimulationStatus.COMPLETE,
            ticks_completed=5,
            final_world_sha256="51b0c8138eb0aaa3ebf80ebc4e1c812ebc74a439164f85267fb986ca22ae3a8e",
            metrics={
                "events": 5.0,
                "final_resources.alpha": 6.0,
                "resource_delta.alpha": 1.0,
                "rng_draws": 0.0,
            },
            required_artifact_refs=(
                "replay-sha256:4116d3d4a1deb02d52d53bb02f70f85766b7d43bede4e5f8457314edc0909cf0",
            ),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class VerifiedReferenceScenario:
    scenario_sha256: str
    initial_world_sha256: str
    scenario: ReferenceScenario
    known_answer: KnownAnswer

    def __post_init__(self) -> None:
        if self.scenario.sha256 != self.scenario_sha256:
            raise ValueError("registered scenario bytes do not match the declared digest")
        if self.scenario.initial_world.sha256 != self.initial_world_sha256:
            raise ValueError("registered initial world does not match the declared digest")
        if self.known_answer.scenario_id != self.scenario.scenario_id:
            raise ValueError("known answer scenario id mismatch")
        if self.known_answer.scenario_sha256 != self.scenario_sha256:
            raise ValueError("known answer scenario digest mismatch")
        if self.known_answer.initial_world_sha256 != self.initial_world_sha256:
            raise ValueError("known answer initial-world digest mismatch")


class ReferenceScenarioRegistry:
    """Digest-addressed scenario provider that verifies bytes and known answers on entry."""

    def __init__(self, records: Iterable[VerifiedReferenceScenario]) -> None:
        by_digest: dict[str, VerifiedReferenceScenario] = {}
        ids: set[str] = set()
        for record in records:
            if record.scenario_sha256 in by_digest:
                raise ValueError(f"duplicate scenario digest: {record.scenario_sha256}")
            if record.scenario.scenario_id in ids:
                raise ValueError(f"duplicate scenario id: {record.scenario.scenario_id}")
            by_digest[record.scenario_sha256] = record
            ids.add(record.scenario.scenario_id)
        if not by_digest:
            raise ValueError("reference scenario registry must not be empty")
        self._by_digest = MappingProxyType(by_digest)

    @property
    def scenarios(self) -> tuple[ReferenceScenario, ...]:
        return tuple(record.scenario for record in self._by_digest.values())

    def resolve(self, case: WorkloadCase) -> VerifiedReferenceScenario:
        try:
            record = self._by_digest[case.scenario_sha256]
        except KeyError as error:
            raise ReferenceWorkloadError(
                f"missing registered scenario: {case.scenario_sha256}"
            ) from error
        scenario = record.scenario
        problems: list[str] = []
        if case.initial_state_sha256 != record.initial_world_sha256:
            problems.append("initial world digest")
        if case.seed != scenario.initial_world.seed:
            problems.append("seed")
        if case.max_ticks != len(scenario.turns):
            problems.append("tick budget")
        if case.contestant_ids != scenario.contestant_ids:
            problems.append("contestants")
        if case.parameters:
            problems.append("unversioned parameters")
        if problems:
            raise ReferenceWorkloadError(
                f"workload case {case.case_id} mismatches registered scenario: {', '.join(problems)}"
            )
        return record


def canonical_reference_scenario_registry() -> ReferenceScenarioRegistry:
    scenarios = _build_scenarios()
    records: list[VerifiedReferenceScenario] = []
    for scenario in scenarios:
        try:
            answer = _KNOWN_ANSWERS[scenario.scenario_id]
        except KeyError as error:
            raise ReferenceWorkloadError(
                f"missing frozen known answer: {scenario.scenario_id}"
            ) from error
        records.append(
            VerifiedReferenceScenario(
                scenario_sha256=answer.scenario_sha256,
                initial_world_sha256=answer.initial_world_sha256,
                scenario=scenario,
                known_answer=answer,
            )
        )
    return ReferenceScenarioRegistry(records)


def canonical_reference_workload_manifest() -> WorkloadManifest:
    scenarios = _build_scenarios()
    manifest = WorkloadManifest(
        workload_id=CANONICAL_REFERENCE_WORKLOAD_ID,
        workload_version=CANONICAL_REFERENCE_WORKLOAD_VERSION,
        ruleset=REFERENCE_RULESET,
        cases=tuple(
            WorkloadCase(
                case_id=scenario.scenario_id,
                scenario_sha256=scenario.sha256,
                initial_state_sha256=scenario.initial_world.sha256,
                seed=scenario.initial_world.seed,
                max_ticks=len(scenario.turns),
                contestant_ids=scenario.contestant_ids,
                requested_features=(
                    _HARVEST_FEATURES
                    if scenario.scenario_id == "harvest-deposit-golden"
                    else _MOVEMENT_FEATURES
                ),
                labels={"scenario": scenario.scenario_id, "source": "public-synthetic"},
            )
            for scenario in scenarios
        ),
        metadata={
            "coverage": "reference-movement-and-harvest-known-answers",
            "production_claim": "false",
            "source": "public-synthetic",
        },
    )
    if manifest.sha256 != CANONICAL_REFERENCE_WORKLOAD_SHA256:
        raise ReferenceWorkloadError("canonical workload digest differs from the frozen contract")
    return manifest


@dataclass(frozen=True, slots=True)
class WorkloadBackendIdentity:
    backend_id: str
    engine_version: str
    protocol_version: str
    features: tuple[str, ...]
    execution_modes: tuple[str, ...]
    max_batch_size: int
    supports_batch: bool
    supports_incremental_world_hash: bool
    supports_zero_copy: bool
    interchange_formats: tuple[str, ...]

    @classmethod
    def from_descriptor(
        cls, descriptor: BackendDescriptor, *, protocol_version: str
    ) -> WorkloadBackendIdentity:
        capabilities = descriptor.capabilities
        return cls(
            backend_id=descriptor.backend_id,
            engine_version=descriptor.engine_version,
            protocol_version=protocol_version,
            features=tuple(sorted(capabilities.features)),
            execution_modes=tuple(sorted(capabilities.execution_modes)),
            max_batch_size=capabilities.max_batch_size,
            supports_batch=capabilities.supports_batch,
            supports_incremental_world_hash=capabilities.supports_incremental_world_hash,
            supports_zero_copy=capabilities.supports_zero_copy,
            interchange_formats=tuple(sorted(capabilities.interchange_formats)),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "backend_id": self.backend_id,
                "engine_version": self.engine_version,
                "protocol_version": self.protocol_version,
                "features": self.features,
                "execution_modes": self.execution_modes,
                "max_batch_size": self.max_batch_size,
                "supports_batch": self.supports_batch,
                "supports_incremental_world_hash": self.supports_incremental_world_hash,
                "supports_zero_copy": self.supports_zero_copy,
                "interchange_formats": self.interchange_formats,
            }
        )
        assert isinstance(value, dict)
        return value


@dataclass(frozen=True, slots=True)
class WorkloadEpisodeResult:
    case_id: str
    repetition: int
    request_id: str
    episode_id: str
    backend_id: str
    engine_version: str
    rules_sha256: str
    seed: int
    status: SimulationStatus
    publishable: bool
    ticks_completed: int
    final_world_sha256: str | None
    metrics: Mapping[str, float] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", _frozen_float_map(self.metrics))
        object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))
        object.__setattr__(self, "errors", tuple(self.errors))

    @classmethod
    def from_request_result(
        cls, request: SimulationRequest, result: SimulationResult
    ) -> WorkloadEpisodeResult:
        return cls(
            case_id=str(request.labels["workload_case_id"]),
            repetition=int(request.labels["workload_repetition"]),
            request_id=result.request_id,
            episode_id=result.episode_id,
            backend_id=result.backend_id,
            engine_version=result.engine_version,
            rules_sha256=result.rules_sha256,
            seed=result.seed,
            status=result.status,
            publishable=result.publishable,
            ticks_completed=result.ticks_completed,
            final_world_sha256=result.final_world_sha256,
            metrics=result.metrics,
            artifact_refs=result.artifact_refs,
            errors=result.errors,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        metrics: dict[str, JsonValue] = {
            key: _metric_json(value) for key, value in self.metrics.items()
        }
        value = to_json_value(
            {
                "case_id": self.case_id,
                "repetition": self.repetition,
                "request_id": self.request_id,
                "episode_id": self.episode_id,
                "backend_id": self.backend_id,
                "engine_version": self.engine_version,
                "rules_sha256": self.rules_sha256,
                "seed": self.seed,
                "status": self.status.value,
                "publishable": self.publishable,
                "ticks_completed": self.ticks_completed,
                "final_world_sha256": self.final_world_sha256,
                "metrics": metrics,
                "artifact_refs": self.artifact_refs,
                "errors": self.errors,
            }
        )
        assert isinstance(value, dict)
        return value


@dataclass(frozen=True, slots=True)
class WorkloadRun:
    manifest_sha256: str
    workload_id: str
    workload_version: str
    backend: WorkloadBackendIdentity
    ruleset: RulesetRef
    episodes: tuple[WorkloadEpisodeResult, ...]
    publishable: bool
    issues: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = REFERENCE_WORKLOAD_RUN_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_sha256", _sha256(self.manifest_sha256, "manifest"))
        object.__setattr__(self, "episodes", tuple(self.episodes))
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.publishable and self.issues:
            raise ValueError("publishable workload runs cannot contain issues")

    @classmethod
    def create(
        cls,
        *,
        manifest: WorkloadManifest,
        backend: WorkloadBackendIdentity,
        requests: Sequence[SimulationRequest],
        results: Sequence[SimulationResult],
    ) -> WorkloadRun:
        issues: list[str] = []
        if len(requests) != manifest.episode_count:
            issues.append("request count does not match manifest episode count")
        if len(results) != len(requests):
            issues.append("result count does not match request count")
        episodes: list[WorkloadEpisodeResult] = []
        for index, (request, result) in enumerate(zip(requests, results, strict=False)):
            identity = (
                result.request_id,
                result.episode_id,
                result.backend_id,
                result.engine_version,
                result.rules_sha256,
                result.seed,
            )
            expected = (
                request.request_id,
                request.episode_id,
                request.config.backend_id,
                request.config.engine_version,
                request.config.ruleset.rules_sha256,
                request.config.seed,
            )
            if identity != expected:
                issues.append(f"episode {index} result identity mismatch")
            if result.status is not SimulationStatus.COMPLETE:
                issues.append(f"episode {index} is {result.status.value}")
            if not result.publishable:
                issues.append(f"episode {index} is not publishable")
            if result.final_world_sha256 is None:
                issues.append(f"episode {index} has no final world digest")
            if not all(math.isfinite(value) for value in result.metrics.values()):
                issues.append(f"episode {index} contains non-finite metrics")
            episodes.append(WorkloadEpisodeResult.from_request_result(request, result))
        episode_ids = tuple(item.episode_id for item in episodes)
        if len(episode_ids) != len(set(episode_ids)):
            issues.append("episode ids are not unique")
        return cls(
            manifest_sha256=manifest.sha256,
            workload_id=manifest.workload_id,
            workload_version=manifest.workload_version,
            backend=backend,
            ruleset=manifest.ruleset,
            episodes=tuple(episodes),
            publishable=not issues,
            issues=tuple(issues),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "schema_version": self.schema_version,
                "manifest_sha256": self.manifest_sha256,
                "workload_id": self.workload_id,
                "workload_version": self.workload_version,
                "backend": self.backend.to_dict(),
                "ruleset": {
                    "name": self.ruleset.name,
                    "version": self.ruleset.version,
                    "rules_sha256": self.ruleset.rules_sha256,
                },
                "episodes": [episode.to_dict() for episode in self.episodes],
                "publishable": self.publishable,
                "issues": self.issues,
            }
        )
        assert isinstance(value, dict)
        return value

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())


class BackendWorkloadRunner:
    """Inject one registered backend into the frozen canonical workload path."""

    def __init__(
        self,
        scenarios: ReferenceScenarioRegistry,
        backend: SimulatorBackend,
    ) -> None:
        self._scenarios = scenarios
        self._backend = backend
        self._registry = BackendRegistry()
        self._registry.register(backend)

    @property
    def backend(self) -> SimulatorBackend:
        return self._backend

    def run(self, manifest: WorkloadManifest, *, batch_size: int = 1) -> WorkloadRun:
        if manifest.ruleset != REFERENCE_RULESET:
            raise ReferenceWorkloadError("manifest rules identity is not the reference ruleset")
        if batch_size < 1 or batch_size > self._backend.descriptor.capabilities.max_batch_size:
            raise ReferenceWorkloadError("batch_size is outside backend capabilities")
        for case in manifest.cases:
            self._scenarios.resolve(case)
        requests = tuple(
            manifest.iter_requests(
                backend_id=self._backend.descriptor.backend_id,
                engine_version=self._backend.descriptor.engine_version,
                protocol_version=REFERENCE_PROTOCOL_VERSION,
            )
        )
        results: list[SimulationResult] = []
        for offset in range(0, len(requests), batch_size):
            results.extend(self._registry.simulate_batch(requests[offset : offset + batch_size]))
        identity = WorkloadBackendIdentity.from_descriptor(
            self._backend.descriptor,
            protocol_version=REFERENCE_PROTOCOL_VERSION,
        )
        run = WorkloadRun.create(
            manifest=manifest,
            backend=identity,
            requests=requests,
            results=results,
        )
        if not run.publishable:
            raise ReferenceWorkloadError("workload backend failed closed: " + "; ".join(run.issues))
        return run


class ReferenceWorkloadRunner(BackendWorkloadRunner):
    """Run the real reference engine and its frozen known-answer gate."""

    def __init__(self, scenarios: ReferenceScenarioRegistry) -> None:
        super().__init__(scenarios, ReferenceEngineBackend(scenarios.scenarios))

    @property
    def backend(self) -> ReferenceEngineBackend:
        backend = super().backend
        assert isinstance(backend, ReferenceEngineBackend)
        return backend

    def run(self, manifest: WorkloadManifest, *, batch_size: int = 1) -> WorkloadRun:
        records = tuple(self._scenarios.resolve(case) for case in manifest.cases)
        run = super().run(manifest, batch_size=batch_size)
        answers = {record.scenario.scenario_id: record.known_answer for record in records}
        for episode in run.episodes:
            answer = answers[episode.case_id]
            mismatches = _episode_known_answer_mismatches(episode, answer)
            if mismatches:
                raise ReferenceWorkloadError(
                    f"known-answer mismatch for {episode.case_id}: {', '.join(mismatches)}"
                )
        return run


def _episode_known_answer_mismatches(
    episode: WorkloadEpisodeResult, answer: KnownAnswer
) -> tuple[str, ...]:
    mismatches: list[str] = []
    if episode.status is not answer.status:
        mismatches.append("status")
    if episode.ticks_completed != answer.ticks_completed:
        mismatches.append("ticks_completed")
    if episode.final_world_sha256 != answer.final_world_sha256:
        mismatches.append("final_world_sha256")
    if dict(episode.metrics) != dict(answer.metrics):
        mismatches.append("metrics")
    replay_identity = ReplayArtifactIdentity.from_artifact_refs(episode.artifact_refs)
    legacy_refs = (f"replay-sha256:{replay_identity.payload_sha256}",)
    if legacy_refs != answer.required_artifact_refs:
        mismatches.append("artifact_refs")
    return tuple(mismatches)


@dataclass(frozen=True, slots=True)
class DifferentialMismatch:
    field: str
    reference: JsonValue
    candidate: JsonValue
    episode_id: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "field": self.field,
                "episode_id": self.episode_id,
                "reference": self.reference,
                "candidate": self.candidate,
            }
        )
        assert isinstance(value, dict)
        return value


@dataclass(frozen=True, slots=True)
class DifferentialReport:
    workload_sha256: str
    reference_run_sha256: str
    candidate_run_sha256: str
    mismatches: tuple[DifferentialMismatch, ...]
    publishable: bool
    schema_version: str = DIFFERENTIAL_REPORT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "workload_sha256", _sha256(self.workload_sha256, "workload"))
        object.__setattr__(
            self,
            "reference_run_sha256",
            _sha256(self.reference_run_sha256, "reference run"),
        )
        object.__setattr__(
            self,
            "candidate_run_sha256",
            _sha256(self.candidate_run_sha256, "candidate run"),
        )
        object.__setattr__(self, "mismatches", tuple(self.mismatches))
        if not isinstance(self.publishable, bool):
            raise ValueError("differential publishable must be a boolean")
        expected_publishable = not self.mismatches
        if self.publishable is not expected_publishable:
            raise ValueError("differential publishability must be fail-closed")

    @property
    def passed(self) -> bool:
        return self.publishable

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "schema_version": self.schema_version,
                "workload_sha256": self.workload_sha256,
                "reference_run_sha256": self.reference_run_sha256,
                "candidate_run_sha256": self.candidate_run_sha256,
                "passed": self.passed,
                "publishable": self.publishable,
                "mismatches": [mismatch.to_dict() for mismatch in self.mismatches],
            }
        )
        assert isinstance(value, dict)
        return value

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())


def compare_workload_runs(reference: WorkloadRun, candidate: WorkloadRun) -> DifferentialReport:
    """Compare semantic episode evidence without treating speed as correctness."""

    mismatches: list[DifferentialMismatch] = []

    def add(
        field: str,
        expected: object,
        actual: object,
        *,
        episode_id: str | None = None,
    ) -> None:
        mismatches.append(
            DifferentialMismatch(
                field=field,
                reference=to_json_value(expected),
                candidate=to_json_value(actual),
                episode_id=episode_id,
            )
        )

    for field_name in ("manifest_sha256", "workload_id", "workload_version", "ruleset"):
        expected = getattr(reference, field_name)
        actual = getattr(candidate, field_name)
        if expected != actual:
            if isinstance(expected, RulesetRef):
                expected = {
                    "name": expected.name,
                    "version": expected.version,
                    "rules_sha256": expected.rules_sha256,
                }
                actual_rules = candidate.ruleset
                actual = {
                    "name": actual_rules.name,
                    "version": actual_rules.version,
                    "rules_sha256": actual_rules.rules_sha256,
                }
            add(field_name, expected, actual)
    if not reference.publishable:
        add("reference_run.publishable", True, False)
    if not candidate.publishable:
        add("candidate_run.publishable", True, False)
    if reference.issues:
        add("reference_run.issues", (), reference.issues)
    if candidate.issues:
        add("candidate_run.issues", (), candidate.issues)

    reference_ids = tuple(episode.episode_id for episode in reference.episodes)
    candidate_ids = tuple(episode.episode_id for episode in candidate.episodes)
    if reference_ids != candidate_ids:
        add("episode_alignment", reference_ids, candidate_ids)
    if len(candidate_ids) != len(set(candidate_ids)):
        add("candidate_episode_duplicates", (), candidate_ids)

    reference_by_id = {episode.episode_id: episode for episode in reference.episodes}
    candidate_by_id = {episode.episode_id: episode for episode in candidate.episodes}
    semantic_fields = (
        "case_id",
        "repetition",
        "status",
        "publishable",
        "rules_sha256",
        "seed",
        "ticks_completed",
        "final_world_sha256",
    )
    for episode_id in reference_ids:
        expected = reference_by_id[episode_id]
        actual = candidate_by_id.get(episode_id)
        if actual is None:
            add("missing_episode", episode_id, None, episode_id=episode_id)
            continue
        try:
            expected_replay_semantic = ReplayArtifactIdentity.from_artifact_refs(
                expected.artifact_refs
            ).semantic_sha256
            actual_replay_semantic = ReplayArtifactIdentity.from_artifact_refs(
                actual.artifact_refs
            ).semantic_sha256
        except ValueError:
            add(
                "artifact_refs",
                expected.artifact_refs,
                actual.artifact_refs,
                episode_id=episode_id,
            )
        else:
            if expected_replay_semantic != actual_replay_semantic:
                add(
                    "replay_semantic_sha256",
                    expected_replay_semantic,
                    actual_replay_semantic,
                    episode_id=episode_id,
                )
        for field_name in semantic_fields:
            expected_value = getattr(expected, field_name)
            actual_value = getattr(actual, field_name)
            if isinstance(expected_value, SimulationStatus):
                expected_value = expected_value.value
                if isinstance(actual_value, SimulationStatus):
                    actual_value = actual_value.value
            if expected_value != actual_value:
                add(field_name, expected_value, actual_value, episode_id=episode_id)
        expected_metrics = dict(expected.metrics)
        actual_metrics = dict(actual.metrics)
        if not all(math.isfinite(value) for value in actual_metrics.values()):
            add(
                "metrics.non_finite",
                False,
                True,
                episode_id=episode_id,
            )
        if expected_metrics != actual_metrics:
            add(
                "metrics",
                {key: _metric_json(value) for key, value in expected_metrics.items()},
                {key: _metric_json(value) for key, value in actual_metrics.items()},
                episode_id=episode_id,
            )
    for episode_id in candidate_ids:
        if episode_id not in reference_by_id:
            add("unexpected_episode", None, episode_id, episode_id=episode_id)

    return DifferentialReport(
        workload_sha256=reference.manifest_sha256,
        reference_run_sha256=reference.sha256,
        candidate_run_sha256=candidate.sha256,
        mismatches=tuple(mismatches),
        publishable=not mismatches,
    )


def run_canonical_reference_workload(*, batch_size: int = 1) -> WorkloadRun:
    manifest = canonical_reference_workload_manifest()
    runner = ReferenceWorkloadRunner(canonical_reference_scenario_registry())
    return runner.run(manifest, batch_size=batch_size)
