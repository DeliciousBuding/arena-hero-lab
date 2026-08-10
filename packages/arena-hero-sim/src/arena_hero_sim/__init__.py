"""Deterministic Arena Hero simulator platform contracts."""

from arena_hero_sim.backend import BackendDescriptor, SimulatorBackend
from arena_hero_sim.contracts import (
    BackendCapabilities,
    RulesetRef,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorConfig,
)
from arena_hero_sim.reference import ReferenceBackendPlaceholder
from arena_hero_sim.registry import BackendRegistry
from arena_hero_sim.serialization import canonical_json_bytes, content_sha256

__all__ = [
    "BackendCapabilities",
    "BackendDescriptor",
    "BackendRegistry",
    "ReferenceBackendPlaceholder",
    "RulesetRef",
    "SimulationRequest",
    "SimulationResult",
    "SimulationStatus",
    "SimulatorBackend",
    "SimulatorConfig",
    "canonical_json_bytes",
    "content_sha256",
]
__version__ = "0.2.0"
