"""Full public leaderboard battery: 7 scenarios x N seeds, JSON + manifest.

Runs only the public third-party roster (evolve / drew-z / guide / waaiging /
tactic / wuwd / massarmy + rand / wait).  Our own python and hunter contestants
never enter the public table.  Every match is content-addressed and the run
manifest pins each third-party repo HEAD, the official SDK version and the
evolve genes sha so an L-station reader can reproduce the exact ranking.

The public battery spans two regimes (the sim package's
``validate_scenario_battery`` rejects duplicate presets at import time):
four 256/2000-tick lab-regime scenarios (fast anchor + distinct stress axes)
and three 512/5000-tick production-regime scenarios (large map + long horizon +
sparse depleting resources, per docs/design/production-world-model-v1.md).  The
ultra-long (10000-tick), royale (8-player 512) and barren-research scenarios are
opt-in via ``--long`` / ``--royale`` / ``--barren``; the match ceiling is
adaptive (max(900, ticks*1.5)) so long-horizon matches are not falsely killed.

Outputs: ``results/<scenario>__<seed>.json`` (merge unit, one per match),
``checkpoint.json`` (resumable state), ``leaderboard.json`` (public table),
``bench.json`` (website payload: total + sub-leaderboards + per-match data) and
``manifest.json`` (pinned inputs).  No HTML: the leaderboard-web site consumes
``bench.json`` directly.

Parallelism: matches run in ``--workers`` concurrent worker *processes*.  Each
worker runs one (scenario, seed) match (evolve in-process + 6 SDK subprocesses),
writes a self-contained JSON result to ``results/<scenario>__<seed>.json`` and
reports tick progress over a shared counter so the parent can tell a true
deadlock from a slow-but-progressing match.  Those result files are the merge
unit for distributed runs: copy this repo + a work slice to N machines, run the
same command on each slice, copy the ``results/`` files back and re-aggregate.

Usage:
    uv run python scripts/leaderboard_battery.py --seeds 0 1 2
    uv run python scripts/leaderboard_battery.py --seeds 0 1 2 --workers 6
    uv run python scripts/leaderboard_battery.py --seeds 0 1 2 3 4 5 6 7 8 9 --out-dir C:/Users/Ding/tmp/arena-lb
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import subprocess
import sys
import time
import traceback
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from arena_hero_sim.ffa.bench_report import build_bench_payload, extract_match_obs
from arena_hero_sim.ffa.leaderboard import (
    BARREN_RESPAWN_SCENARIO,
    LONG_SCENARIOS,
    ROYALE_SCENARIOS,
    SCENARIOS,
    aggregate_leaderboard,
    bootstrap_leaderboard,
    rank_metrics,
    terminal_metrics,
)
from arena_hero_sim.ffa.orchestrator import GENERATOR_VERSION, run_ffa
from arena_hero_sim.ffa.public_contestants import (
    PUBLIC_ROSTER,
    build_public_leaderboard_contestants,
)
from arena_hero_sim.ffa.stage_metrics import aggregate_stages, extract_match_stages

# Two-tier per-match wall-clock bound (progress-aware):
# - STALL: no tick progress for this long => a true deadlock (e.g. an in-process
#   evolve heuristic spinning).  Kill fast instead of burning the old 360s.
# - CEILING: a slow-but-progressing match is allowed up to this absolute bound.
#   This fixes the "waaiging late-game degradation" case where a 2000-tick match
#   legitimately needs >360s (it was being killed at 98% complete).
MATCH_STALL_SECONDS = 90.0
MATCH_CEILING_SECONDS = 900.0
# A match where an agent died mid-match is retried this many times (fresh
# subprocesses each attempt) before the result is recorded as crashed.
_MATCH_ATTEMPTS = 3

LEADERBOARD_SCHEMA = "arena.leaderboard.public.v1"
MANIFEST_SCHEMA = "arena.leaderboard.manifest.v1"

_ARENA_ROOT = Path(__file__).resolve().parents[2]
_EVOLVE_GENES = (
    _ARENA_ROOT / "reference" / "third-party" / "arena-evolve" / "genes" / "evolve_v7_best.json"
)
_REFERENCE_REPOS = {
    "evolve": "reference/third-party/arena-evolve",
    "drew-z": "reference/third-party/arena-hero-agent",
    "guide": "reference/third-party/arena-hero-guide",
    "waaiging": "reference/third-party/arena-hero-clone-waaiging",
    "tactic": "reference/third-party/arena-hero-tactic",
    "wuwd": "reference/third-party/arena-hero-agent-wuwd",
    "massarmy": "reference/third-party/arena-hero-agent-massarmy",
}
_SDK_REPO = "arena-hero-sdk-py"


def _git_head(rel: str) -> str:
    repo = _ARENA_ROOT / rel
    if not (repo / ".git").exists():
        return "not-a-git-repo"
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else "unavailable"


def _git_dirty(rel: str) -> bool:
    """True when the repo working tree has uncommitted changes.

    ``git rev-parse HEAD`` alone is insufficient to reproduce a run launched
    from a dirty tree (the v3 production scenarios were first run before they
    were committed), so the manifest records this flag plus a content-address
    of the actual source files (see ``_lab_source_sha256``).
    """
    repo = _ARENA_ROOT / rel
    if not (repo / ".git").exists():
        return False
    proc = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _lab_source_sha256() -> str:
    """Content-address the sim-relevant lab source files (path + bytes).

    Covers the files that actually determine match outcomes — the sim package
    and the battery scripts — so a reader can reproduce a run even when it was
    launched from an uncommitted working tree.  Deterministic: same files ->
    same digest.
    """
    repo = _ARENA_ROOT / "arena-hero-lab"
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "packages/arena-hero-sim/src", "scripts"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return "unavailable"
    digests = {}
    for rel in proc.stdout.splitlines():
        path = repo / rel
        if path.is_file():
            digests[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(
        json.dumps(digests, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _sdk_python() -> str:
    for candidate in (
        _ARENA_ROOT / _SDK_REPO / ".venv" / "Scripts" / "python.exe",
        _ARENA_ROOT / _SDK_REPO / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _sdk_version() -> str:
    proc = subprocess.run(
        [
            _sdk_python(),
            "-c",
            "import importlib.metadata as m; print(m.version('arena-hero'))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return "unavailable"
    return proc.stdout.strip()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict:
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "sim_generator_version": GENERATOR_VERSION,
        "contestants": {
            cid: {
                "repo": _REFERENCE_REPOS.get(cid),
                "git_head": _git_head(_REFERENCE_REPOS[cid]) if cid in _REFERENCE_REPOS else None,
                "kind": "third-party" if cid in _REFERENCE_REPOS else "control",
            }
            for cid in PUBLIC_ROSTER
        },
        "evolve_genes_sha256": _file_sha256(_EVOLVE_GENES) if _EVOLVE_GENES.is_file() else None,
        "sdk": {
            "repo": _SDK_REPO,
            "git_head": _git_head(_SDK_REPO),
            "version": _sdk_version(),
        },
        "lab_git_head": _git_head("arena-hero-lab"),
        "lab_working_tree_dirty": _git_dirty("arena-hero-lab"),
        "lab_source_sha256": _lab_source_sha256(),
    }


def _checkpoint_path(out_dir: Path | None) -> Path | None:
    return out_dir / "checkpoint.json" if out_dir is not None else None


def _load_checkpoint(path: Path | None) -> dict:
    empty = {
        "matches": {},
        "scenario_infos": [],
        "seeds": [],
        "roster": list(PUBLIC_ROSTER),
    }
    if path is None or not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    return {**empty, **data}


def _save_checkpoint(path: Path | None, state: dict) -> None:
    if path is None:
        return
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _match_key(scenario_id: str, seed: int) -> str:
    return f"{scenario_id}:{seed}"


def _result_path(results_dir: Path, scenario_id: str, seed: int) -> Path:
    return results_dir / f"{scenario_id}__{seed}.json"


def _worker_match(
    scenario_id: str,
    params: dict,
    seed: int,
    ticks: int,
    tick_progress,
    result_path: Path,
) -> None:
    """Run one (scenario, seed) match in a worker process.

    Writes a self-contained JSON result to ``result_path`` (atomic) and updates
    ``tick_progress`` (a multiprocessing shared int) after every tick so the
    parent can distinguish "slow but progressing" from a true deadlock.  Worker
    console output is redirected to a per-match ``.log`` file; the parent prints
    one clean summary line per match.

    If any contestant's strategy failed during the match (e.g. a SDK subprocess
    died under CPU contention — observed as guide dying at tick 4 while 7
    matches spawned their 42 subprocesses at once), the whole match is retried
    with freshly built contestants.  A dead agent changes the trajectory, so a
    match with errors is not a clean result; same-machine reruns are otherwise
    byte-identical, so the retry converges to the correct sha.
    """
    log_handle = open(  # noqa: SIM115 -- worker redirects its stdio for the process lifetime
        result_path.with_suffix(".log"), "w", encoding="utf-8", errors="replace"
    )
    sys.stdout = log_handle
    sys.stderr = log_handle

    result: dict[str, object] = {"scenario": scenario_id, "seed": seed, "status": "ok"}
    report = None
    decision_stats: list = []
    strategies: list = []
    last_dead: list[str] = []
    for attempt in range(1, _MATCH_ATTEMPTS + 1):
        tick_progress.value = 0  # reset so the parent's stall guard sees fresh progress
        try:
            contestants, sdk_strategies = build_public_leaderboard_contestants()
            strategies = list(sdk_strategies)

            def _on_progress(tick: int) -> None:
                tick_progress.value = tick

            report = run_ffa(
                contestants,
                seed=seed,
                ticks=ticks,
                size=int(params["size"]),
                obstacle_density=float(params["obstacle_density"]),
                spawn_center=tuple(params["spawn_center"]),
                resource_scale=float(params["resource_scale"]),
                resource_replenish_every=int(params["resource_replenish_every"]),
                respawn_style=str(params["respawn_style"]),
                progress_callback=_on_progress,
            )
            dead = [entry.contestant_id for entry in report.terminal if entry.strategy_errors > 0]
            if dead:
                last_dead = dead
                print(
                    f"  RETRY [{scenario_id}] seed={seed} attempt={attempt}: "
                    f"agent(s) died mid-match: {', '.join(dead)}",
                    flush=True,
                )
                report = None
            else:
                # Capture stats before close() (close prints but keeps counters).
                decision_stats = [s.decision_stats() for s in strategies]
        except Exception as exc:
            result["status"] = "crashed"
            result["error"] = repr(exc)
            result["traceback"] = traceback.format_exc()
            report = None
        finally:
            for strategy in strategies:
                with suppress(Exception):
                    strategy.close()
            strategies = []
        if report is not None:
            break

    if report is None:
        if result.get("status") != "crashed":
            result["status"] = "crashed"
            result["error"] = (
                f"agents kept dying mid-match after {_MATCH_ATTEMPTS} attempts: "
                f"{', '.join(last_dead) or 'unknown'}"
            )
    else:
        tick_progress.value = 0
        try:
            metrics: dict[str, dict[str, float | str]] = {
                t.contestant_id: {**terminal_metrics(t), "contestant": t.contestant_id}
                for t in report.terminal
            }
            ranked = rank_metrics([metrics[cid] for cid in PUBLIC_ROSTER])
            terminal_rows = [
                {
                    "contestant": t.contestant_id,
                    "rank": next(m["rank"] for m in ranked if m["contestant"] == t.contestant_id),
                    "alive": t.survival_alive,
                    "core_hp": t.core_hp,
                    "final_resources": t.final_resources,
                    "population_final": t.population_final,
                    "respawn_count": t.respawn_count,
                    "stats": dict(sorted(t.stats.items())),
                    "strategy_errors": t.strategy_errors,
                    "strategy_last_error": t.strategy_last_error,
                }
                for t in report.terminal
            ]
            result["ranked"] = ranked
            result["terminal_rows"] = terminal_rows
            result["stage"] = extract_match_stages(report)
            result["match_obs"] = extract_match_obs(
                report, ranked, PUBLIC_ROSTER, seed, scenario_id, ticks
            )
            result["artifact_sha256"] = report.artifact_sha256
            result["decision_stats"] = decision_stats
        except Exception as exc:
            result["status"] = "crashed"
            result["error"] = repr(exc)
            result["traceback"] = traceback.format_exc()
    for strategy in strategies:
        with suppress(Exception):
            strategy.close()
    log_handle.flush()
    log_handle.close()

    tmp = result_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    tmp.replace(result_path)


def _default_workers() -> int:
    cpu = os.cpu_count() or 4
    # game.step() fans out each contestant's decide() on a persistent per-match
    # thread pool and the SDK subprocesses sit idle between ticks, so a match
    # keeps ~1.5-2.5 cores busy at a time (measured on the 4-way distributed
    # runs: cpu//3 left small hosts underutilised while 2 workers on a 2-core
    # VPS kept it saturated).  One worker per core is the fastest wall-clock
    # profile; the cap keeps big boxes responsive.  ARENA_BATTERY_WORKERS
    # overrides for per-host tuning.
    env = os.environ.get("ARENA_BATTERY_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(2, min(8, cpu))


def _parse_slice(spec: str) -> tuple[int, int]:
    """Parse a ``k/N`` shard spec for distributed multi-machine runs."""
    try:
        k, n = (int(part) for part in spec.split("/"))
    except (ValueError, AttributeError):
        raise SystemExit(f"invalid --slice {spec!r}; expected k/N (e.g. 0/4)") from None
    if n < 1 or not (0 <= k < n):
        raise SystemExit(f"invalid --slice {spec!r}; need 0 <= k < N")
    return k, n


def _collect_result(info: dict, results_dir: Path, matches: dict) -> None:
    """Read a finished worker's result file and record it (prints one line)."""
    scenario_id = info["scenario_id"]
    seed = info["seed"]
    key = _match_key(scenario_id, seed)
    result_path = _result_path(results_dir, scenario_id, seed)
    elapsed = round(time.monotonic() - info["started"], 1)
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result = {
                "scenario": scenario_id,
                "seed": seed,
                "status": "crashed",
                "error": f"unreadable result file: {exc}",
            }
    else:
        result = {
            "scenario": scenario_id,
            "seed": seed,
            "status": "crashed",
            "error": "worker exited without writing a result",
        }
    matches[key] = result
    result["elapsed_s"] = elapsed

    status = result.get("status")
    if status == "ok":
        ranked_entries = [m for m in (result.get("ranked") or []) if isinstance(m, dict)]
        winner = next(
            (str(m.get("contestant")) for m in ranked_entries if m.get("rank") == 1.0),
            "?",
        )
        print(
            f"[{scenario_id}] seed={seed} sha={result.get('artifact_sha256', '')[:12]} "
            f"winner={winner} ({elapsed}s)",
            flush=True,
        )
        for ds in result.get("decision_stats") or []:
            if not isinstance(ds, dict) or not ds.get("count"):
                continue
            print(
                f"  DECISION {ds['agent']}: count={ds['count']} "
                f"avg={ds.get('avg_s')}s max={ds.get('max_s')}s slow={ds.get('slow_count')}",
                flush=True,
            )
    elif status == "hung":
        print(f"[{scenario_id}] seed={seed} HUNG ({elapsed}s)", flush=True)
    else:
        print(f"ERROR [{scenario_id}] seed={seed} crashed: {result.get('error')}", flush=True)


