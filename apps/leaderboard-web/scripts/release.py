"""Build and optionally publish the static leaderboard application."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Never

SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = APP_ROOT.parents[1]
DEFAULT_SOURCE = SCRIPT_DIR / "input" / "results.json"
DEFAULT_RUN_ROOT = WORKSPACE_ROOT / "artifacts" / "runs"
BENCH_PATH = APP_ROOT / "src" / "data" / "bench.json"
ONLINE_URL = "https://deliciousbuding.github.io/arena-hero-leaderboard/"
REPORT_SCHEMA = "arena.bench.report.v3"


def log(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> Never:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], *, cwd: Path = WORKSPACE_ROOT) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        fail(f"required executable is not available: {command[0]}")
    resolved = [executable, *command[1:]]
    log(f"==> {' '.join(command)}")
    completed = subprocess.run(resolved, cwd=cwd, check=False)
    if completed.returncode != 0:
        fail(f"command failed with exit code {completed.returncode}: {' '.join(command)}")


def find_latest_run(run_root: Path) -> Path:
    if not run_root.is_dir():
        fail(f"run directory does not exist: {run_root}")
    candidates = sorted(
        run_root.rglob("results.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        fail(f"no results.json found under {run_root}")
    return candidates[0]


def read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read JSON from {path}: {error}")
    if not isinstance(value, dict):
        fail(f"expected a JSON object in {path}")
    return value


def choose_source(args: argparse.Namespace) -> Path:
    if args.source is not None:
        return args.source.resolve()
    if args.latest:
        return find_latest_run(args.run_root.resolve())
    return DEFAULT_SOURCE


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    source_group = result.add_mutually_exclusive_group()
    source_group.add_argument("--source", type=Path, help="benchmark results.json")
    source_group.add_argument("--latest", action="store_true", help="use the latest local run")
    result.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    result.add_argument("--force", action="store_true", help="rebuild unchanged input")
    result.add_argument(
        "--deploy",
        action="store_true",
        help="explicitly publish the validated static export to GitHub Pages",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    source = choose_source(args)
    if not source.is_file():
        fail(f"source does not exist: {source}")

    report = read_json(source)
    if report.get("schema") != REPORT_SCHEMA:
        fail(f"unexpected schema: {report.get('schema')} (expected {REPORT_SCHEMA})")

    if BENCH_PATH.exists() and not args.force:
        current = read_json(BENCH_PATH)
        if current.get("schema") == report.get("schema") and current.get(
            "generatedAt"
        ) == report.get("generatedAt"):
            log("==> source already converted; use --force to rebuild")
            return 0

    run(
        [
            "uv",
            "run",
            "arena-hero-bench",
            "convert",
            str(source),
            "--output",
            str(BENCH_PATH),
            "--source-root",
            str(APP_ROOT),
        ]
    )
    run(["pnpm", "--filter", "@arena-hero/leaderboard-web", "build"])
    run(["pnpm", "--filter", "@arena-hero/leaderboard-web", "lint"])

    if args.deploy:
        run(["pnpm", "--filter", "@arena-hero/leaderboard-web", "deploy:gh-pages"])
        log(f"==> published: {ONLINE_URL}")
    else:
        log("==> validated locally; publication was not requested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
