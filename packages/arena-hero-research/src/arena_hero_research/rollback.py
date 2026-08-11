"""Offline release rollback drill verification core.

The rollback drill proves that release bundles can be switched forward and
back without touching a live runtime. It builds a baseline bundle (v1), builds
a next-version bundle (v2) from an isolated shadow source tree, then rebuilds
v1 and verifies that the restored bundle is byte-identical to the original.
This module holds the pure verification logic; orchestration lives in
``scripts/rollback_drill.py`` and reuses the release builder machinery.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from arena_hero_research.provenance import SoftwareBillOfMaterials
from arena_hero_research.release import (
    RELEASE_MANIFEST_SCHEMA,
    SourceState,
    sha256_file,
)
from arena_hero_sim.serialization import JsonValue

ROLLBACK_DRILL_SCHEMA: Final = "arena.lab.rollback-drill.v1"
ROLLBACK_DRILL_GENERATOR: Final = "arena-hero-lab"
ROLLBACK_DRILL_VERSION: Final = "0.1.0"
MANIFEST_FILENAME: Final = "release-manifest.json"
STATUS_PASS: Final = "pass"
STATUS_DIFF: Final = "diff"


class RollbackDrillError(RuntimeError):
    """Raised when a drill bundle or step cannot be verified."""


@dataclass(frozen=True, slots=True)
class ArtifactHashDifference:
    """One recorded artifact digest difference between two bundles."""

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
class BundleIdentity:
    """Verified identity of one release bundle."""

    bundle: str
    manifest_sha256: str
    source: SourceState
    artifact_hashes: tuple[tuple[str, str], ...]

    def hashes(self) -> dict[str, str]:
        return dict(self.artifact_hashes)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "bundle": self.bundle,
            "manifest_sha256": self.manifest_sha256,
            "source": self.source.to_dict(),
            "artifacts": dict(self.artifact_hashes),
        }


@dataclass(frozen=True, slots=True)
class RollbackEvidence:
    """Comparison between the original and restored baseline bundles."""

    status: str
    manifest_equal: bool
    source_equal: bool
    artifact_differences: tuple[ArtifactHashDifference, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "status": self.status,
            "manifest_equal": self.manifest_equal,
            "source_equal": self.source_equal,
            "artifact_differences": [
                difference.to_dict() for difference in self.artifact_differences
            ],
        }


def load_manifest(bundle_dir: Path) -> dict[str, JsonValue]:
    """Load and schema-check the release manifest inside a bundle directory."""
    path = bundle_dir / MANIFEST_FILENAME
    if not path.is_file():
        raise RollbackDrillError(f"missing release manifest in {bundle_dir}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RollbackDrillError(f"invalid release manifest in {bundle_dir}: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != RELEASE_MANIFEST_SCHEMA:
        raise RollbackDrillError(f"unsupported release manifest schema in {bundle_dir}")
    return manifest


def _source_from_manifest(bundle_dir: Path, value: object) -> SourceState:
    if not isinstance(value, dict):
        raise RollbackDrillError(f"release manifest in {bundle_dir} has no source anchor")
    commit = value.get("commit")
    dirty = value.get("dirty")
    digest = value.get("source_build_sha256")
    if not isinstance(commit, str) or not isinstance(digest, str) or not isinstance(dirty, bool):
        raise RollbackDrillError(f"invalid source anchor in release manifest {bundle_dir}")
    return SourceState(commit=commit, dirty=dirty, source_build_sha256=digest)


def _verify_artifacts(bundle_dir: Path, manifest: dict[str, JsonValue]) -> list[tuple[str, str]]:
    packages = manifest.get("packages")
    if not isinstance(packages, list) or not packages:
        raise RollbackDrillError(f"release manifest in {bundle_dir} lists no packages")
    hashes: list[tuple[str, str]] = []
    for entry in packages:
        if not isinstance(entry, dict):
            raise RollbackDrillError(f"invalid package entry in release manifest {bundle_dir}")
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, list):
            raise RollbackDrillError(f"invalid artifacts in release manifest {bundle_dir}")
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise RollbackDrillError(
                    f"invalid artifact record in release manifest {bundle_dir}"
                )
            file = artifact.get("file")
            recorded = artifact.get("sha256")
            if not isinstance(file, str) or not isinstance(recorded, str):
                raise RollbackDrillError(
                    f"invalid artifact record in release manifest {bundle_dir}"
                )
            path = bundle_dir / file
            if not path.is_file():
                raise RollbackDrillError(f"missing artifact {file} in bundle {bundle_dir}")
            actual = sha256_file(path)
            if actual != recorded:
                raise RollbackDrillError(
                    f"artifact {file} digest mismatch in bundle {bundle_dir}: "
                    f"{actual} != {recorded}"
                )
            hashes.append((file, recorded))
        sbom = entry.get("sbom")
        if not isinstance(sbom, dict):
            raise RollbackDrillError(f"invalid sbom record in release manifest {bundle_dir}")
        sbom_file = sbom.get("file")
        if not isinstance(sbom_file, str):
            raise RollbackDrillError(f"invalid sbom record in release manifest {bundle_dir}")
        sbom_path = bundle_dir / sbom_file
        if not sbom_path.is_file():
            raise RollbackDrillError(f"missing sbom {sbom_file} in bundle {bundle_dir}")
        try:
            sbom_value = json.loads(sbom_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RollbackDrillError(
                f"invalid sbom {sbom_file} in bundle {bundle_dir}: {exc}"
            ) from exc
        if (
            not isinstance(sbom_value, dict)
            or not SoftwareBillOfMaterials.from_dict(sbom_value).verify()
        ):
            raise RollbackDrillError(f"sbom {sbom_file} failed verification in bundle {bundle_dir}")
    hashes.sort(key=lambda item: item[0])
    return hashes


def bundle_identity(bundle_dir: Path) -> BundleIdentity:
    """Verify a release bundle against its manifest and return its identity.

    Every artifact and SBOM listed in the manifest must exist and match its
    recorded digest; any mismatch raises :class:`RollbackDrillError`.
    """
    manifest = load_manifest(bundle_dir)
    return BundleIdentity(
        bundle=bundle_dir.name,
        manifest_sha256=sha256_file(bundle_dir / MANIFEST_FILENAME),
        source=_source_from_manifest(bundle_dir, manifest.get("source")),
        artifact_hashes=tuple(_verify_artifacts(bundle_dir, manifest)),
    )


def _artifact_kind(file: str) -> str:
    if file.endswith(".whl"):
        return "wheel"
    if file.endswith(".tar.gz"):
        return "sdist"
    return "other"


def compare_artifact_hashes(
    primary: Mapping[str, str],
    secondary: Mapping[str, str],
) -> tuple[ArtifactHashDifference, ...]:
    """Compare artifact digests and record every difference with its kind.

    Files present in only one bundle are recorded with an empty digest on the
    missing side.
    """
    differences: list[ArtifactHashDifference] = []
    for file in sorted(set(primary) | set(secondary)):
        first = primary.get(file, "")
        second = secondary.get(file, "")
        if first != second:
            differences.append(
                ArtifactHashDifference(
                    file=file,
                    kind=_artifact_kind(file),
                    primary_sha256=first,
                    secondary_sha256=second,
                )
            )
    return tuple(differences)


def rollback_evidence(
    initial: BundleIdentity,
    restored: BundleIdentity,
    *,
    initial_manifest_bytes: bytes,
    restored_manifest_bytes: bytes,
) -> RollbackEvidence:
    """Compare the restored baseline bundle with the original bundle.

    The status is ``pass`` only when the manifest bytes, source anchor, and
    every artifact digest are identical; otherwise it is ``diff`` and every
    artifact difference is recorded explicitly.
    """
    manifest_equal = initial_manifest_bytes == restored_manifest_bytes
    source_equal = initial.source == restored.source
    differences = compare_artifact_hashes(initial.hashes(), restored.hashes())
    status = STATUS_PASS if manifest_equal and source_equal and not differences else STATUS_DIFF
    return RollbackEvidence(
        status=status,
        manifest_equal=manifest_equal,
        source_equal=source_equal,
        artifact_differences=differences,
    )


def next_patch_version(version: str) -> str:
    """Return the deterministic next patch version (``0.2.0`` -> ``0.2.1``)."""
    parts = version.split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        raise RollbackDrillError(f"cannot derive a next patch version from {version!r}")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def apply_version_bump(packages_dir: Path, from_version: str, to_version: str) -> int:
    """Apply a deterministic version marker to every workspace package.

    Replaces ``version = "<from_version>"`` with ``version = "<to_version>"``
    in every ``packages/*/pyproject.toml`` and returns the number of patched
    files. Fails closed when no package matched.
    """
    patched = 0
    for path in sorted(packages_dir.glob("*/pyproject.toml")):
        text = path.read_text(encoding="utf-8")
        replaced = text.replace(f'version = "{from_version}"', f'version = "{to_version}"')
        if replaced != text:
            path.write_text(replaced, encoding="utf-8")
            patched += 1
    if patched == 0:
        raise RollbackDrillError(f"version marker {from_version!r} matched no package")
    return patched