def _run_parallel(
    pending: list[tuple[str, dict, int, int]],
    *,
    workers: int,
    results_dir: Path,
    matches: dict,
    on_done,
    ceiling_seconds: float = MATCH_CEILING_SECONDS,
) -> None:
    """Dispatch pending (scenario_id, params, seed, ticks) to worker processes.

    Enforces a hard concurrency cap (auto rate-limiting / backpressure): at most
    ``workers`` matches run at once; the rest wait.  Terminates a worker as soon
    as it stalls (no tick progress) or exceeds the absolute ceiling
    (``ceiling_seconds``, raised for the opt-in 4000-tick scenarios).
    """
    if not pending:
        return
    total = len(pending)
    running: dict[int, dict] = {}
    done = 0
    start = time.monotonic()
    last_report = start
    print(f"[parallel] {total} matches, workers={workers}", flush=True)
    while pending or running:
        # Backpressure: fill the pool up to the cap, no more.
        while len(running) < workers and pending:
            scenario_id, params, seed, ticks = pending.pop(0)
            tick_progress = mp.Value("i", 0)
            proc = mp.Process(
                target=_worker_match,
                args=(
                    scenario_id,
                    params,
                    seed,
                    ticks,
                    tick_progress,
                    _result_path(results_dir, scenario_id, seed),
                ),
                daemon=True,
            )
            proc.start()
            now = time.monotonic()
            pid = proc.pid
            assert pid is not None  # set synchronously by Process.start()
            running[pid] = {
                "proc": proc,
                "scenario_id": scenario_id,
                "seed": seed,
                "tick_progress": tick_progress,
                "started": now,
                "last_tick": -1,
                "last_seen": now,
            }

        for pid in list(running):
            info = running[pid]
            proc = info["proc"]
            proc.join(timeout=0)
            if not proc.is_alive():
                _collect_result(info, results_dir, matches)
                done += 1
                on_done()
                del running[pid]
                continue

            now = time.monotonic()
            tick = info["tick_progress"].value
            if tick != info["last_tick"]:
                info["last_tick"] = tick
                info["last_seen"] = now
            stalled = now - info["last_seen"]
            elapsed = now - info["started"]

            if stalled > MATCH_STALL_SECONDS:
                proc.terminate()
                proc.join(timeout=5)
                key = _match_key(info["scenario_id"], info["seed"])
                matches[key] = {
                    "scenario": info["scenario_id"],
                    "seed": info["seed"],
                    "status": "hung",
                    "reason": "stall",
                    "last_tick": info["last_tick"],
                    "elapsed_s": round(elapsed, 1),
                }
                print(
                    f"ERROR [{info['scenario_id']}] seed={info['seed']} STALLED "
                    f"at tick={info['last_tick']} ({stalled:.0f}s no progress), terminating",
                    flush=True,
                )
                done += 1
                on_done()
                del running[pid]
            elif elapsed > ceiling_seconds:
                proc.terminate()
                proc.join(timeout=5)
                key = _match_key(info["scenario_id"], info["seed"])
                matches[key] = {
                    "scenario": info["scenario_id"],
                    "seed": info["seed"],
                    "status": "hung",
                    "reason": "ceiling",
                    "last_tick": info["last_tick"],
                    "elapsed_s": round(elapsed, 1),
                }
                print(
                    f"ERROR [{info['scenario_id']}] seed={info['seed']} hit ceiling "
                    f"at tick={info['last_tick']} ({elapsed:.0f}s), terminating",
                    flush=True,
                )
                done += 1
                on_done()
                del running[pid]

        now = time.monotonic()
        if now - last_report >= 20 and done > 0:
            last_report = now
            avg = (now - start) / done
            eta = (total - done) * avg / workers
            print(
                f"[progress] {done}/{total} done, {len(running)} running, "
                f"avg={avg:.0f}s/match, ETA ~{int(eta) // 60}m{int(eta) % 60:02d}s",
                flush=True,
            )
        time.sleep(0.2)


