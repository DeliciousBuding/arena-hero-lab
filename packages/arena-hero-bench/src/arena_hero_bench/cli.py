"""Command-line entry point for Arena Hero benchmark tooling."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from arena_hero_bench.agent_runtime import (
    AGENT_RUN_EVIDENCE_SCHEMA,
    DEFAULT_AGENT_COMMIT,
    DEFAULT_SDK_TAG,
    GENERATOR_VERSION,
    AgentRuntimeImportError,
    import_agent_run,
    source_build_sha256,
)
from arena_hero_bench.converter import convert_file
from arena_hero_bench.differential import (
    DifferentialError,
    run_differential_from_manifest,
)
from arena_hero_bench.kpi_differential import run_kpi_differential_from_manifest
from arena_hero_bench.manifest import ArtifactManifest
from arena_hero_bench.storage import ArtifactStoreError, FilesystemArtifactStore
from arena_hero_sim.serialization import canonical_json_bytes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arena-hero-bench")
    subparsers = parser.add_subparsers(dest="command", required=True)
    convert = subparsers.add_parser("convert", help="convert a v3 report for leaderboard-web")
    convert.add_argument("source", type=Path)
    convert.add_argument("--output", required=True, type=Path)
    convert.add_argument("--source-root", type=Path)
    convert.add_argument("--source-label")
    convert.add_argument("--converted-at")
    import_run = subparsers.add_parser(
        "import-agent-run",
        help="import one offline agent run into a content-addressed lab artifact",
    )
    import_run.add_argument(
        "--records",
        required=True,
        type=Path,
        metavar="PATH",
        help="offline agent records JSONL (tick and loop records)",
    )
    import_run.add_argument(
        "--tenant", required=True, metavar="ID", help="expected tenant id for the run"
    )
    import_run.add_argument(
        "--health",
        type=Path,
        metavar="PATH",
        help="optional offline agent health snapshot JSON",
    )
    import_run.add_argument(
        "--agent-commit",
        default=DEFAULT_AGENT_COMMIT,
        metavar="SHA",
        help=f"public agent commit recorded in provenance (default: {DEFAULT_AGENT_COMMIT})",
    )
    import_run.add_argument(
        "--sdk-tag",
        default=DEFAULT_SDK_TAG,
        metavar="TAG",
        help=f"public SDK tag recorded in provenance (default: {DEFAULT_SDK_TAG})",
    )
    differential = subparsers.add_parser(
        "differential",
        help="classify a TS/Python replay differential run into a content-addressed report",
    )
    differential.add_argument(
        "--run",
        required=True,
        type=Path,
        metavar="MANIFEST",
        help="differential run manifest JSON (paths are relative to the manifest)",
    )
    kpi_differential = subparsers.add_parser(
        "kpi-differential",
        help="classify an evolve/Python Agent KPI differential run into a content-addressed report",
    )
    kpi_differential.add_argument(
        "--run",
        required=True,
        type=Path,
        metavar="MANIFEST",
        help="KPI differential run manifest JSON (paths are relative to the manifest)",
    )
    import_run.add_argument(
        "--store",
        type=Path,
        metavar="PATH",
        help="content-addressed artifact store root; when omitted the digest is only reported",
    )
    return parser


def _import_agent_run_command(args: argparse.Namespace) -> int:
    try:
        evidence = import_agent_run(
            args.records,
            tenant_id=args.tenant,
            health_path=args.health,
            agent_commit=args.agent_commit,
            sdk_tag=args.sdk_tag,
        )
        if args.store is not None:
            store = FilesystemArtifactStore(args.store)
            payload = canonical_json_bytes(evidence.content)
            manifest = ArtifactManifest.for_content(
                content=payload,
                schema_version=AGENT_RUN_EVIDENCE_SCHEMA,
                generator_version=GENERATOR_VERSION,
                provenance=evidence.provenance,
                source_build_sha256=source_build_sha256(args.records, args.health),
            )
            store.store_artifact(manifest, payload)
    except (AgentRuntimeImportError, ArtifactStoreError) as exc:
        print(f"arena-hero-bench: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(evidence.report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _differential_command(args: argparse.Namespace) -> int:
    try:
        report = run_differential_from_manifest(args.run)
    except (DifferentialError, AgentRuntimeImportError) as exc:
        print(f"arena-hero-bench: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_json(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _kpi_differential_command(args: argparse.Namespace) -> int:
    try:
        report = run_kpi_differential_from_manifest(args.run)
    except (DifferentialError, AgentRuntimeImportError) as exc:
        print(f"arena-hero-bench: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_json(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "convert":
        output = convert_file(
            args.source,
            args.output,
            source_root=args.source_root,
            source_label=args.source_label,
            converted_at=args.converted_at,
        )
        print(
            f"[convert] {args.source} -> {args.output} "
            f"({len(output['leaderboard'])} entries, {len(output['scenarios'])} scenarios)"
        )
        return 0
    if args.command == "import-agent-run":
        return _import_agent_run_command(args)
    if args.command == "differential":
        return _differential_command(args)
    if args.command == "kpi-differential":
        return _kpi_differential_command(args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
