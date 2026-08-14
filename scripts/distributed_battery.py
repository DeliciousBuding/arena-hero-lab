"""Distributed battery driver: deploy, launch, poll, collect, merge.

Runs ``leaderboard_battery.py`` across N remote machines (one seed-range per
host), copies the per-match result files back and re-aggregates one merged
leaderboard locally via the battery's own resume logic.

Hosts are intentionally NOT hard-coded here: the lab repo is public and internal
test-machine names must never leak into it.  Supply hosts through a local
gitignored JSON file (default ``~/.arena-hosts.json``) or the ``ARENA_HOSTS``
env var:

    [
      {"ssh": "my-alias", "seeds": [0, 1, 2, 3], "workers": 3,
       "python": "/root/arena/arena-hero-lab/.venv/bin/python",
       "script": "/root/arena/arena-hero-lab/scripts/leaderboard_battery.py",
       "out": "/root/arena-battery"},
      ...
    ]

``python``/``script``/``out`` are remote paths (out is wiped + recreated per
run).  Windows hosts add ``"posix": false`` and point ``python`` at their local
venv ``python.exe`` (launch is then skipped; start the battery via the host's
own scheduler and use ``status``/``collect``).

Usage:
    uv run python scripts/distributed_battery.py deploy --seeds 0 9
    uv run python scripts/distributed_battery.py status
    uv run python scripts/distributed_battery.py collect --out-dir /tmp/arena-lb-v5
    uv run python scripts/distributed_battery.py run --seeds 0 9 --out-dir ...   (all-in-one)

Deploy pushes the five battery-relevant files from this working tree; hosts must
already have the repo + venv laid out (one-time bootstrap, done manually).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import posixpath
import subprocess
import sys
import time
from pathlib import Path

_LAB_ROOT = Path(__file__).resolve().parents[1]
# Deploy every file that affects match semantics or battery orchestration.  The
# list is intentionally broad: a stale file on a remote node would silently run
# the old battery and produce results that don't merge with the local run.
_FILES_TO_DEPLOY = [
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/__init__.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/config.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/world.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/engine.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/game.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/orchestrator.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/observation.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/strategy.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/contestants.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/leaderboard.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/bench_report.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/stage_metrics.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/sdk_bridge.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/sdk_agent_shim.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/python_agent_shim.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/evolve_shim.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/public_contestants.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/sdk_adapters/__init__.py",
    "packages/arena-hero-sim/src/arena_hero_sim/ffa/sdk_adapters/massarmy.py",
    "packages/arena-hero-sim/src/arena_hero_sim/serialization.py",
    "scripts/leaderboard_battery.py",
]


def load_hosts() -> list[dict]:
    raw = os.environ.get("ARENA_HOSTS")
    if raw:
        return json.loads(raw)
    path = Path.home() / ".arena-hosts.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    raise SystemExit(
        "no hosts configured: set ARENA_HOSTS or create ~/.arena-hosts.json "
        "(see module docstring for the schema)"
    )


def _ssh(host: str, remote_cmd: str, *, quiet: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", host, remote_cmd],
        capture_output=True,
        text=True,
        check=False,
    )
    if not quiet and proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0 and proc.stderr.strip() and not quiet:
        print(proc.stderr.strip()[-400:], file=sys.stderr)
    return proc


def _scp(host: str, local: Path, remote: str) -> bool:
    proc = subprocess.run(
        ["scp", "-o", "ConnectTimeout=20", str(local), f"{host}:{remote}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def split_seeds(hosts: list[dict], seeds: list[int]) -> list[dict]:
    """Assign seed sub-ranges to hosts (hosts without ``seeds`` split the range)."""
    explicit = [h for h in hosts if h.get("seeds")]
    implicit = [h for h in hosts if not h.get("seeds")]
    assigned: list[dict] = []
    for host in explicit:
        host = dict(host)
        host["seeds"] = [int(s) for s in host["seeds"]]
        assigned.append(host)
    remaining = [s for s in seeds if all(s not in h["seeds"] for h in assigned)]
    if implicit and remaining:
        chunk = max(1, len(remaining) // len(implicit))
        for index, host in enumerate(implicit):
            host = dict(host)
            host["seeds"] = remaining[index * chunk : (index + 1) * chunk]
            assigned.append(host)
    return assigned


def _progress_cmd(host: dict) -> str:
    if host.get("posix", True):
        return (
            f"grep -oP '\\[progress\\] \\d+/\\d+ done.*ETA ~[0-9ms]+' {host['out']}.log | tail -1"
        )
    win_log = host["out"].replace("/", "\\\\") + ".log"
    return (
        'powershell -NoProfile -Command "(Select-String -Path '
        f"'{win_log}' -Pattern '\\[progress\\]' | Select-Object -Last 1).Line\""
    )


def _count_cmd(host: dict) -> str:
    if host.get("posix", True):
        return f"grep -c 'winner=' {host['out']}.log"
    win_log = host["out"].replace("/", "\\\\") + ".log"
    return (
        'powershell -NoProfile -Command "(Select-String -Path '
        f"'{win_log}' -Pattern 'winner=').Count\""
    )


def _shard_id(host: dict) -> str:
    """Stable shard id for a host (used in merge-integrity checks)."""
    return host.get("id") or host["ssh"].split("@")[-1].split(".")[0]


def _remote_sha256(host: dict, remote_path: str) -> str:
    """Read sha256 of a remote file (sha256sum on posix, Get-FileHash on win)."""
    if host.get("posix", True):
        proc = _ssh(host["ssh"], f"sha256sum {remote_path} 2>/dev/null | cut -d' ' -f1", quiet=True)
        return proc.stdout.strip()
    win_path = remote_path.replace("/", "\\\\")
    proc = _ssh(
        host["ssh"],
        f'powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 '
        f"'{win_path}').Hash.ToLower()\"",
        quiet=True,
    )
    return proc.stdout.strip()


def deploy(hosts: list[dict]) -> None:
    for host in hosts:
        ssh = host["ssh"]
        print(f"== deploy {ssh}")
        for rel in _FILES_TO_DEPLOY:
            local = _LAB_ROOT / rel
            remote = posixpath.normpath(f"{Path(host['script']).parent}/../{rel}")
            if not _scp(ssh, local, remote):
                raise SystemExit(f"scp {rel} -> {ssh} failed")
    print("deploy done")


def launch(hosts: list[dict], tier: str) -> None:
    for host in hosts:
        if not host.get("posix", True):
            print(f"== launch {host['ssh']} skipped (posix=false; use host scheduler)")
            continue
        ssh = host["ssh"]
        seeds = " ".join(str(s) for s in host["seeds"])
        workers = host.get("workers", 1)
        cmd = (
            f"rm -rf {host['out']} && "
            f"ARENA_BATTERY_WORKERS={workers} nohup {host['python']} {host['script']} "
            f"--tier {tier} --seeds {seeds} --out-dir {host['out']} "
            f"> {host['out']}.log 2>&1 & echo launched"
        )
        print(f"== launch {ssh} seeds=[{seeds}] workers={workers}")
        _ssh(ssh, cmd)


def status(hosts: list[dict]) -> None:
    for host in hosts:
        ssh = host["ssh"]
        print(f"== {ssh}")
        _ssh(ssh, f"{_progress_cmd(host)}; {_count_cmd(host)}")


def collect(hosts: list[dict], out_dir: Path, seeds: list[int]) -> None:
    results = out_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    for host in hosts:
        ssh = host["ssh"]
        print(f"== collect {ssh}")
        source = f"{host['out']}/results/."
        proc = subprocess.run(
            ["scp", "-o", "ConnectTimeout=20", "-r", f"{ssh}:{source}", str(results)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(f"  collect from {ssh} failed: {proc.stderr[-200:]}", file=sys.stderr)
    count = len(list(results.glob("*.json")))
    print(f"collected {count} result files into {results}")

    print("== merge + aggregate")
    battery = _LAB_ROOT / "scripts" / "leaderboard_battery.py"
    subprocess.run(
        [
            sys.executable,
            str(battery),
            "--tier",
            "full",
            "--seeds",
            *map(str, seeds),
            "--out-dir",
            str(out_dir),
        ],
        check=False,
    )
    print(f"merged leaderboard in {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["deploy", "launch", "status", "collect", "run"])
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(10)))
    ap.add_argument("--tier", default="full")
    ap.add_argument("--out-dir", type=Path, default=Path("artifacts/leaderboard"))
    args = ap.parse_args()

    hosts = load_hosts()
    assigned = split_seeds(hosts, args.seeds)
    if args.command in ("deploy", "run"):
        deploy(assigned)
    if args.command in ("launch", "run"):
        launch(assigned, args.tier)
    if args.command == "status":
        status(assigned)
    if args.command in ("collect", "run"):
        if args.command == "run":
            print("polling until all hosts finish (Ctrl-C to stop polling)...")
            # v3 battery has 7 scenarios; expected = len(seeds) * 7.
            expected = len(args.seeds) * 7
            while True:
                done = 0
                for host in assigned:
                    proc = _ssh(host["ssh"], _count_cmd(host), quiet=True)
                    with contextlib.suppress(ValueError):
                        done += int(proc.stdout.strip() or 0)
                print(f"[{time.strftime('%H:%M:%S')}] {done}/{expected} matches done")
                if done >= expected:
                    break
                time.sleep(120)
        collect(assigned, args.out_dir, args.seeds)


if __name__ == "__main__":
    main()
