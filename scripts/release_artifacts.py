"""Build the reproducible Lab release bundle with SBOMs and content hashes.

Usage:
    uv run python scripts/release_artifacts.py [--output dist/release]

The bundle contains the built wheels and sdists for arena-hero-sim,
arena-hero-bench, and arena-hero-research, one minimal SBOM per package
(``arena.research.sbom.v1``), and a content-addressed release manifest
(``arena.lab.release-manifest.v1``). Every artifact is bound to a SHA-256
digest, and reproducibility is verified by byte-comparing two builds from the
same source state; any difference is recorded explicitly in the manifest and
the command exits non-zero. The command is offline-only and never publishes,
deploys, or writes production data.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Never

from arena_hero_research.provenance import EnvironmentSnapshot
from arena_hero_research.release import (
    DEFAULT_EXECUTOR,
    RELEASE_MANIFEST_SCHEMA,
    ArtifactRecord,
    PackageMetadata,
    ReleaseArtifactError,
    ReproducibilityEvidence,
    SoftwareBillOfMaterials,
    SourceState,
    assemble_manifest,
    build_package_sbom,
    collect_artifacts,
    compare_artifact_sets,
    sbom_filename,
    source_build_sha256,
    workspace_members,
)
from arena_hero_sim.serialization import canonical_json_bytes

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = WORKSPACE_ROOT / "dist" / "release"
MANIFEST_FILENAME = "release-manifest.json"


def log(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> Never:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], *, cwd: Path = WORKSPACE_ROOT) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        fail(f"required executable is not available: {command[0]}")
    log(f"==> {' '.join(command)}")
    completed = subprocess.run([executable, *command[1:]], cwd=cwd, check=False)
    if completed.returncode != 0:
        fail(f"command failed with exit code {completed.returncode}: {' '.join(command)}")


def git_source_state(root: Path) -> SourceState:
    """Capture deterministic source anchors: HEAD commit, dirty flag, tree digest."""
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
    )
    dirty = bool(status.stdout.strip())
    files = subprocess.run(
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    )
    entries = [(line, root / line) for line in files.stdout.splitlines() if line]
    return SourceState(
        commit=commit,
        dirty=dirty,
        source_build_sha256=source_build_sha256(entries),
    )


def build_once(directory: Path) -> dict[str, tuple[ArtifactRecord, ArtifactRecord]]:
    run(["uv", "build", "--all-packages", "--out-dir", str(directory)])
    return collect_artifacts(directory, workspace_members(WORKSPACE_ROOT))


def write_bundle(
    output: Path,
    members: list[PackageMetadata],
    artifacts: dict[str, tuple[ArtifactRecord, ArtifactRecord]],
    sboms: dict[str, SoftwareBillOfMaterials],
    source: SourceState,
    environment: EnvironmentSnapshot,
    reproducibility: ReproducibilityEvidence,
) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    for package in members:
        for record in artifacts[package.name]:
            shutil.copyfile(record.path, output / record.file)
        sbom = sboms[package.name]
        (output / sbom_filename(package)).write_bytes(canonical_json_bytes(sbom.to_dict()))
    manifest = assemble_manifest(
        packages=members,
        artifacts=artifacts,
        sboms=sboms,
        source=source,
        environment=environment,
        reproducibility=reproducibility,
    )
    manifest_path = output / MANIFEST_FILENAME
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    return manifest_path


def print_summary(
    output: Path,
    manifest_path: Path,
    members: list[PackageMetadata],
    artifacts: dict[str, tuple[ArtifactRecord, ArtifactRecord]],
    sboms: dict[str, SoftwareBillOfMaterials],
    reproducibility: ReproducibilityEvidence,
) -> None:
    log(f"[release] {RELEASE_MANIFEST_SCHEMA} manifest -> {manifest_path}")
    log(
        "[release] reproducible: "
        f"{'yes' if reproducibility.reproducible else 'no'} "
        f"({reproducibility.builds_compared} builds, "
        f"{len(reproducibility.differences)} differences)"
    )
    for package in members:
        wheel, sdist = artifacts[package.name]
        sbom = sboms[package.name]
        log(
            f"[release] {package.name} {package.version}: "
            f"wheel {wheel.sha256[:16]} sdist {sdist.sha256[:16]} "
            f"sbom {sbom.canonical_sha256[:16]}"
        )
    for difference in reproducibility.differences:
        log(
            f"[release] difference {difference.kind} {difference.file}: "
            f"{difference.primary_sha256} != {difference.secondary_sha256}"
        )
    log(f"[release] bundle: {output}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="destination for the release bundle (default: dist/release)",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    members = workspace_members(WORKSPACE_ROOT)
    environment = EnvironmentSnapshot.capture_public(executor=DEFAULT_EXECUTOR)
    source = git_source_state(WORKSPACE_ROOT)
    try:
        with tempfile.TemporaryDirectory() as temp:
            primary_dir = Path(temp) / "build-a"
            secondary_dir = Path(temp) / "build-b"
            primary_dir.mkdir()
            secondary_dir.mkdir()
            log(f"==> building primary artifacts into {primary_dir}")
            primary = build_once(primary_dir)
            log(f"==> building secondary artifacts into {secondary_dir}")
            secondary = build_once(secondary_dir)
            reproducibility = compare_artifact_sets(primary, secondary)
            sboms = {
                package.name: build_package_sbom(package, members, environment.python_version)
                for package in members
            }
            manifest_path = write_bundle(
                args.output,
                members,
                primary,
                sboms,
                source,
                environment,
                reproducibility,
            )
    except ReleaseArtifactError as exc:
        fail(str(exc))
    print_summary(
        args.output,
        manifest_path,
        members,
        primary,
        sboms,
        reproducibility,
    )
    return 0 if reproducibility.reproducible else 1


if __name__ == "__main__":
    raise SystemExit(main())
