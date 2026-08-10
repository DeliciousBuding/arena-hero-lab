"""Public, content-addressed environment and SBOM provenance contracts.

These artifacts intentionally capture a bounded reproducibility surface. They do not read
process environment variables, hostnames, user names, executable paths, or credentials.
"""

from __future__ import annotations

import platform
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from arena_hero_research.validation import (
    freeze_public_metadata,
    require_identifier,
    require_json_mapping,
    require_sequence,
    require_sha256,
    require_text,
)
from arena_hero_sim.serialization import JsonValue, content_sha256


class ProvenanceError(ValueError):
    pass


@dataclass(frozen=True, slots=True, order=True)
class SoftwareComponent:
    """One explicitly disclosed software component in the minimal public SBOM."""

    name: str
    version: str
    component_type: str = "library"
    purl: str | None = None
    license_ids: tuple[str, ...] = ()
    sha256: str | None = None
    properties: Mapping[str, JsonValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_text(self.name, "component name"))
        object.__setattr__(self, "version", require_text(self.version, "component version"))
        object.__setattr__(
            self,
            "component_type",
            require_identifier(self.component_type, "component_type"),
        )
        if self.purl is not None:
            object.__setattr__(self, "purl", require_text(self.purl, "purl"))
        licenses = tuple(require_text(item, "license id") for item in self.license_ids)
        if len(licenses) != len(set(licenses)):
            raise ProvenanceError("component license ids must be unique")
        object.__setattr__(self, "license_ids", tuple(sorted(licenses)))
        if self.sha256 is not None:
            object.__setattr__(self, "sha256", require_sha256(self.sha256, "component sha256"))
        object.__setattr__(
            self,
            "properties",
            freeze_public_metadata(self.properties, "component properties"),
        )

    def identity(self) -> tuple[str, str, str, str]:
        return (self.name.casefold(), self.version, self.component_type, self.purl or "")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "version": self.version,
            "component_type": self.component_type,
            "purl": self.purl,
            "license_ids": list(self.license_ids),
            "sha256": self.sha256,
            "properties": dict(self.properties),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SoftwareComponent:
        licenses = require_sequence(value.get("license_ids", ()), "license_ids")
        purl = value.get("purl")
        sha256 = value.get("sha256")
        return cls(
            name=str(value["name"]),
            version=str(value["version"]),
            component_type=str(value.get("component_type", "library")),
            purl=None if purl is None else str(purl),
            license_ids=tuple(str(item) for item in licenses),
            sha256=None if sha256 is None else str(sha256),
            properties=require_json_mapping(value.get("properties", {}), "component properties"),
        )


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    """A bounded public runtime snapshot without ambient environment or host identity."""

    schema_version: str
    python_implementation: str
    python_version: str
    operating_system: str
    operating_system_release: str
    machine: str
    executor: str
    metadata: Mapping[str, JsonValue]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.environment-snapshot.v1":
            raise ProvenanceError("unsupported environment snapshot schema")
        for name in (
            "python_implementation",
            "python_version",
            "operating_system",
            "operating_system_release",
            "machine",
        ):
            object.__setattr__(self, name, require_text(getattr(self, name), name))
        object.__setattr__(self, "executor", require_identifier(self.executor, "executor"))
        object.__setattr__(
            self, "metadata", freeze_public_metadata(self.metadata, "environment metadata")
        )
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "python_implementation": self.python_implementation,
            "python_version": self.python_version,
            "operating_system": self.operating_system,
            "operating_system_release": self.operating_system_release,
            "machine": self.machine,
            "executor": self.executor,
            "metadata": dict(self.metadata),
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def create(
        cls,
        *,
        python_implementation: str,
        python_version: str,
        operating_system: str,
        operating_system_release: str,
        machine: str,
        executor: str,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> EnvironmentSnapshot:
        provisional = cls(
            schema_version="arena.research.environment-snapshot.v1",
            python_implementation=python_implementation,
            python_version=python_version,
            operating_system=operating_system,
            operating_system_release=operating_system_release,
            machine=machine,
            executor=executor,
            metadata=metadata or {},
            canonical_sha256="0" * 64,
        )
        return cls(
            schema_version=provisional.schema_version,
            python_implementation=provisional.python_implementation,
            python_version=provisional.python_version,
            operating_system=provisional.operating_system,
            operating_system_release=provisional.operating_system_release,
            machine=provisional.machine,
            executor=provisional.executor,
            metadata=provisional.metadata,
            canonical_sha256=content_sha256(provisional.payload()),
        )

    @classmethod
    def capture_public(
        cls,
        *,
        executor: str,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> EnvironmentSnapshot:
        """Capture only portable runtime facts; never inspect environment variables."""

        return cls.create(
            python_implementation=platform.python_implementation() or "unknown",
            python_version=platform.python_version() or "unknown",
            operating_system=platform.system() or "unknown",
            operating_system_release=platform.release() or "unknown",
            machine=platform.machine() or "unknown",
            executor=executor,
            metadata=metadata,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EnvironmentSnapshot:
        return cls(
            schema_version=str(value["schema_version"]),
            python_implementation=str(value["python_implementation"]),
            python_version=str(value["python_version"]),
            operating_system=str(value["operating_system"]),
            operating_system_release=str(value["operating_system_release"]),
            machine=str(value["machine"]),
            executor=str(value["executor"]),
            metadata=require_json_mapping(value["metadata"], "environment metadata"),
            canonical_sha256=str(value["canonical_sha256"]),
        )


@dataclass(frozen=True, slots=True)
class SoftwareBillOfMaterials:
    """A minimal, public, deterministic SBOM; not a supply-chain attestation."""

    schema_version: str
    generator: str
    components: tuple[SoftwareComponent, ...]
    metadata: Mapping[str, JsonValue]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.sbom.v1":
            raise ProvenanceError("unsupported SBOM schema")
        object.__setattr__(self, "generator", require_identifier(self.generator, "generator"))
        components = tuple(sorted(self.components, key=SoftwareComponent.identity))
        identities = tuple(item.identity() for item in components)
        if not components:
            raise ProvenanceError("SBOM must contain at least one disclosed component")
        if len(identities) != len(set(identities)):
            raise ProvenanceError("SBOM component identities must be unique")
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "metadata", freeze_public_metadata(self.metadata, "SBOM metadata"))
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "generator": self.generator,
            "components": [item.to_dict() for item in self.components],
            "metadata": dict(self.metadata),
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def create(
        cls,
        *,
        generator: str,
        components: Sequence[SoftwareComponent],
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> SoftwareBillOfMaterials:
        provisional = cls(
            schema_version="arena.research.sbom.v1",
            generator=generator,
            components=tuple(components),
            metadata=metadata or {},
            canonical_sha256="0" * 64,
        )
        return cls(
            schema_version=provisional.schema_version,
            generator=provisional.generator,
            components=provisional.components,
            metadata=provisional.metadata,
            canonical_sha256=content_sha256(provisional.payload()),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SoftwareBillOfMaterials:
        components = require_sequence(value["components"], "components")
        parsed: list[SoftwareComponent] = []
        for component in components:
            if not isinstance(component, Mapping):
                raise TypeError("SBOM component must be a mapping")
            parsed.append(SoftwareComponent.from_dict(component))
        return cls(
            schema_version=str(value["schema_version"]),
            generator=str(value["generator"]),
            components=tuple(parsed),
            metadata=require_json_mapping(value["metadata"], "SBOM metadata"),
            canonical_sha256=str(value["canonical_sha256"]),
        )


@dataclass(frozen=True, slots=True)
class EnvironmentProvenance:
    """Content-addressed binding between an environment snapshot and its public SBOM."""

    schema_version: str
    environment_sha256: str
    sbom_sha256: str
    metadata: Mapping[str, JsonValue]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.environment-provenance.v1":
            raise ProvenanceError("unsupported environment provenance schema")
        object.__setattr__(
            self,
            "environment_sha256",
            require_sha256(self.environment_sha256, "environment_sha256"),
        )
        object.__setattr__(self, "sbom_sha256", require_sha256(self.sbom_sha256, "sbom_sha256"))
        object.__setattr__(
            self, "metadata", freeze_public_metadata(self.metadata, "provenance metadata")
        )
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "environment_sha256": self.environment_sha256,
            "sbom_sha256": self.sbom_sha256,
            "metadata": dict(self.metadata),
        }

    def verify(
        self,
        environment: EnvironmentSnapshot | None = None,
        sbom: SoftwareBillOfMaterials | None = None,
    ) -> bool:
        if content_sha256(self.payload()) != self.canonical_sha256:
            return False
        environment_matches = environment is None or (
            environment.verify() and environment.canonical_sha256 == self.environment_sha256
        )
        sbom_matches = sbom is None or (sbom.verify() and sbom.canonical_sha256 == self.sbom_sha256)
        return environment_matches and sbom_matches

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def create(
        cls,
        *,
        environment: EnvironmentSnapshot,
        sbom: SoftwareBillOfMaterials,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> EnvironmentProvenance:
        if not environment.verify() or not sbom.verify():
            raise ProvenanceError("environment and SBOM artifacts must verify before binding")
        provisional = cls(
            schema_version="arena.research.environment-provenance.v1",
            environment_sha256=environment.canonical_sha256,
            sbom_sha256=sbom.canonical_sha256,
            metadata=metadata or {},
            canonical_sha256="0" * 64,
        )
        return cls(
            schema_version=provisional.schema_version,
            environment_sha256=provisional.environment_sha256,
            sbom_sha256=provisional.sbom_sha256,
            metadata=provisional.metadata,
            canonical_sha256=content_sha256(provisional.payload()),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EnvironmentProvenance:
        return cls(
            schema_version=str(value["schema_version"]),
            environment_sha256=str(value["environment_sha256"]),
            sbom_sha256=str(value["sbom_sha256"]),
            metadata=require_json_mapping(value["metadata"], "provenance metadata"),
            canonical_sha256=str(value["canonical_sha256"]),
        )
