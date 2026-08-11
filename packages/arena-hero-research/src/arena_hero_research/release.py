"""Content-addressed release bundle and reproducibility evidence for Lab packages.

The release builder binds built distributions (wheels and sdists) to SHA-256
digests, records a per-package minimal SBOM (``arena.research.sbom.v1``), and
verifies reproducible builds by byte-comparing two builds from the same source
state. This is reproducibility evidence for the arena-hero-sim,
arena-hero-bench, and arena-hero-research releases; it is not a supply-chain
attestation and it never publishes or deploys anything.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from arena_hero_research.provenance import (
    EnvironmentSnapshot,
    SoftwareBillOfMaterials,
    SoftwareComponent,
)
from arena_hero_sim.serialization import JsonValue, content_sha256

RELEASE_MANIFEST_SCHEMA: Final = "arena.lab.release-manifest.v1"
RELEASE_GENERATOR: Final = "arena-hero-lab"
GENERATOR_VERSION: Final = "0.1.0"
SBOM_SCOPE: Final = "declared-dependencies-and-python-runtime"
DEFAULT_EXECUTOR: Final = "lab-release"


class ReleaseArtifactError(RuntimeError):
    """Raised when a release bundle cannot be assembled or verified."""


@dataclass(frozen=True, slots=True)
class PackageMetadata:
    """Workspace package identity and declared runtime dependencies."""

    name: str
    version: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """One built distribution file bound to its SHA-256 digest."""

    file: str
    kind: str
    sha256: str
    path: Path

    def to_dict(self) -> dict[str, JsonValue]:
        return {"file": self.file, "kind": self.kind, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class ArtifactDifference:
    """One explicitly recorded byte difference between two builds."""

    file: str
    kind: str
    primary_sha256: str
    secondary_sha256: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "file": self.file,
            "kind": self.kind,
            "primary_sha256": self.primary_sha256,
            "secondary_sha256": self.secondary_sha256,
        }


@dataclass(frozen=True, slots=True)
class ReproducibilityEvidence:
    """Byte-comparison outcome between two builds from the same source state."""

    reproducible: bool
    builds_compared: int
    differences: tuple[ArtifactDifference, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "reproducible": self.reproducible,
            "builds_compared": self.builds_compared,
            "differences": [difference.to_dict() for difference in self.differences],
        }


@dataclass(frozen=True, slots=True)
class SourceState:
    """Deterministic anchors identifying the source state used for a build."""

    commit: str
    dirty: bool
    source_build_sha256: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "commit": self.commit,
            "dirty": self.dirty,
            "source_build_sha256": self.source_build_sha256,
        }


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest of a file's exact bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def workspace_members(root: Path) -> list[PackageMetadata]:
    """Read declared workspace package metadata from ``packages/*/pyproject.toml``.

    Members are returned sorted by package name. Dependency lists are read as
    declared in ``[project].dependencies``; versions are resolved from the
    workspace member itself, never from the network.
    """
    members: list[PackageMetadata] = []
    for path in sorted((root / "packages").glob("*/pyproject.toml")):
        relative = path.relative_to(root).as_posix()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        project = data.get("project")
        if not isinstance(project, Mapping):
            raise ReleaseArtifactError(f"missing [project] table in {relative}")
        name = project.get("name")
        version = project.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ReleaseArtifactError(f"invalid [project] name/version in {relative}")
        dependencies = project.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) for item in dependencies
        ):
            raise ReleaseArtifactError(f"invalid [project].dependencies in {relative}")
        members.append(
            PackageMetadata(
                name=name,
                version=version,
                dependencies=tuple(dependencies),
            )
        )
    if not members:
        raise ReleaseArtifactError(f"no workspace package members found under {root}")
    return sorted(members, key=lambda member: member.name.casefold())


def dependency_version(dependency: str, members: Sequence[PackageMetadata]) -> str:
    """Resolve a declared dependency to its workspace member version."""
    name = dependency.split("[", 1)[0].strip()
    for member in members:
        if member.name.casefold() == name.casefold():
            return member.version
    raise ReleaseArtifactError(f"declared dependency is not a workspace member: {dependency}")


def expected_artifact_files(package: PackageMetadata) -> tuple[str, str]:
    """Return the deterministic (wheel, sdist) file names for a package."""
    normalized = package.name.replace("-", "_")
    return (
        f"{normalized}-{package.version}-py3-none-any.whl",
        f"{normalized}-{package.version}.tar.gz",
    )


def sbom_filename(package: PackageMetadata) -> str:
    return f"{package.name}-{package.version}.sbom.json"


