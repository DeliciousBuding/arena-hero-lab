"""Reference backends: the legacy placeholder and bounded real M4 engine."""

from __future__ import annotations

from types import MappingProxyType

from arena_hero_sim.backend import BackendDescriptor
from arena_hero_sim.contracts import (
    BackendCapabilities,
    RulesetRef,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
)
from arena_hero_sim.reference_contracts import (
    REFERENCE_MOVEMENT_RULE_IDENTITY,
    REFERENCE_RULES,
    ReferenceEpisodeStatus,
    ReferenceScenario,
)
from arena_hero_sim.reference_engine import (
    ReferenceEpisodeResult,
    UnsupportedReferenceSliceError,
    run_reference_episode,
)

REFERENCE_BACKEND_ID = "reference-engine"
REFERENCE_ENGINE_VERSION = "0.1.0-m4"
REFERENCE_PROTOCOL_VERSION = "arena.sim.v1"
REFERENCE_RULESET = RulesetRef(
    "arena-hero",
    REFERENCE_RULES.rules_version,
    REFERENCE_RULES.sha256,
)
REFERENCE_FEATURES = frozenset(
    {
        "deterministic-rng-stream",
        "full-world-hash",
        "reference-harvest-deposit-v1",
        "reference-legal-actions-v1",
        REFERENCE_MOVEMENT_RULE_IDENTITY,
        "reference-visibility-v1",
        "versioned-replay-v1",
    }
)


class ReferenceBackendPlaceholder:
    """Contract placeholder; it never claims to execute Arena Hero rules."""

    _descriptor = BackendDescriptor(
        backend_id="reference-placeholder",
        engine_version="0.1.0-placeholder",
        capabilities=BackendCapabilities(
            protocol_versions=(REFERENCE_PROTOCOL_VERSION,),
            features=frozenset({"contract-validation"}),
            execution_modes=frozenset({"in-process"}),
            max_batch_size=1024,
            supports_batch=True,
            supports_incremental_world_hash=False,
            supports_zero_copy=False,
            interchange_formats=frozenset({"canonical-json"}),
        ),
    )

    @property
    def descriptor(self) -> BackendDescriptor:
        return self._descriptor

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        return self._unsupported(request)

    def simulate_batch(
        self, requests: tuple[SimulationRequest, ...]
    ) -> tuple[SimulationResult, ...]:
        return tuple(self._unsupported(request) for request in requests)

    def _unsupported(self, request: SimulationRequest) -> SimulationResult:
        return SimulationResult(
            request_id=request.request_id,
            episode_id=request.episode_id,
            backend_id=self.descriptor.backend_id,
            engine_version=self.descriptor.engine_version,
            rules_sha256=request.config.ruleset.rules_sha256,
            seed=request.config.seed,
            status=SimulationStatus.UNSUPPORTED,
            publishable=False,
            ticks_completed=0,
            errors=(
                "reference backend is a contract placeholder and does not implement game rules",
            ),
        )


class ReferenceEngineBackend:
    """Real deterministic backend for one explicitly versioned rules slice."""

    _descriptor = BackendDescriptor(
        backend_id=REFERENCE_BACKEND_ID,
        engine_version=REFERENCE_ENGINE_VERSION,
        capabilities=BackendCapabilities(
            protocol_versions=(REFERENCE_PROTOCOL_VERSION,),
            features=REFERENCE_FEATURES,
            execution_modes=frozenset({"in-process"}),
            max_batch_size=1024,
            supports_batch=True,
            supports_incremental_world_hash=False,
            supports_zero_copy=False,
            interchange_formats=frozenset({"canonical-json"}),
        ),
    )

    def __init__(self, scenarios: tuple[ReferenceScenario, ...]) -> None:
        by_digest: dict[str, ReferenceScenario] = {}
        ids: set[str] = set()
        for scenario in scenarios:
            if scenario.sha256 in by_digest:
                raise ValueError(f"duplicate reference scenario digest: {scenario.sha256}")
            if scenario.scenario_id in ids:
                raise ValueError(f"duplicate reference scenario id: {scenario.scenario_id}")
            ids.add(scenario.scenario_id)
            by_digest[scenario.sha256] = scenario
        self._scenarios = MappingProxyType(by_digest)

    @property
    def descriptor(self) -> BackendDescriptor:
        return self._descriptor

    def execute(self, request: SimulationRequest) -> ReferenceEpisodeResult:
        problem = self._support_problem(request)
        if problem is not None:
            raise UnsupportedReferenceSliceError(problem)
        assert request.input_artifact_sha256 is not None
        scenario = self._scenarios[request.input_artifact_sha256]
        return run_reference_episode(
            scenario,
            request_id=request.request_id,
            episode_id=request.episode_id,
            max_ticks=request.config.max_ticks,
        )

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        try:
            episode = self.execute(request)
        except UnsupportedReferenceSliceError as error:
            return self._unsupported(request, str(error))
        status = (
            SimulationStatus.COMPLETE
            if episode.status is ReferenceEpisodeStatus.COMPLETE
            else SimulationStatus.PARTIAL
        )
        return SimulationResult(
            request_id=request.request_id,
            episode_id=request.episode_id,
            backend_id=self.descriptor.backend_id,
            engine_version=self.descriptor.engine_version,
            rules_sha256=request.config.ruleset.rules_sha256,
            seed=request.config.seed,
            status=status,
            publishable=status is SimulationStatus.COMPLETE,
            ticks_completed=episode.ticks_completed,
            final_world_sha256=episode.final_world.sha256,
            metrics=episode.metrics,
            artifact_refs=(f"replay-sha256:{episode.replay.payload_sha256}",),
            errors=(
                ()
                if status is SimulationStatus.COMPLETE
                else ("tick budget ended before the registered scenario completed",)
            ),
        )

    def simulate_batch(
        self, requests: tuple[SimulationRequest, ...]
    ) -> tuple[SimulationResult, ...]:
        return tuple(self.simulate(request) for request in requests)

    def _support_problem(self, request: SimulationRequest) -> str | None:
        if not request.config.deterministic:
            return "reference engine requires deterministic=true"
        if request.config.ruleset != REFERENCE_RULESET:
            return "requested rules identity is outside the implemented reference slice"
        if request.config.parameters:
            return "reference engine does not accept unversioned configuration parameters"
        if request.input_artifact_sha256 is None:
            return "registered scenario digest is required as input_artifact_sha256"
        scenario = self._scenarios.get(request.input_artifact_sha256)
        if scenario is None:
            return "input artifact is not a registered reference scenario"
        if request.initial_state_sha256 != scenario.initial_world.sha256:
            return "initial_state_sha256 does not match the registered scenario"
        if request.config.seed != scenario.initial_world.seed:
            return "request seed does not match the registered scenario"
        if request.contestant_ids != scenario.contestant_ids:
            return "contestants do not match the registered scenario"
        return None

    def _unsupported(self, request: SimulationRequest, problem: str) -> SimulationResult:
        return SimulationResult(
            request_id=request.request_id,
            episode_id=request.episode_id,
            backend_id=self.descriptor.backend_id,
            engine_version=self.descriptor.engine_version,
            rules_sha256=request.config.ruleset.rules_sha256,
            seed=request.config.seed,
            status=SimulationStatus.UNSUPPORTED,
            publishable=False,
            ticks_completed=0,
            errors=(problem,),
        )
