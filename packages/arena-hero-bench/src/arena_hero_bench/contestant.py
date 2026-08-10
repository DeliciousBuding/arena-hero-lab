"""Versioned contestant manifests and registry validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType

from arena_hero_sim.serialization import content_sha256, to_json_value

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class ContestantRegistryError(ValueError):
    pass


class DuplicateContestantError(ContestantRegistryError):
    pass


class ArtifactDigestMismatchError(ContestantRegistryError):
    pass


class UnknownContestantError(ContestantRegistryError):
    pass


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


def _digest(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ResourceRequirements:
    cpu_cores: float
    memory_mb: int
    process_limit: int
    timeout_seconds: float
    gpu: str | None = None

    def __post_init__(self) -> None:
        if self.cpu_cores <= 0 or self.memory_mb < 1 or self.process_limit < 1:
            raise ValueError("CPU, memory, and process requirements must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class IsolationRequirements:
    subprocess_required: bool = True
    network_policy: str = "deny"
    filesystem_policy: str = "ephemeral"
    environment_allowlist: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.network_policy not in {"deny", "loopback", "declared"}:
            raise ValueError("unsupported network_policy")
        if self.filesystem_policy not in {"ephemeral", "read-only-inputs", "declared"}:
            raise ValueError("unsupported filesystem_policy")
        object.__setattr__(
            self,
            "environment_allowlist",
            tuple(dict.fromkeys(self.environment_allowlist)),
        )


@dataclass(frozen=True, slots=True)
class ContestantManifest:
    """Portable contestant identity with no hard-coded model catalog."""

    schema_version: str
    contestant_id: str
    version: str
    entry_point: str
    language: str
    runtime: str
    protocol_version: str
    artifact_sha256: str
    config_schema: Mapping[str, object]
    resources: ResourceRequirements
    capabilities: frozenset[str] = field(default_factory=frozenset)
    isolation: IsolationRequirements = field(default_factory=IsolationRequirements)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _text(self.schema_version, "schema_version"))
        object.__setattr__(self, "contestant_id", _identifier(self.contestant_id, "contestant_id"))
        for field_name in ("version", "entry_point", "language", "runtime", "protocol_version"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        object.__setattr__(
            self,
            "artifact_sha256",
            _digest(self.artifact_sha256, "artifact_sha256"),
        )
        schema = to_json_value(self.config_schema)
        if not isinstance(schema, dict):
            raise ValueError("config_schema must be a JSON object")
        object.__setattr__(self, "config_schema", MappingProxyType(schema))
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))

    @property
    def key(self) -> tuple[str, str]:
        return self.contestant_id, self.version

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "contestant_id": self.contestant_id,
            "version": self.version,
            "entry_point": self.entry_point,
            "language": self.language,
            "runtime": self.runtime,
            "protocol_version": self.protocol_version,
            "artifact_sha256": self.artifact_sha256,
            "config_schema": dict(self.config_schema),
            "resources": asdict(self.resources),
            "capabilities": sorted(self.capabilities),
            "isolation": asdict(self.isolation),
        }

    def manifest_sha256(self) -> str:
        return content_sha256(self.to_dict())


class ContestantRegistry:
    def __init__(self) -> None:
        self._manifests: dict[tuple[str, str], ContestantManifest] = {}

    def register(self, manifest: ContestantManifest, *, artifact: bytes | None = None) -> None:
        if manifest.key in self._manifests:
            raise DuplicateContestantError(
                f"contestant already registered: {manifest.contestant_id}@{manifest.version}"
            )
        if artifact is not None and content_sha256(artifact) != manifest.artifact_sha256:
            raise ArtifactDigestMismatchError("contestant artifact SHA-256 does not match manifest")
        self._manifests[manifest.key] = manifest

    def get(self, contestant_id: str, version: str) -> ContestantManifest:
        try:
            return self._manifests[(contestant_id, version)]
        except KeyError as error:
            raise UnknownContestantError(
                f"unknown contestant: {contestant_id}@{version}"
            ) from error

    def versions(self, contestant_id: str) -> tuple[str, ...]:
        return tuple(sorted(version for item, version in self._manifests if item == contestant_id))
