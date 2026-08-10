"""Explicitly incomplete reference backend used to validate platform wiring."""

from __future__ import annotations

from arena_hero_sim.backend import BackendDescriptor
from arena_hero_sim.contracts import (
    BackendCapabilities,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
)


class ReferenceBackendPlaceholder:
    """Contract placeholder; it never claims to execute Arena Hero rules."""

    _descriptor = BackendDescriptor(
        backend_id="reference-placeholder",
        engine_version="0.1.0-placeholder",
        capabilities=BackendCapabilities(
            protocol_versions=("arena.sim.v1",),
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
