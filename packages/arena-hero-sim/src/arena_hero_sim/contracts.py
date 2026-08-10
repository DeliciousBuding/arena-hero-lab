"""Immutable contracts for simulator backends and deterministic episodes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _identifier(value: str, field_name: str) -> str:
    normalized = _text(value, field_name)
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase portable identifier")
    return normalized


def _sha256(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _frozen_str_map(value: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(sorted((str(key), str(item)) for key, item in value.items())))


def _frozen_float_map(value: Mapping[str, float]) -> Mapping[str, float]:
    return MappingProxyType(dict(sorted((str(key), float(item)) for key, item in value.items())))


class SimulationStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class RulesetRef:
    """Versioned rules identity independent of a backend implementation."""

    name: str
    version: str
    rules_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "ruleset name"))
        object.__setattr__(self, "version", _text(self.version, "ruleset version"))
        object.__setattr__(self, "rules_sha256", _sha256(self.rules_sha256, "rules_sha256"))


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """Negotiable behavior and performance features exposed by a backend."""

    protocol_versions: tuple[str, ...]
    features: frozenset[str] = field(default_factory=frozenset)
    execution_modes: frozenset[str] = field(default_factory=lambda: frozenset({"in-process"}))
    max_batch_size: int = 1
    supports_batch: bool = False
    supports_incremental_world_hash: bool = False
    supports_zero_copy: bool = False
    interchange_formats: frozenset[str] = field(
        default_factory=lambda: frozenset({"canonical-json"})
    )

    def __post_init__(self) -> None:
        protocols = tuple(
            dict.fromkeys(_text(item, "protocol version") for item in self.protocol_versions)
        )
        if not protocols:
            raise ValueError("at least one protocol version is required")
        if self.max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")
        if not self.supports_batch and self.max_batch_size != 1:
            raise ValueError("non-batch backends must use max_batch_size=1")
        object.__setattr__(self, "protocol_versions", protocols)
        object.__setattr__(self, "features", frozenset(self.features))
        object.__setattr__(self, "execution_modes", frozenset(self.execution_modes))
        object.__setattr__(self, "interchange_formats", frozenset(self.interchange_formats))

    def missing_features(self, requested: frozenset[str]) -> frozenset[str]:
        return requested - self.features


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    """Frozen execution configuration included in every request digest."""

    backend_id: str
    engine_version: str
    ruleset: RulesetRef
    seed: int
    max_ticks: int
    protocol_version: str
    deterministic: bool = True
    requested_features: frozenset[str] = field(default_factory=frozenset)
    parameters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend_id", _identifier(self.backend_id, "backend_id"))
        object.__setattr__(self, "engine_version", _text(self.engine_version, "engine_version"))
        object.__setattr__(
            self, "protocol_version", _text(self.protocol_version, "protocol_version")
        )
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if self.max_ticks < 1:
            raise ValueError("max_ticks must be positive")
        object.__setattr__(self, "requested_features", frozenset(self.requested_features))
        object.__setattr__(self, "parameters", _frozen_str_map(self.parameters))


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """One deterministic episode request with immutable identity and inputs."""

    request_id: str
    episode_id: str
    config: SimulatorConfig
    initial_state_sha256: str
    contestant_ids: tuple[str, ...]
    input_artifact_sha256: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request_id"))
        object.__setattr__(self, "episode_id", _identifier(self.episode_id, "episode_id"))
        object.__setattr__(
            self,
            "initial_state_sha256",
            _sha256(self.initial_state_sha256, "initial_state_sha256"),
        )
        if self.input_artifact_sha256 is not None:
            object.__setattr__(
                self,
                "input_artifact_sha256",
                _sha256(self.input_artifact_sha256, "input_artifact_sha256"),
            )
        contestants = tuple(_identifier(item, "contestant_id") for item in self.contestant_ids)
        if not contestants:
            raise ValueError("at least one contestant is required")
        if len(contestants) != len(set(contestants)):
            raise ValueError("contestant_ids must be unique")
        object.__setattr__(self, "contestant_ids", contestants)
        object.__setattr__(self, "labels", _frozen_str_map(self.labels))


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Backend result whose publication state is explicit and fail-closed."""

    request_id: str
    episode_id: str
    backend_id: str
    engine_version: str
    rules_sha256: str
    seed: int
    status: SimulationStatus
    publishable: bool
    ticks_completed: int
    final_world_sha256: str | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request_id"))
        object.__setattr__(self, "episode_id", _identifier(self.episode_id, "episode_id"))
        object.__setattr__(self, "backend_id", _identifier(self.backend_id, "backend_id"))
        object.__setattr__(self, "engine_version", _text(self.engine_version, "engine_version"))
        object.__setattr__(self, "rules_sha256", _sha256(self.rules_sha256, "rules_sha256"))
        if self.seed < 0 or self.ticks_completed < 0:
            raise ValueError("seed and ticks_completed must be non-negative")
        if self.final_world_sha256 is not None:
            object.__setattr__(
                self,
                "final_world_sha256",
                _sha256(self.final_world_sha256, "final_world_sha256"),
            )
        if self.status is not SimulationStatus.COMPLETE and self.publishable:
            raise ValueError(f"{self.status.value} results must set publishable=false")
        if self.status is SimulationStatus.COMPLETE and self.final_world_sha256 is None:
            raise ValueError("complete results require final_world_sha256")
        object.__setattr__(self, "metrics", _frozen_float_map(self.metrics))
        object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))
        object.__setattr__(self, "errors", tuple(self.errors))
