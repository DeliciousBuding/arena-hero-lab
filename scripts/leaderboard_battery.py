"""Full public leaderboard battery: 7 scenarios x N seeds, JSON + manifest + HTML.

Runs only the public third-party roster (evolve / drew-z / guide / waaiging /
tactic + rand / wait).  Our own python and hunter contestants never enter the
public table.  Every match is content-addressed and the run manifest pins each
third-party repo HEAD, the official SDK version and the evolve genes sha so an
L-station reader can reproduce the exact ranking.

Usage:
    uv run python scripts/leaderboard_battery.py --seeds 0 1 2
    uv run python scripts/leaderboard_battery.py --seeds 0 1 2 3 4 5 6 7 8 9 --out-dir C:/Users/Ding/tmp/arena-lb
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from arena_hero_sim.ffa.leaderboard import (
    SCENARIOS,
    aggregate_leaderboard,
    rank_metrics,
    terminal_metrics,
)
from arena_hero_sim.ffa.orchestrator import GENERATOR_VERSION, run_ffa
from arena_hero_sim.ffa.public_contestants import (
    PUBLIC_ROSTER,
    build_public_leaderboard_contestants,
)

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
    }


def run_battery(seeds: list[int], *, smoke: bool = False) -> dict:
    per_scenario: list[dict] = []
    records: list[dict] = []

    for scenario in SCENARIOS:
        per_seed: list[dict] = []
        for seed in seeds:
            # Fresh contestants per match: every agent starts with clean
            # cross-tick memory (evolve heuristic maps, SDK agent memories)
            # so one seed never leaks state into the next.  This is what makes
            # the leaderboard a fair, reproducible evaluation instead of a
            # warm-start.
            contestants, sdk_strategies = build_public_leaderboard_contestants()
            try:
                ticks = 40 if smoke else scenario.ticks
                report = run_ffa(
                    contestants,
                    seed=seed,
                    ticks=ticks,
                    size=scenario.size,
                    obstacle_density=scenario.obstacle_density,
                    spawn_center=scenario.spawn_center,
                    resource_scale=scenario.resource_scale,
                )
            finally:
                for strategy in sdk_strategies:
                    strategy.close()

            metrics = {t.contestant_id: terminal_metrics(t) for t in report.terminal}
            for cid, m in metrics.items():
                m["contestant"] = cid
            ranked = rank_metrics([metrics[cid] for cid in PUBLIC_ROSTER])
            for m in ranked:
                records.append(dict(m))
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
                }
                for t in report.terminal
            ]
            per_seed.append(
                {
                    "seed": seed,
                    "artifact_sha256": report.artifact_sha256,
                    "terminal": terminal_rows,
                }
            )
            print(
                f"[{scenario.id}] seed={seed} sha={report.artifact_sha256[:12]} "
                f"winner={next(m['contestant'] for m in ranked if m['rank'] == 1.0)}"
            )
        per_scenario.append(
            {
                "id": scenario.id,
                "name": scenario.name,
                "params": {
                    "size": scenario.size,
                    "obstacle_density": scenario.obstacle_density,
                    "resource_scale": scenario.resource_scale,
                    "spawn_center": list(scenario.spawn_center),
                    "ticks": scenario.ticks,
                },
                "seeds": per_seed,
            }
        )

    rows = aggregate_leaderboard(records, PUBLIC_ROSTER)
    return {
        "schema": LEADERBOARD_SCHEMA,
        "roster": list(PUBLIC_ROSTER),
        "scenarios": per_scenario,
        "leaderboard": [row.to_json() for row in rows],
    }


def render_html(payload: dict, manifest: dict) -> str:
    rows = payload["leaderboard"]
    medal = {0: "🥇", 1: "🥈", 2: "🥉"}

    def esc(value: object) -> str:
        return html.escape(str(value))

    body_rows = []
    for index, row in enumerate(rows):
        body_rows.append(
            "<tr>"
            f"<td>{medal.get(index, index + 1)}</td>"
            f"<td><b>{esc(row['contestant'])}</b></td>"
            f"<td>{row['wins']}</td>"
            f"<td>{row['matches']}</td>"
            f"<td>{row['mean_rank']}</td>"
            f"<td>{row['survival_rate']}</td>"
            f"<td>{row['total_kills']}</td>"
            f"<td>{row['total_deposited']}</td>"
            f"<td>{row['total_resources']}</td>"
            f"<td>{row['composite']}</td>"
            "</tr>"
        )
    scenarios = "".join(
        f"<li><b>{esc(s['id'])}</b> — {esc(s['name'])} ({esc(s['params']['ticks'])} ticks, "
        f"size {esc(s['params']['size'])}, density {esc(s['params']['obstacle_density'])}, "
        f"resource x{esc(s['params']['resource_scale'])})</li>"
        for s in payload["scenarios"]
    )
    contestants = "".join(
        f"<li><b>{esc(cid)}</b> "
        f"{'— ' + esc(manifest['contestants'][cid]['repo']) if cid in manifest['contestants'] and manifest['contestants'][cid]['repo'] else ''}"
        f" @ <code>{esc(manifest['contestants'][cid]['git_head'])}</code></li>"
        for cid in payload["roster"]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arena Hero — Public Third-Party Agent Leaderboard</title>
<style>
body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; color: #1a1a1a; }}
h1 {{ margin-bottom: 0.25rem; }} .sub {{ color: #666; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: right; }}
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
th {{ background: #f5f5f5; }}
code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 0.85em; }}
</style></head><body>
<h1>Arena Hero — Public Third-Party Agent Leaderboard</h1>
<p class="sub">Content-addressed FFA battery. Generated {esc(manifest["generated_at"])} · sim {esc(manifest["sim_generator_version"])} · SDK {esc(manifest["sdk"]["version"])}</p>
<h2>Ranking</h2>
<table>
<thead><tr><th>#</th><th>Agent</th><th>Wins</th><th>Matches</th><th>Mean rank</th><th>Survival</th><th>Kills</th><th>Deposits</th><th>Resources</th><th>Composite</th></tr></thead>
<tbody>{"".join(body_rows)}</tbody>
</table>
<h2>Scenarios</h2><ul>{scenarios}</ul>
<h2>Contestants (pinned)</h2><ul>{contestants}</ul>
<p>Composite = rank×0.6 + kill×0.3 + economy×0.1 (each min-max normalized across the pool). Reproduce with <code>uv run python scripts/leaderboard_battery.py --seeds ...</code>.</p>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--out-dir", type=Path, default=Path("artifacts/leaderboard"))
    ap.add_argument("--smoke", action="store_true", help="cap ticks at 40 for a fast sanity run")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    payload = run_battery(args.seeds, smoke=args.smoke)
    manifest = build_manifest()

    leaderboard_path = args.out_dir / "leaderboard.json"
    manifest_path = args.out_dir / "manifest.json"
    html_path = args.out_dir / "report.html"

    leaderboard_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    html_path.write_text(render_html(payload, manifest), encoding="utf-8")

    print("\n=== leaderboard ===")
    for row in payload["leaderboard"]:
        print(
            f"  #{row['composite'] and ''}{row['contestant']:8s} wins={row['wins']:2d} "
            f"mean_rank={row['mean_rank']:.2f} composite={row['composite']:.3f}"
        )
    print(f"\nwrote {leaderboard_path}")
    print(f"wrote {manifest_path}")
    print(f"wrote {html_path}")


if __name__ == "__main__":
    main()