def run_battery(
    seeds: list[int],
    *,
    smoke: bool = False,
    max_ticks: int | None = None,
    scenarios: tuple = SCENARIOS,
    out_dir: Path | None = None,
    workers: int | None = None,
    slice_spec: tuple[int, int] | None = None,
    plan: bool = False,
    plan_seed_count: int = 1,
    retry_failed: bool = False,
    ceiling_seconds: float = MATCH_CEILING_SECONDS,
    bootstrap_iterations: int = 1000,
    no_bench: bool = False,
) -> dict:
    out_dir = out_dir or Path("artifacts/leaderboard")
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = _checkpoint_path(out_dir)
    results_dir = out_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Precompute per-scenario params + ticks (static, independent of run order).
    scenario_specs: list[tuple] = []
    for scenario in scenarios:
        ticks = 40 if smoke else scenario.ticks
        if max_ticks is not None:
            ticks = min(ticks, max_ticks)
        params = {
            "size": scenario.size,
            "obstacle_density": scenario.obstacle_density,
            "resource_scale": scenario.resource_scale,
            "spawn_center": list(scenario.spawn_center),
            "resource_replenish_every": scenario.resource_replenish_every,
            "respawn_style": scenario.respawn_style,
            "ticks": ticks,
        }
        scenario_specs.append((scenario, ticks, params))

    scenario_infos: list[dict[str, object]] = [
        {
            "id": scenario.id,
            "params": params,
            "template": {
                "configNote": scenario.name,
                "radius": scenario.size // 2,
                "randomDrop": False,
                "resources": str(scenario.resource_scale),
            },
        }
        for scenario, _ticks, params in scenario_specs
    ]

    # Load checkpoint (flat matches map) and reconstruct the pending work.
    state = _load_checkpoint(checkpoint_path)
    matches: dict[str, dict] = state["matches"]

    def _save() -> None:
        _save_checkpoint(
            checkpoint_path,
            {
                "matches": matches,
                "scenario_infos": scenario_infos,
                "seeds": list(seeds),
                "roster": list(PUBLIC_ROSTER),
            },
        )

    pending: list[tuple[str, dict, int, int]] = []
    flat_index = 0
    for scenario, ticks, params in scenario_specs:
        for seed in seeds:
            in_slice = slice_spec is None or flat_index % slice_spec[1] == slice_spec[0]
            flat_index += 1
            if not in_slice:
                continue
            key = _match_key(scenario.id, seed)
            if key in matches and not (retry_failed and matches[key].get("status") != "ok"):
                continue
            matches.pop(key, None)
            # Resume from an orphan result file (crash between worker write and
            # the parent's checkpoint save): trust the on-disk result — unless
            # this is a failed match being retried, in which case drop the file.
            result_file = _result_path(results_dir, scenario.id, seed)
            if result_file.exists():
                if retry_failed:
                    with suppress(OSError):
                        result_file.unlink()
                else:
                    try:
                        matches[key] = json.loads(result_file.read_text(encoding="utf-8"))
                        _save()
                        continue
                    except (OSError, json.JSONDecodeError):
                        pass
            pending.append((scenario.id, params, seed, ticks))

    effective_workers = workers if workers is not None else _default_workers()
    _run_parallel(
        pending,
        workers=effective_workers,
        results_dir=results_dir,
        matches=matches,
        on_done=_save,
        ceiling_seconds=ceiling_seconds,
    )

    # Completion summary (clean/accurate final tally + per-scenario timing).
    ok = hung = crashed = 0
    per_scenario_times: dict[str, list[float]] = {}
    for m in matches.values():
        status = m.get("status")
        if status == "ok":
            ok += 1
        elif status == "hung":
            hung += 1
        else:
            crashed += 1
        if status == "ok" and m.get("elapsed_s") is not None:
            per_scenario_times.setdefault(m["scenario"], []).append(m["elapsed_s"])
    print("\n=== completion summary ===", flush=True)
    print(f"  ok={ok} hung={hung} crashed={crashed}  (total {len(matches)})", flush=True)
    for sid in sorted(per_scenario_times):
        ts = per_scenario_times[sid]
        print(
            f"  {sid:16s} avg={sum(ts) / len(ts):5.0f}s max={max(ts):5.0f}s n={len(ts)}",
            flush=True,
        )
    hung_keys = sorted(k for k, m in matches.items() if m.get("status") == "hung")
    if hung_keys:
        print(f"  timed-out: {', '.join(hung_keys)}", flush=True)

    if plan and per_scenario_times:
        sampled_total = sum(sum(ts) / len(ts) for ts in per_scenario_times.values() if ts)
        est = sampled_total * plan_seed_count / max(effective_workers, 1)
        print(
            f"[plan] sampled 1 seed/scenario (sum={sampled_total:.0f}s); "
            f"est full {plan_seed_count}-seed run @ {effective_workers} workers "
            f"= ~{int(est) // 60}m{int(est) % 60:02d}s",
            flush=True,
        )

    # Rebuild aggregation inputs from the flat matches map (deterministic order).
    records: list[dict] = []
    match_groups: list[list[dict]] = []
    stage_records: list[dict] = []
    match_records: list[dict] = []
    per_scenario: list[dict] = []
    for scenario, _ticks, params in scenario_specs:
        seed_rows = []
        for seed in seeds:
            m = matches.get(_match_key(scenario.id, seed))
            if m is None:
                continue
            if m["status"] == "ok":
                records.extend(m["ranked"])
                match_groups.append(list(m["ranked"]))
                stage_records.append(m["stage"])
                match_records.append(m["match_obs"])
                seed_rows.append(
                    {
                        "seed": seed,
                        "artifact_sha256": m.get("artifact_sha256"),
                        "terminal": m.get("terminal_rows", []),
                    }
                )
            elif m["status"] == "hung":
                seed_rows.append({"seed": seed, "hung": True, "terminal": []})
            else:
                seed_rows.append({"seed": seed, "crashed": True, "error": m.get("error")})
        per_scenario.append(
            {
                "id": scenario.id,
                "name": scenario.name,
                "params": params,
                "seeds": seed_rows,
            }
        )

    rows = aggregate_leaderboard(records, PUBLIC_ROSTER)
    bootstrap = bootstrap_leaderboard(match_groups, PUBLIC_ROSTER, iterations=bootstrap_iterations)
    subboards = aggregate_stages(stage_records, PUBLIC_ROSTER)
    generated_at = datetime.now(UTC).isoformat()
    # Mega-runs (hundreds of matches) skip the website payload: the per-tick
    # samples would balloon bench.json into tens of MB and bloat the static
    # site bundle.  The canonical 10-seed run still produces the full bench.
    bench_payload = None
    if not no_bench:
        bench_payload = build_bench_payload(
            rows=rows,
            subboards=subboards,
            match_records=match_records,
            scenario_ids=[str(s["id"]) for s in scenario_infos],
            scenario_infos=scenario_infos,
            roster=list(PUBLIC_ROSTER),
            seeds=list(seeds),
            ticks=max((ticks for _, ticks, _ in scenario_specs), default=2000),
            generated_at=generated_at,
            source_label="arena-hero-lab scripts/leaderboard_battery.py",
        )
        # Bootstrap confidence bands ride along on both artifacts; the website
        # renders composite CIs without recomputing anything.
        bench_payload["bootstrap"] = bootstrap
    return {
        "schema": LEADERBOARD_SCHEMA,
        "roster": list(PUBLIC_ROSTER),
        "scenarios": per_scenario,
        "leaderboard": [row.to_json() for row in rows],
        "subLeaderboards": subboards,
        "bootstrap": bootstrap,
        "bench": bench_payload,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--seeds", type=int, nargs="+", default=None, help="explicit seed list (overrides --tier)"
    )
    ap.add_argument(
        "--tier",
        choices=["smoke", "small", "full"],
        default=None,
        help="preset: smoke (40 ticks, 1 seed), small (3 seeds), full (10 seeds)",
    )
    ap.add_argument("--out-dir", type=Path, default=Path("artifacts/leaderboard"))
    ap.add_argument("--smoke", action="store_true", help="cap ticks at 40 for a fast sanity run")
    ap.add_argument(
        "--max-ticks",
        type=int,
        default=None,
        help="cap every scenario tick count (fast first-publish mode)",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"concurrent match workers (default: auto = {_default_workers()})",
    )
    ap.add_argument(
        "--slice",
        type=str,
        default=None,
        help="k/N: run only the k-th of N shards (distributed multi-machine)",
    )
    ap.add_argument(
        "--plan",
        action="store_true",
        help="sample 1 seed per scenario then print a full-run time estimate",
    )
    ap.add_argument(
        "--barren",
        action="store_true",
        help="run only the opt-in barren far-respawn research scenario",
    )
    ap.add_argument(
        "--royale",
        action="store_true",
        help="run only the opt-in large battle-royale scenarios (512 maps)",
    )
    ap.add_argument(
        "--long",
        action="store_true",
        help="run only the opt-in 4000-tick long-horizon scenario",
    )
    ap.add_argument(
        "--retry-failed",
        action="store_true",
        help="re-run matches whose recorded status is not ok (drop their old results)",
    )
    ap.add_argument(
        "--no-bench",
        action="store_true",
        help="skip the website bench payload (mega-runs: keeps bench.json out of a large out-dir)",
    )
    args = ap.parse_args()

    # Resolve the seed list: explicit --seeds wins, else the tier preset, else
    # the small (3-seed) default.
    if args.seeds is not None:
        seeds = args.seeds
    elif args.tier == "smoke":
        seeds = [0]
        args.smoke = True
    elif args.tier == "full":
        seeds = list(range(10))
    else:
        seeds = [0, 1, 2]

    slice_spec = _parse_slice(args.slice) if args.slice is not None else None

    args.out_dir.mkdir(parents=True, exist_ok=True)
    # Capture the manifest *before* the run: git heads / SDK version / genes sha
    # are the inputs that produced the following artifact SHAs.  Recording them
    # after the run could pin a later HEAD (e.g. a commit made while the battery
    # was still running) and break byte-for-byte reproducibility.
    manifest = build_manifest()
    if args.barren:
        scenarios = (BARREN_RESPAWN_SCENARIO,)
    elif args.royale:
        scenarios = ROYALE_SCENARIOS
    elif args.long:
        scenarios = LONG_SCENARIOS
    else:
        scenarios = SCENARIOS
    # Adaptive match ceiling: long-horizon matches legitimately need more wall
    # clock than the 900s default (a 5000-tick production match runs ~3000s, a
    # 10000-tick ultra-long ~6000s).  The 90s stall guard still catches true
    # deadlocks fast regardless of this absolute cap.
    max_preset_ticks = max(s.ticks for s in scenarios)
    ceiling_seconds = max(MATCH_CEILING_SECONDS, max_preset_ticks * 1.5)

    run_seeds = seeds[:1] if args.plan else seeds
    payload = run_battery(
        run_seeds,
        smoke=args.smoke,
        max_ticks=args.max_ticks,
        scenarios=scenarios,
        out_dir=args.out_dir,
        workers=args.workers,
        slice_spec=slice_spec,
        plan=args.plan,
        plan_seed_count=len(seeds),
        retry_failed=args.retry_failed,
        ceiling_seconds=ceiling_seconds,
        no_bench=args.no_bench,
    )

    leaderboard_path = args.out_dir / "leaderboard.json"
    manifest_path = args.out_dir / "manifest.json"
    bench_path = args.out_dir / "bench.json"

    leaderboard_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    if payload["bench"] is not None:
        bench_path.write_text(
            json.dumps(payload["bench"], ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    print("\n=== sub-leaderboards ===")
    for board_id, board_rows in payload["subLeaderboards"].items():
        top = ", ".join(f"{r['contestant']}:{r['score']:.3f}" for r in board_rows[:3])
        print(f"  {board_id}: {top}")

    print("\n=== leaderboard ===")
    for row in payload["leaderboard"]:
        print(
            f"  #{row['composite'] and ''}{row['contestant']:8s} wins={row['wins']:2d} "
            f"mean_rank={row['mean_rank']:.2f} composite={row['composite']:.3f}"
        )
    print(f"\nwrote {leaderboard_path}")
    print(f"wrote {manifest_path}")
    if payload["bench"] is not None:
        print(f"wrote {bench_path}")


if __name__ == "__main__":
    main()
