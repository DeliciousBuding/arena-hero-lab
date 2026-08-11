"""Release bundle manifest, SBOM, and reproducibility evidence tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arena_hero_research.provenance import EnvironmentSnapshot, SoftwareBillOfMaterials
from arena_hero_research.release import (
    GENERATOR_VERSION,
    RELEASE_MANIFEST_SCHEMA,
    SBOM_SCOPE,
    ArtifactRecord,
    PackageMetadata,
    ReleaseArtifactError,
    SourceState,
    assemble_manifest,
    build_package_sbom,
    collect_artifacts,
    compare_artifact_sets,
    expected_artifact_files,
    sbom_filename,
    sha256_file,
    source_build_sha256,
    workspace_members,
)
from arena_hero_sim.serialization import canonical_json_bytes, content_sha256

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


def _record(tmp_path: Path, name: str, kind: str, payload: bytes) -> ArtifactRecord:
    path = tmp_path / name
    path.write_bytes(payload)
    return ArtifactRecord(file=name, kind=kind, sha256=sha256_file(path), path=path)


def test_workspace_members_are_deterministic() -> None:
    members = workspace_members(WORKSPACE_ROOT)

    assert [member.name for member in members] == [
        "arena-hero-bench",
        "arena-hero-research",
        "arena-hero-sim",
    ]
    assert all(member.version == "0.2.0" for member in members)
    by_name = {member.name: member for member in members}
    assert by_name["arena-hero-sim"].dependencies == ()
    assert by_name["arena-hero-bench"].dependencies == ("arena-hero-sim",)
    assert by_name["arena-hero-research"].dependencies == (
        "arena-hero-bench",
        "arena-hero-sim",
    )


def test_workspace_members_parse_minimal_fixture(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "demo"
    package_dir.mkdir(parents=True)
    (package_dir / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.2.3"\n', encoding="utf-8"
    )

    members = workspace_members(tmp_path)

    assert members == [PackageMetadata(name="demo", version="1.2.3", dependencies=())]


def test_workspace_members_rejects_broken_fixture(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "demo"
    package_dir.mkdir(parents=True)
    (package_dir / "pyproject.toml").write_text("[tool]\nfoo = 1\n", encoding="utf-8")

    with pytest.raises(ReleaseArtifactError):
        workspace_members(tmp_path)


def test_expected_artifact_files_match_hatchling_layout() -> None:
    package = PackageMetadata(
        name="arena-hero-sim",
        version="0.2.0",
        dependencies=(),
    )

    assert expected_artifact_files(package) == (
        "arena_hero_sim-0.2.0-py3-none-any.whl",
        "arena_hero_sim-0.2.0.tar.gz",
    )
    assert sbom_filename(package) == "arena-hero-sim-0.2.0.sbom.json"


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "bytes.bin"
    path.write_bytes(b"hello release")

    assert sha256_file(path) == content_sha256(b"hello release")


def test_collect_artifacts_round_trip(tmp_path: Path) -> None:
    members = [
        member for member in workspace_members(WORKSPACE_ROOT) if member.name == "arena-hero-sim"
    ]
    wheel_name, sdist_name = expected_artifact_files(members[0])
    (tmp_path / wheel_name).write_bytes(b"wheel")
    (tmp_path / sdist_name).write_bytes(b"sdist")

    artifacts = collect_artifacts(tmp_path, members)

    wheel, sdist = artifacts["arena-hero-sim"]
    assert wheel.kind == "wheel"
    assert sdist.kind == "sdist"
    assert wheel.sha256 == content_sha256(b"wheel")
    assert sdist.sha256 == content_sha256(b"sdist")


def test_collect_artifacts_fails_closed_on_missing_file(tmp_path: Path) -> None:
    members = [
        member for member in workspace_members(WORKSPACE_ROOT) if member.name == "arena-hero-sim"
    ]

    with pytest.raises(ReleaseArtifactError):
        collect_artifacts(tmp_path, members)


def test_source_build_sha256_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    entries = [("first.py", first), ("second.py", second)]
    original = source_build_sha256(entries)

    assert source_build_sha256(entries) == original
    assert source_build_sha256([("first.py", first), ("renamed.py", second)]) != original
    second.write_bytes(b"changed")
    assert source_build_sha256(entries) != original


def test_build_package_sbom_discloses_declared_dependencies(tmp_path: Path) -> None:
    members = workspace_members(WORKSPACE_ROOT)
    bench = next(member for member in members if member.name == "arena-hero-bench")
    sim = next(member for member in members if member.name == "arena-hero-sim")

    bench_sbom = build_package_sbom(bench, members, "3.12.10")
    sim_sbom = build_package_sbom(sim, members, "3.12.10")

    assert bench_sbom.verify()
    assert sim_sbom.verify()
    assert bench_sbom.schema_version == "arena.research.sbom.v1"
    assert bench_sbom.metadata["scope"] == SBOM_SCOPE
    assert [component.name for component in bench_sbom.components] == [
        "arena-hero-sim",
        "Python",
    ]
    assert [component.name for component in sim_sbom.components] == ["Python"]
    assert bench_sbom.components[0].purl == "pkg:pypi/arena-hero-sim@0.2.0"


def test_sbom_digest_is_stable_across_rebuilds(tmp_path: Path) -> None:
    members = workspace_members(WORKSPACE_ROOT)
    research = next(member for member in members if member.name == "arena-hero-research")

    first = build_package_sbom(research, members, "3.12.10")
    second = build_package_sbom(research, members, "3.12.10")

    assert first.canonical_sha256 == second.canonical_sha256
    assert SoftwareBillOfMaterials.from_dict(first.to_dict()) == first


def test_compare_artifact_sets_reports_identical_builds(tmp_path: Path) -> None:
    primary = {
        "arena-hero-sim": (
            _record(tmp_path, "sim.whl", "wheel", b"same"),
            _record(tmp_path, "sim.tar.gz", "sdist", b"same"),
        )
    }
    secondary = {
        "arena-hero-sim": (
            _record(tmp_path, "sim.whl", "wheel", b"same"),
            _record(tmp_path, "sim.tar.gz", "sdist", b"same"),
        )
    }

    evidence = compare_artifact_sets(primary, secondary)

    assert evidence.reproducible
    assert evidence.builds_compared == 2
    assert evidence.differences == ()


def test_compare_artifact_sets_records_differences(tmp_path: Path) -> None:
    primary = {
        "arena-hero-sim": (
            _record(tmp_path, "sim.whl", "wheel", b"same"),
            _record(tmp_path, "sim.tar.gz", "sdist", b"same"),
        )
    }
    secondary = {
        "arena-hero-sim": (
            _record(tmp_path, "sim.whl", "wheel", b"drifted"),
            _record(tmp_path, "sim.tar.gz", "sdist", b"same"),
        )
    }

    evidence = compare_artifact_sets(primary, secondary)

    assert not evidence.reproducible
    assert len(evidence.differences) == 1
    difference = evidence.differences[0]
    assert difference.kind == "wheel"
    assert difference.primary_sha256 == content_sha256(b"same")
    assert difference.secondary_sha256 == content_sha256(b"drifted")


def test_compare_artifact_sets_fails_closed_on_missing_package(tmp_path: Path) -> None:
    primary = {
        "arena-hero-sim": (
            _record(tmp_path, "sim.whl", "wheel", b"same"),
            _record(tmp_path, "sim.tar.gz", "sdist", b"same"),
        )
    }

    with pytest.raises(ReleaseArtifactError):
        compare_artifact_sets(primary, {})


def test_manifest_is_deterministic_and_self_consistent(tmp_path: Path) -> None:
    members = workspace_members(WORKSPACE_ROOT)
    source = SourceState(
        commit="a" * 40,
        dirty=False,
        source_build_sha256="b" * 64,
    )
    environment = _environment()
    artifacts: dict[str, tuple[ArtifactRecord, ArtifactRecord]] = {}
    sboms: dict[str, SoftwareBillOfMaterials] = {}
    for member in members:
        wheel = _record(tmp_path, f"{member.name}.whl", "wheel", member.name.encode())
        sdist = _record(tmp_path, f"{member.name}.tar.gz", "sdist", member.version.encode())
        artifacts[member.name] = (wheel, sdist)
        sboms[member.name] = build_package_sbom(member, members, environment.python_version)
    reproducibility = compare_artifact_sets(artifacts, artifacts)

    manifest = assemble_manifest(
        packages=members,
        artifacts=artifacts,
        sboms=sboms,
        source=source,
        environment=environment,
        reproducibility=reproducibility,
    )
    reassembled = assemble_manifest(
        packages=members,
        artifacts=artifacts,
        sboms=sboms,
        source=source,
        environment=environment,
        reproducibility=reproducibility,
    )

    assert canonical_json_bytes(manifest) == canonical_json_bytes(reassembled)
    decoded = json.loads(canonical_json_bytes(manifest))
    assert decoded["schema_version"] == RELEASE_MANIFEST_SCHEMA
    assert decoded["generator_version"] == GENERATOR_VERSION
    assert decoded["source"] == source.to_dict()
    assert decoded["reproducibility"] == reproducibility.to_dict()
    package_by_name = {entry["name"]: entry for entry in decoded["packages"]}
    for member in members:
        wheel, sdist = artifacts[member.name]
        entry = package_by_name[member.name]
        assert entry["artifacts"] == [wheel.to_dict(), sdist.to_dict()]
        assert entry["sbom"]["sha256"] == sboms[member.name].canonical_sha256
        assert entry["sbom"]["schema"] == "arena.research.sbom.v1"
