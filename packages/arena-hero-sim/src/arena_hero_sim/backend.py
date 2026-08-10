"""Replaceable simulator backend protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from arena_hero_sim.contracts import BackendCapabilities, SimulationRequest, SimulationResult


@dataclass(frozen=True, slots=True)
class BackendDescriptor:
    backend_id: str
    engine_version: str
    capabilities: BackendCapabilities


@runtime_checkable
class SimulatorBackend(Protocol):
    """Backend seam for reference, optimized, native, or remote engines."""

    @property
    def descriptor(self) -> BackendDescriptor: ...

    def simulate(self, request: SimulationRequest) -> SimulationResult: ...

    def simulate_batch(
        self, requests: tuple[SimulationRequest, ...]
    ) -> tuple[SimulationResult, ...]: ...
