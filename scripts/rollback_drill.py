"""Offline release rollback drill: baseline -> next version -> rollback closed loop.

Usage:
    uv run python scripts/rollback_drill.py [--output dist/drill]

The drill proves that the release artifact surface can switch forward and back
without any live runtime:

1. ``v1`` builds the current source state with the release builder machinery
   and records every artifact digest plus the release manifest digest.
2. ``v2`` materializes an isolated shadow source tree from the tracked HEAD
   files, applies a deterministic version marker (a patch version bump), and
   builds the next-version bundle there. The bundle is verified and its digest
   differences versus v1 are recorded explicitly.
3. ``v1-restored`` rebuilds the current source state and verifies that every
   artifact digest, the source anchor, and the manifest bytes match v1.

The drill is offline-only: builds run through ``uv build --offline``, nothing
is published or deployed, no registry is written, and the shadow tree lives in
a temporary directory. A deterministic report (``arena.lab.rollback-drill.v1``)
is written to ``<output>/drill-report.json`` and the command exits non-zero
when any step fails.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Never

import release_artifacts

from arena_hero_research.provenance import EnvironmentSnapshot
from arena_hero_research.release import (
    DEFAULT_EXECUTOR,
    ReleaseArtifactError,
    ReproducibilityEvidence,
    SourceState,
    build_package_sbom,
    collect_artifacts,
    source_build_sha256,
    workspace_members,
)
from arena_hero_research.rollback import (
    MANIFEST_FILENAME,
    ROLLBACK_DRILL_SCHEMA,
    ROLLBACK_DRILL_VERSION,
    STATUS_PASS,
    RollbackDrillError,
    apply_version_bump,
    bundle_identity,
    compare_artifact_hashes,
    next_patch_version,
    rollback_evidence,
)
from arena_hero_sim.serialization import JsonValue, canonical_json_bytes

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = WORKSPACE_ROOT / "dist" / "drill"
REPORT_FILENAME = "drill-report.json"


def log(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> Never:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def git_archive_files(root: Path, destination: Path) -> None:
    """Materialize the tracked HEAD files of ``root`` into ``destination``."""
    completed = subprocess.run(
        ["git", "archive", "HEAD"], cwd=root, check=True, capture_output=True
    )
    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
        archive.extractall(destination)


def shadow_source_state(root: Path, shadow: Path, commit: str) -> SourceState:
    """Anchor a shadow tree: the baseline commit, clean, with its own digest."""
    completed = subprocess.run(
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    )
    entries = [(line, shadow / line) for line in completed.stdout.splitlines() if line]
    return SourceState(
        commit=commit,
        dirty=False,
        source_build_sha256=source_build_sha256(entries),
    )


def build_bundle(
    *,
    root: Path,
    output: Path,
    source: SourceState,
    environment: EnvironmentSnapshot,
) -> Path:
    """Build every workspace package from ``root`` and write a release bundle."""
    members = workspace_members(root)
    with tempfile.TemporaryDirectory() as temp:
        build_dir = Path(temp)
        release_artifacts.run(
            ["uv", "build", "--all-packages", "--offline", "--out-dir", str(build_dir)],
            cwd=root,
        )
        artifacts = collect_artifacts(build_dir, members)
        sboms = {
            package.name: build_package_sbom(package, members, environment.python_version)
            for package in members
        }
        reproducibility = ReproducibilityEvidence(
            reproducible=True,
            builds_compared=1,
            differences=(),
        )
        return release_artifacts.write_bundle(
            output,
            members,
            artifacts,
            sboms,
            source,
            environment,
            reproducibility,
        )


def materialize_next_version(
    root: Path,
    destination: Path,
    from_version: str,
    to_version: str,
) -> int:
    """Materialize the tracked HEAD files and apply the version marker."""
    git_archive_files(root, destination)
    return apply_version_bump(destination / "packages", from_version, to_version)


def run_drill(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    environment = EnvironmentSnapshot.capture_public(executor=DEFAULT_EXECUTOR)
    baseline_source = release_artifacts.git_source_state(WORKSPACE_ROOT)
    members = workspace_members(WORKSPACE_ROOT)
    if not members:
        fail("no workspace packages found")
    from_version = members[0].version
    to_version = next_patch_version(from_version)

    log("[drill] phase 1/3: build baseline v1")
    build_bundle(
        root=WORKSPACE_ROOT,
        output=output / "v1",
        source=baseline_source,
        environment=environment,
    )
    v1 = bundle_identity(output / "v1")

    log("[drill] phase 2/3: build next version v2 from shadow source")
    with tempfile.TemporaryDirectory() as temp:
        shadow = Path(temp)
        patched = materialize_next_version(WORKSPACE_ROOT, shadow, from_version, to_version)
        next_source = shadow_source_state(WORKSPACE_ROOT, shadow, baseline_source.commit)
        build_bundle(
            root=shadow,
            output=output / "v2",
            source=next_source,
            environment=environment,
        )
    v2 = bundle_identity(output / "v2")

    log("[drill] phase 3/3: rebuild baseline v1 (rollback)")
    restored_source = release_artifacts.git_source_state(WORKSPACE_ROOT)
    build_bundle(
        root=WORKSPACE_ROOT,
        output=output / "v1-restored",
        source=restored_source,
        environment=environment,
    )
    v1_restored = bundle_identity(output / "v1-restored")

    v2_differences = compare_artifact_hashes(v1.hashes(), v2.hashes())
    rollback = rollback_evidence(
        v1,
        v1_restored,
        initial_manifest_bytes=(output / "v1" / MANIFEST_FILENAME).read_bytes(),
        restored_manifest_bytes=(output / "v1-restored" / MANIFEST_FILENAME).read_bytes(),
    )
    steps: list[JsonValue] = [
        {
            "id": "build-v1",
            "phase": "baseline",
            "status": STATUS_PASS,
            **v1.to_dict(),
        },
        {
            "id": "build-v2",
            "phase": "next",
            "status": STATUS_PASS,
            **v2.to_dict(),
            "differences_vs_v1": [difference.to_dict() for difference in v2_differences],
            "differs_from_v1": bool(v2_differences),
        },
        {
            "id": "rollback-v1",
            "phase": "rollback",
            "status": rollback.status,
            **v1_restored.to_dict(),
            "manifest_equal_to_v1": rollback.manifest_equal,
            "source_equal_to_v1": rollback.source_equal,
            "artifact_differences_vs_v1": [
                difference.to_dict() for difference in rollback.artifact_differences
            ],
        },
    ]
    statuses = [STATUS_PASS, STATUS_PASS, rollback.status]
    overall = STATUS_PASS if all(status == STATUS_PASS for status in statuses) else "fail"
    report: dict[str, JsonValue] = {
        "schema_version": ROLLBACK_DRILL_SCHEMA,
        "generator_version": ROLLBACK_DRILL_VERSION,
        "baseline_source": baseline_source.to_dict(),
        "marker": {
            "kind": "version-bump",
            "from_version": from_version,
            "to_version": to_version,
            "patched_files": patched,
        },
        "environment": environment.to_dict(),
        "steps": steps,
        "overall_status": overall,
    }
    report_path = output / REPORT_FILENAME
    report_path.write_bytes(canonical_json_bytes(report))

    log(f"[drill] {ROLLBACK_DRILL_SCHEMA} report -> {report_path}")
    log(
        f"[drill] v1 baseline: manifest={v1.manifest_sha256[:16]} "
        f"artifacts={len(v1.artifact_hashes)} source={v1.source.source_build_sha256[:16]}"
    )
    log(
        f"[drill] v2 next: manifest={v2.manifest_sha256[:16]} "
        f"artifacts={len(v2.artifact_hashes)} differs_from_v1={bool(v2_differences)} "
        f"recorded_differences={len(v2_differences)}"
    )
    log(
        f"[drill] rollback v1: status={rollback.status} "
        f"manifest_equal={rollback.manifest_equal} source_equal={rollback.source_equal} "
        f"artifact_differences={len(rollback.artifact_differences)}"
    )
    log(f"[drill] overall: {overall}")
    return 0 if overall == STATUS_PASS else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="destination for drill bundles and report (default: dist/drill)",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run_drill(args.output)
    except (RollbackDrillError, ReleaseArtifactError) as exc:
        return fail(str(exc))
    except subprocess.CalledProcessError as exc:
        return fail(f"subprocess failed with exit code {exc.returncode}: {' '.join(exc.cmd)}")


if __name__ == "__main__":
    raise SystemExit(main())
