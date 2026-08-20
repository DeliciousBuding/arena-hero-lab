"""Sync production tenant logs from the source host into the local lab dataset.

Pulls each tenant's ``ticks.jsonl``/``ticks.jsonl.N``, ``telemetry.jsonl``,
``live_status.json`` and ``writer-lease.json`` from the production host into
``data/runtime/production/<tenant>/`` (gitignored) so the replay / bench /
diag scripts can run against real logs offline.

Usage (from the arena-hero-lab repo root):
    uv run python scripts/sync_production_logs.py                # incremental (rsync)
    uv run python scripts/sync_production_logs.py --full          # first-time full pull
    uv run python scripts/sync_production_logs.py --tenants t1 t2 # subset
    uv run python scripts/sync_production_logs.py --dry-run       # show what would run

Requirements:
- `rsync` installed locally and on the source host.
- Passwordless SSH from this host to the source host (see ~/.ssh/config).
- The source tenant root is passed via ``--source-root``; both it and the
  host alias stay out of the repo because the lab repo is public while the
  production host and its paths are internal.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "runtime" / "production"
TENANTS = ("t1", "t2", "t3", "t4")
FILE_PATTERNS = ("ticks.jsonl*", "telemetry.jsonl*", "live_status.json", "writer-lease.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        required=True,
        help="source SSH host alias (required; the production host is internal)",
    )
    parser.add_argument(
        "--source-root",
        required=True,
        help="source tenant root on the production host, the directory that "
        "contains one subdirectory per tenant (required; internal paths "
        "never enter the public repo)",
    )
    parser.add_argument(
        "--tenants",
        nargs="+",
        default=list(TENANTS),
        choices=TENANTS,
        help="tenant subset (default: all four)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="local output directory (default: data/runtime/production)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="full copy ignoring existing local state (delete extraneous local files)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print rsync commands without running")
    return parser.parse_args()


def run_sync(args: argparse.Namespace) -> int:
    if shutil.which("rsync") is None:
        print("error: rsync is not installed locally")
        return 1

    failed: list[str] = []
    for tenant in args.tenants:
        output_dir = args.output_root / tenant
        output_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "rsync",
            "-az",
            "--timeout=30",
        ]
        if args.full:
            cmd.append("--delete")
        if args.dry_run:
            cmd.append("--dry-run")
        for pattern in FILE_PATTERNS:
            cmd.append(f"{args.host}:{args.source_root}/{tenant}/{pattern}")
        cmd.append(str(output_dir) + "/")

        if args.dry_run:
            print(" ".join(cmd))
            continue

        print(f"[sync] {tenant} <- {args.host}:{args.source_root}/{tenant}/")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            failed.append(tenant)
            print(result.stderr.strip())
        else:
            # rsync -a already shows per-file lines; summarize local size after.
            size = sum(f.stat().st_size for f in output_dir.glob("ticks.jsonl*") if f.is_file())
            print(f"  -> {output_dir}  ({size / 1e6:.1f} MB ticks)")

    if failed:
        print(f"failed tenants: {failed}")
        return 1
    return 0


def main() -> int:
    args = parse_args()
    if args.dry_run:
        print("dry-run against:", args.host)
    return run_sync(args)


if __name__ == "__main__":
    raise SystemExit(main())
