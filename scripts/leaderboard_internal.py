"""Internal research melee: public roster + our own python / hunter contestants.

Deliberately separate from the public battery.  Our own ``arena-hero-agent``
(``python``) and ``HunterBot`` (``hunter``) are *research controls*: they are
scored on the same benchmark (same scenarios / seeds / stage sub-boards) but are
marked ``kind: ours`` and written to ``internal_*`` artifacts only — they never
enter the public leaderboard, so we are never both referee and competitor.

Usage:
    python scripts/leaderboard_internal.py --seeds 0 1 2 --out-dir artifacts/internal
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena_hero_sim.ffa.bench_report import build_bench_payload, extract_match_obs
from arena_hero_sim.ffa.contestants import HunterBot
from arena_hero_sim.ffa.leaderboard import (
    SCENARIOS,
    aggregate_leaderboard,
    rank_metrics,
    terminal_metrics,
)
from arena_hero_sim.ffa.orchestrator import run_ffa
from arena_hero_sim.ffa.public_contestants import (
    PUBLIC_ROSTER,
    build_public_leaderboard_contestants,
)
from arena_hero_sim.ffa.python_agent_shim import PythonAgentStrategy
from arena_hero_sim.ffa.stage_metrics import aggregate_stages, extract_match_stages

INTERNAL_ROSTER: tuple[str, ...] = (*PUBLIC_ROSTER, "python", "hunter")


def build_internal_contestants() -> tuple[dict[str, Any], list[Any]]:
    contestants, sdk = build_public_leaderboard_contestants()
    closables: list[Any] = list(sdk)
    python = PythonAgentStrategy()
    contestants["python"] = python
    contestants["hunter"] = HunterBot()
    closables.append(python)
    return contestants, closables


def run_internal(seeds: list[int], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    stage_records: list[dict] = []
    match_records: list[dict] = []
    scenario_infos: list[dict] = []
    per_scenario: list[dict] = []

    for scenario in SCENARIOS:
        per_seed: list[dict] = []
        for seed in seeds:
            contestants, sdk = build_internal_contestants()
            try:
                report = run_ffa(
                    contestants,
                    seed=seed,
                    ticks=scenario.ticks,
                    size=scenario.size,
                    obstacle_density=scenario.obstacle_density,
                    spawn_center=scenario.spawn_center,
                    resource_scale=scenario.resource_scale,
                    resource_replenish_every=scenario.resource_replenish_every,
                    respawn_style=scenario.respawn_style,
                )
            finally:
                for strategy in sdk:
                    strategy.close()

            metrics = {t.contestant_id: terminal_metrics(t) for t in report.terminal}
            for cid, m in metrics.items():
                m["contestant"] = cid
            ranked = rank_metrics([metrics[cid] for cid in INTERNAL_ROSTER])
            for m in ranked:
                records.append(dict(m))
            stage_records.append(extract_match_stages(report))
            match_records.append(
                extract_match_obs(
                    report, ranked, INTERNAL_ROSTER, seed, scenario.id, scenario.ticks
                )
            )
            per_seed.append(
                {
                    "seed": seed,
                    "artifact_sha256": report.artifact_sha256,
                    "winner": next(m["contestant"] for m in ranked if m["rank"] == 1.0),
                }
            )
            print(f"[internal][{scenario.id}] seed={seed} sha={report.artifact_sha256[:12]}")

        scenario_params = {
            "size": scenario.size,
            "obstacle_density": scenario.obstacle_density,
            "resource_scale": scenario.resource_scale,
            "spawn_center": list(scenario.spawn_center),
            "resource_replenish_every": scenario.resource_replenish_every,
            "respawn_style": scenario.respawn_style,
            "ticks": scenario.ticks,
        }
        per_scenario.append(
            {"id": scenario.id, "name": scenario.name, "params": scenario_params, "seeds": per_seed}
        )
        scenario_infos.append(
            {
                "id": scenario.id,
                "params": scenario_params,
                "template": {
                    "configNote": scenario.name,
                    "radius": scenario.size // 2,
                    "randomDrop": False,
                    "resources": str(scenario.resource_scale),
                },
            }
        )

    rows = aggregate_leaderboard(records, INTERNAL_ROSTER)
    subboards = aggregate_stages(stage_records, INTERNAL_ROSTER)
    bench = build_bench_payload(
        rows=rows,
        subboards=subboards,
        match_records=match_records,
        scenario_ids=[s["id"] for s in scenario_infos],
        scenario_infos=scenario_infos,
        roster=list(INTERNAL_ROSTER),
        seeds=list(seeds),
        ticks=2000,
        generated_at=datetime.now(UTC).isoformat(),
        source_label="arena-hero-lab scripts/leaderboard_internal.py",
    )
    return {
        "schema": "arena.leaderboard.internal.v1",
        "roster": list(INTERNAL_ROSTER),
        "scenarios": per_scenario,
        "leaderboard": [row.to_json() for row in rows],
        "subLeaderboards": subboards,
        "bench": bench,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--out-dir", type=Path, default=Path("artifacts/internal"))
    args = ap.parse_args()
    payload = run_internal(args.seeds, args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "internal_leaderboard.json").write_text(
        json.dumps(
            {k: v for k, v in payload.items() if k != "bench"},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (args.out_dir / "internal_bench.json").write_text(
        json.dumps(payload["bench"], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("\n=== internal leaderboard ===")
    for row in payload["leaderboard"]:
        print(
            f"  {row['contestant']:8s} wins={row['wins']:2d} mean_rank={row['mean_rank']:.2f} composite={row['composite']:.3f}"
        )
    print(f"\nwrote {args.out_dir / 'internal_leaderboard.json'}")
    print(f"wrote {args.out_dir / 'internal_bench.json'}")


if __name__ == "__main__":
    main()