def collect_artifacts(
    directory: Path, packages: Sequence[PackageMetadata]
) -> dict[str, tuple[ArtifactRecord, ArtifactRecord]]:
    """Collect and hash the (wheel, sdist) artifacts built into ``directory``."""
    collected: dict[str, tuple[ArtifactRecord, ArtifactRecord]] = {}
    for package in packages:
        wheel_name, sdist_name = expected_artifact_files(package)
        wheel_path = directory / wheel_name
        sdist_path = directory / sdist_name
        if not wheel_path.is_file() or not sdist_path.is_file():
            raise ReleaseArtifactError(f"missing built artifacts for {package.name} in {directory}")
        collected[package.name] = (
            ArtifactRecord(
                file=wheel_name,
                kind="wheel",
                sha256=sha256_file(wheel_path),
                path=wheel_path,
            ),
            ArtifactRecord(
                file=sdist_name,
                kind="sdist",
                sha256=sha256_file(sdist_path),
                path=sdist_path,
            ),
        )
    return collected


def build_package_sbom(
    package: PackageMetadata,
    members: Sequence[PackageMetadata],
    python_version: str,
) -> SoftwareBillOfMaterials:
    """Build the minimal declared-dependency SBOM for one package.

    Components are the Python runtime plus each declared workspace dependency,
    sorted by name for a deterministic digest. The scope metadata explicitly
    declares that this SBOM lists declared dependencies only and is not a
    transitive dependency or vulnerability scan.
    """
    components: list[SoftwareComponent] = [
        SoftwareComponent(
            name="Python",
            version=python_version,
            component_type="runtime",
            purl=f"pkg:generic/python@{python_version}",
        )
    ]
    for dependency in sorted(package.dependencies, key=str.casefold):
        version = dependency_version(dependency, members)
        components.append(
            SoftwareComponent(
                name=dependency,
                version=version,
                component_type="library",
                purl=f"pkg:pypi/{dependency}@{version}",
            )
        )
    return SoftwareBillOfMaterials.create(
        generator=RELEASE_GENERATOR,
        components=tuple(components),
        metadata={"scope": SBOM_SCOPE},
    )


def compare_artifact_sets(
    primary: Mapping[str, tuple[ArtifactRecord, ArtifactRecord]],
    secondary: Mapping[str, tuple[ArtifactRecord, ArtifactRecord]],
) -> ReproducibilityEvidence:
    """Byte-compare two builds per artifact and record every difference."""
    differences: list[ArtifactDifference] = []
    for name in sorted(primary):
        if name not in secondary:
            raise ReleaseArtifactError(f"secondary build is missing package {name}")
        secondary_by_kind = {record.kind: record for record in secondary[name]}
        for record in primary[name]:
            other = secondary_by_kind.get(record.kind)
            if other is None:
                raise ReleaseArtifactError(
                    f"secondary build is missing {record.kind} for package {name}"
                )
            if record.sha256 != other.sha256:
                differences.append(
                    ArtifactDifference(
                        file=record.file,
                        kind=record.kind,
                        primary_sha256=record.sha256,
                        secondary_sha256=other.sha256,
                    )
                )
    return ReproducibilityEvidence(
        reproducible=not differences,
        builds_compared=2,
        differences=tuple(differences),
    )


def source_build_sha256(entries: Sequence[tuple[str, Path]]) -> str:
    """Content-address the tracked source files that feed a build.

    The digest covers the relative path and bytes of every entry; it is not a
    Git commit identifier. Entries are expected to come from ``git ls-files``.
    """
    digests = {
        relative: hashlib.sha256(path.read_bytes()).hexdigest() for relative, path in entries
    }
    return content_sha256(digests)


def assemble_manifest(
    *,
    packages: Sequence[PackageMetadata],
    artifacts: Mapping[str, tuple[ArtifactRecord, ArtifactRecord]],
    sboms: Mapping[str, SoftwareBillOfMaterials],
    source: SourceState,
    environment: EnvironmentSnapshot,
    reproducibility: ReproducibilityEvidence,
) -> dict[str, JsonValue]:
    """Assemble the deterministic release manifest for the bundle."""
    package_entries: list[JsonValue] = []
    for package in packages:
        wheel, sdist = artifacts[package.name]
        sbom = sboms[package.name]
        package_entries.append(
            {
                "name": package.name,
                "version": package.version,
                "artifacts": [wheel.to_dict(), sdist.to_dict()],
                "sbom": {
                    "file": sbom_filename(package),
                    "schema": sbom.schema_version,
                    "sha256": sbom.canonical_sha256,
                },
            }
        )
    return {
        "schema_version": RELEASE_MANIFEST_SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "source": source.to_dict(),
        "environment": environment.to_dict(),
        "reproducibility": reproducibility.to_dict(),
        "packages": package_entries,
    }
