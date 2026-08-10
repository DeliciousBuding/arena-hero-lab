"""Content-addressed manifests for benchmark runs and generated artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStatus(StrEnum):
    """Lifecycle status carried by run and artifact manifests."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


def _validated_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _validated_digest(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _validated_provenance(value: Mapping[str, str]) -> Mapping[str, str]:
    normalized = {
        _validated_text(str(key), "provenance key"): _validated_text(str(item), "provenance value")
        for key, item in value.items()
    }
    if not normalized:
        raise ValueError("provenance must not be empty")
    return MappingProxyType(dict(sorted(normalized.items())))


def _validate_publication(status: ArtifactStatus, publishable: bool) -> None:
    if status is not ArtifactStatus.COMPLETE and publishable:
        raise ValueError(f"{status.value} manifests must set publishable=false")


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """Identity and publication state for one immutable artifact."""

    schema_version: str
    generator_version: str
    provenance: Mapping[str, str]
    source_build_sha256: str
    content_sha256: str
    status: ArtifactStatus
    publishable: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema_version", _validated_text(self.schema_version, "schema_version")
        )
        object.__setattr__(
            self,
            "generator_version",
            _validated_text(self.generator_version, "generator_version"),
        )
        object.__setattr__(self, "provenance", _validated_provenance(self.provenance))
        object.__setattr__(
            self,
            "source_build_sha256",
            _validated_digest(self.source_build_sha256, "source_build_sha256"),
        )
        object.__setattr__(
            self,
            "content_sha256",
            _validated_digest(self.content_sha256, "content_sha256"),
        )
        _validate_publication(self.status, self.publishable)

    @classmethod
    def for_content(
        cls,
        *,
        content: object | bytes,
        schema_version: str,
        generator_version: str,
        provenance: Mapping[str, str],
        source_build_sha256: str,
        status: ArtifactStatus = ArtifactStatus.COMPLETE,
        publishable: bool = True,
    ) -> Self:
        """Construct a manifest whose digest is derived from immutable content."""

        return cls(
            schema_version=schema_version,
            generator_version=generator_version,
            provenance=provenance,
            source_build_sha256=source_build_sha256,
            content_sha256=content_sha256(content),
            status=status,
            publishable=publishable,
        )

    def verify_content(self, content: object | bytes) -> bool:
        """Return whether content matches the recorded SHA-256 digest."""

        return content_sha256(content) == self.content_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
            "provenance": dict(self.provenance),
            "source_build_sha256": self.source_build_sha256,
            "content_sha256": self.content_sha256,
            "status": self.status.value,
            "publishable": self.publishable,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        provenance = value.get("provenance")
        if not isinstance(provenance, Mapping):
            raise ValueError("provenance must be an object")
        return cls(
            schema_version=str(value.get("schema_version", "")),
            generator_version=str(value.get("generator_version", "")),
            provenance={str(key): str(item) for key, item in provenance.items()},
            source_build_sha256=str(value.get("source_build_sha256", "")),
            content_sha256=str(value.get("content_sha256", "")),
            status=ArtifactStatus(str(value.get("status", ""))),
            publishable=value.get("publishable") is True,
        )


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Content-addressed run manifest with immutable artifact references."""

    schema_version: str
    generator_version: str
    provenance: Mapping[str, str]
    source_build_sha256: str
    content_sha256: str
    status: ArtifactStatus
    publishable: bool
    artifacts: tuple[ArtifactManifest, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema_version", _validated_text(self.schema_version, "schema_version")
        )
        object.__setattr__(
            self,
            "generator_version",
            _validated_text(self.generator_version, "generator_version"),
        )
        object.__setattr__(self, "provenance", _validated_provenance(self.provenance))
        object.__setattr__(
            self,
            "source_build_sha256",
            _validated_digest(self.source_build_sha256, "source_build_sha256"),
        )
        object.__setattr__(
            self,
            "content_sha256",
            _validated_digest(self.content_sha256, "content_sha256"),
        )
        _validate_publication(self.status, self.publishable)
        if self.publishable and any(not artifact.publishable for artifact in self.artifacts):
            raise ValueError("a publishable run cannot reference an unpublishable artifact")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
            "provenance": dict(self.provenance),
            "source_build_sha256": self.source_build_sha256,
            "content_sha256": self.content_sha256,
            "status": self.status.value,
            "publishable": self.publishable,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    def canonical_digest(self) -> str:
        """Digest the complete manifest for registry and transport use."""

        return content_sha256(to_json_value(self.to_dict()))
