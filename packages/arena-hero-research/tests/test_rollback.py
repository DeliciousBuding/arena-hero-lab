"""Offline release rollback drill verification core tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arena_hero_research.provenance import EnvironmentSnapshot
from arena_hero_research.release import (
    ArtifactRecord,
    PackageMetadata,
    SourceState,
    assemble_manifest,
    build_package_sbom,
    compare_artifact_sets,
    expected_artifact_files,
    sha256_file,
    workspace_members,
)
from arena_hero_research.rollback import (
    MANIFEST_FILENAME,
    ROLLBACK_DRILL_SCHEMA,
    STATUS_DIFF,
    STATUS_PASS,
    RollbackDrillError,
    apply_version_bump,
    bundle_identity,
    compare_artifact_hashes,
    next_patch_version,
    rollback_evidence,
)
from arena_hero_sim.serialization import canonical_json_bytes

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _environment() -> EnvironmentSnapshot:
    return EnvironmentSnapshot.create(
        python_implementation="CPython",
        python_version="3.12.10",
        operating_system="Linux",
        operating_system_release="6.8.0",
        machine="x86_64",
        executor="lab-release",
    )


def _source() -> SourceState:
    return SourceState(commit="a" * 40, dirty=False, source_build_sha256="b" * 64)


def _packages(version: str) -> list[PackageMetadata]:
    return [
        PackageMetadata(name=member.name, version=version, dependencies=member.dependencies)
        for member in workspace_members(WORKSPACE_ROOT)
    ]


def _write_bundle(directory: Path, *, version: str = "0.2.0") -> dict[str, str]:
    """Write a minimal but valid release bundle into ``directory``.

    Returns the recorded artifact file -> sha256 mapping.
    """
    directory.mkdir(parents=True, exist_ok=True)
    packages = _packages(version)
    environment = _environment()
    artifacts: dict[str, tuple[ArtifactRecord, ArtifactRecord]] = {}
    hashes: dict[str, str] = {}
    for package in packages:
        wheel_name, sdist_name = expected_artifact_files(package)
        wheel = directory / wheel_name
        sdist = directory / sdist_name
        wheel.write_bytes(f"wheel:{package.name}:{version}".encode())
        sdist.write_bytes(f"sdist:{package.name}:{version}".encode())
        artifacts[package.name] = (
            ArtifactRecord(file=wheel_name, kind="wheel", sha256=sha256_file(wheel), path=wheel),
            ArtifactRecord(file=sdist_name, kind="sdist", sha256=sha256_file(sdist), path=sdist),
        )
        hashes[wheel_name] = artifacts[package.name][0].sha256
        hashes[sdist_name] = artifacts[package.name][1].sha256
    sboms = {
        package.name: build_package_sbom(package, packages, environment.python_version)
        for package in packages
    }
    for package in packages:
        (directory / f"{package.name}-{package.version}.sbom.json").write_bytes(
            canonical_json_bytes(sboms[package.name].to_dict())
        )
    manifest = assemble_manifest(
        packages=packages,
        artifacts=artifacts,
        sboms=sboms,
        source=_source(),
        environment=environment,
        reproducibility=compare_artifact_sets(artifacts, artifacts),
    )
    (directory / MANIFEST_FILENAME).write_bytes(canonical_json_bytes(manifest))
    return hashes


def test_bundle_identity_accepts_consistent_bundle(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "v1"
    hashes = _write_bundle(bundle_dir)

    identity = bundle_identity(bundle_dir)

    assert identity.bundle == "v1"
    assert identity.manifest_sha256 == sha256_file(bundle_dir / MANIFEST_FILENAME)
    assert identity.hashes() == hashes
    assert identity.source == _source()


def test_bundle_identity_rejects_tampered_artifact(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "v1"
    hashes = _write_bundle(bundle_dir)
    tampered = next(iter(hashes))
    (bundle_dir / tampered).write_bytes(b"tampered")

    with pytest.raises(RollbackDrillError):
        bundle_identity(bundle_dir)


def test_bundle_identity_rejects_missing_artifact(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "v1"
    hashes = _write_bundle(bundle_dir)
    missing = next(iter(hashes))
    (bundle_dir / missing).unlink()

    with pytest.raises(RollbackDrillError):
        bundle_identity(bundle_dir)


def test_bundle_identity_rejects_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(RollbackDrillError):
        bundle_identity(tmp_path / "missing")


def test_bundle_identity_rejects_unsupported_schema(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "v1"
    _write_bundle(bundle_dir)
    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "arena.lab.unknown.v1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RollbackDrillError):
        bundle_identity(bundle_dir)


def test_compare_artifact_hashes_records_differences() -> None:
    differences = compare_artifact_hashes(
        {"a-0.2.0.whl": "1" * 64, "same.whl": "3" * 64},
        {"a-0.2.1.whl": "2" * 64, "same.whl": "3" * 64},
    )

    assert len(differences) == 2
    by_file = {difference.file: difference for difference in differences}
    assert by_file["a-0.2.0.whl"].kind == "wheel"
    assert by_file["a-0.2.0.whl"].primary_sha256 == "1" * 64
    assert by_file["a-0.2.0.whl"].secondary_sha256 == ""
    assert by_file["a-0.2.1.whl"].kind == "wheel"
    assert by_file["a-0.2.1.whl"].primary_sha256 == ""
    assert by_file["a-0.2.1.whl"].secondary_sha256 == "2" * 64


def test_compare_artifact_hashes_empty_when_identical() -> None:
    hashes = {"a.whl": "1" * 64, "a.tar.gz": "2" * 64}

    assert compare_artifact_hashes(hashes, hashes) == ()


def test_rollback_evidence_passes_when_restored_matches_initial(tmp_path: Path) -> None:
    initial_dir = tmp_path / "v1"
    restored_dir = tmp_path / "v1-restored"
    _write_bundle(initial_dir)
    _write_bundle(restored_dir)
    initial = bundle_identity(initial_dir)
    restored = bundle_identity(restored_dir)

    evidence = rollback_evidence(
        initial,
        restored,
        initial_manifest_bytes=(initial_dir / MANIFEST_FILENAME).read_bytes(),
        restored_manifest_bytes=(restored_dir / MANIFEST_FILENAME).read_bytes(),
    )

    assert evidence.status == STATUS_PASS
    assert evidence.manifest_equal
    assert evidence.source_equal
    assert evidence.artifact_differences == ()


def test_rollback_evidence_records_diff_when_restored_changed(tmp_path: Path) -> None:
    initial_dir = tmp_path / "v1"
    restored_dir = tmp_path / "v1-restored"
    _write_bundle(initial_dir)
    _write_bundle(restored_dir, version="0.2.1")
    initial = bundle_identity(initial_dir)
    restored = bundle_identity(restored_dir)

    evidence = rollback_evidence(
        initial,
        restored,
        initial_manifest_bytes=(initial_dir / MANIFEST_FILENAME).read_bytes(),
        restored_manifest_bytes=(restored_dir / MANIFEST_FILENAME).read_bytes(),
    )

    assert evidence.status == STATUS_DIFF
    assert not evidence.manifest_equal
    assert len(evidence.artifact_differences) == 12


def test_next_patch_version_bumps_patch() -> None:
    assert next_patch_version("0.2.0") == "0.2.1"
    assert next_patch_version("1.9.9") == "1.9.10"


def test_next_patch_version_rejects_malformed() -> None:
    with pytest.raises(RollbackDrillError):
        next_patch_version("not-a-version")


def test_apply_version_bump_patches_every_member(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        package_dir = tmp_path / "packages" / name
        package_dir.mkdir(parents=True)
        (package_dir / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.2.0"\n', encoding="utf-8"
        )

    patched = apply_version_bump(tmp_path / "packages", "0.2.0", "0.2.1")

    assert patched == 2
    for name in ("alpha", "beta"):
        text = (tmp_path / "packages" / name / "pyproject.toml").read_text(encoding="utf-8")
        assert 'version = "0.2.1"' in text


def test_apply_version_bump_fails_closed_without_match(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "alpha"
    package_dir.mkdir(parents=True)
    (package_dir / "pyproject.toml").write_text(
        '[project]\nname = "alpha"\nversion = "1.0.0"\n', encoding="utf-8"
    )

    with pytest.raises(RollbackDrillError):
        apply_version_bump(tmp_path / "packages", "0.2.0", "0.2.1")


def test_rollback_drill_schema_is_stable() -> None:
    assert ROLLBACK_DRILL_SCHEMA == "arena.lab.rollback-drill.v1"
