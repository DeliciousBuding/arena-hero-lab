"""Website-facing bench report (arena.bench.report.v4).

Turns the content-addressed FFA battery results into the rich JSON the
``leaderboard-web`` static site imports.  v4 = v3 contract (contestants,
leaderboard, scenarios with perEntry + per-match player stats, kill timeline,
per-tick samples) plus the new ``subLeaderboards`` stage/strategy boards.

Nothing here invents a number: every field is derived from the terminal row or
the content-addressed trace of the same match.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import median, pstdev
from typing import Any

from .config import WORKER as _WORKER  # noqa: F401  (kept for symmetry)
from .leaderboard import LeaderboardRow
from .orchestrator import FfaReport

SCHEMA = "arena.bench.report.v4"
RULES_VERSION = "v0.14"
_SAMPLE_STEP = 50

# Clean, factual labels.  No marketing superlatives and no legacy TypeScript
# contestants (ts-aggressive / ts-safety are retired from the public table).
CONTESTANT_META: dict[str, dict[str, Any]] = {
    "evolve": {
        "label": "arena-evolve",
        "kind": "python",
        "configNote": "Torther 社区开源：基因启发式策略 + GA 进化研究，固定 evolve_v7_best 快照。",
        "repoUrl": "https://github.com/Torther/arena-evolve",
        "linuxdoUrl": "https://linux.do/t/topic/2723397",
        "linuxdoTitle": "Arena Hero 的一套进化框架(含可直接部署 agent)",
    },
    "drew-z": {
        "label": "arena-hero-agent (Drew-Z)",
        "kind": "python",
        "configNote": "Drew-Z 社区开源：资源优先 resource-first，12W+4V+4R 基础舰队，官方 SDK。",
        "repoUrl": "https://github.com/Drew-Z/arena-hero-agent",
        "linuxdoUrl": "https://linux.do/t/topic/2703873",
        "linuxdoTitle": "【开源】Arena Hero 无人值守 Agent：资源优先策略",
    },
    "guide": {
        "label": "arena-hero-guide",
        "kind": "python",
        "configNote": "VelvetEvening 社区开源：双策略 v3.3（扫荡 / 龟守可切换），官方 SDK。",
        "repoUrl": "https://github.com/VelvetEvening/ArenaHero-nearly-perfect-guide",
        "linuxdoUrl": "https://linux.do/t/topic/2715054",
        "linuxdoTitle": "近乎完美的双策略 for Arena-Hero",
    },
    "waaiging": {
        "label": "ArenaHero (Waaiging)",
        "kind": "python",
        "configNote": "Waaiging 社区开源：SmartTactic 全能战术，自适应经济 / 动态产兵 / 编队推进。",
        "repoUrl": "https://github.com/Waaiging/ArenaHero",
        "linuxdoUrl": "https://linux.do/t/topic/2721042",
        "linuxdoTitle": "Arena Hero 游戏体验分享",
    },
    "tactic": {
        "label": "arena-hero-tactic",
        "kind": "python",
        "configNote": "feixingwawa 社区开源：资源优先 + 均衡防守，矿点调度 + 探索门控。",
        "repoUrl": "https://github.com/feixingwawa/arena-hero-tactic",
        "linuxdoUrl": "https://linux.do/t/topic/2726683",
        "linuxdoTitle": "【开源推广】Arena Hero的agent",
    },
    "wuwd": {
        "label": "arena-hero-agent (WuDiWangWaSai)",
        "kind": "python",
        "configNote": "WuDiWangWaSai 维护的 Drew-Z 线 fork：CoreFarmer 经济型，worker 23 资源优先。",
        "repoUrl": "https://github.com/WuDiWangWaSai/arena-hero-agent",
    },
    "massarmy": {
        "label": "arena-hero-agent (暴兵流)",
        "kind": "python",
        "configNote": "WuDiWangWaSai 暴兵流分支 codex/mass-army：三阶段产兵至 48 人口满编 + 小队进攻 + 停滞核心斩首。",
        "repoUrl": "https://github.com/WuDiWangWaSai/arena-hero-agent",
        "linuxdoUrl": "https://linux.do/t/topic/2743669",
        "linuxdoTitle": "【Arena】自动化脚本：快速发展，暴兵流",
    },
    "rand": {
        "label": "rand (随机对照)",
        "kind": "control",
        "configNote": "确定性伪随机移动对照 bot，保持无状态、可复现。",
    },
    "wait": {
        "label": "wait (静止对照)",
        "kind": "control",
        "configNote": "永不行动的对照 bot，用于标定经济/军事零基线。",
    },
    "python": {
        "label": "arena-hero-agent (ours)",
        "kind": "ours",
        "configNote": "我方 Python agent（compose_decider），仅内部研究对照，不进公开榜。",
        "repoUrl": "https://github.com/DeliciousBuding/arena-hero-agent",
    },
    "hunter": {
        "label": "hunter (ours)",
        "kind": "ours",
        "configNote": "我方内置 HunterBot（BFS 追猎 + 经济循环），仅内部研究对照，不进公开榜。",
    },
}

SCENARIO_LABELS: dict[str, str] = {
    "ffa-std": "标准地图",
    "ffa-open": "开阔地图",
    "ffa-scarce": "资源匮乏",
    "ffa-maze": "迷宫压力",
    "ffa-remote": "偏远出生",
    "ffa-large": "大图长局",
    "ffa-sparse": "稀疏枯竭",
    "ffa-long": "长期对抗",
    "ffa-respawn": "重生压力",
    "ffa-royale": "大型大混战",
    "ffa-royale-scarce": "大混战稀缺",
    "ffa-barren-respawn": "荒区远点重生",
}


def _contestant_entry(cid: str) -> dict[str, Any]:
    meta = CONTESTANT_META.get(cid, {"label": cid, "kind": "python", "configNote": ""})
    entry: dict[str, Any] = {
        "id": cid,
        "label": meta["label"],
        "kind": meta["kind"],
        "configNote": meta["configNote"],
    }
    for key in ("repoUrl", "linuxdoUrl", "linuxdoTitle"):
        if key in meta:
            entry[key] = meta[key]
    return entry


def _frame_at(trace: list[dict], tick: int) -> dict:
    return trace[min(max(int(tick), 0), len(trace) - 1)]


def _players(frame: dict) -> dict:
    return frame.get("players", {})


def _rank_map(ranked: Sequence[Mapping[str, float | str]]) -> dict[str, int]:
    return {str(m["contestant"]): round(float(m["rank"])) for m in ranked}


def _first_kill_ticks(trace: list[dict], roster: Sequence[str]) -> dict[str, int | None]:
    first: dict[str, int | None] = {cid: None for cid in roster}
    for frame in trace:
        tick = int(frame.get("tick", 0))
        for cid, player in frame.get("players", {}).items():
            stats = player.get("stats") or {}
            if first.get(cid) is None and int(stats.get("core_kills", 0)) >= 1:
                first[cid] = tick
    return first


def _kill_events(trace: list[dict], roster: Sequence[str]) -> list[dict]:
    """Derive core-destruction events with killer attribution.

    Cores can be destroyed and respawned within the same tick (respawn is the
    last engine step), so an alive=True→False transition is invisible in the
    end-of-tick frame.  Instead we detect kills via two independent signals:

    1. ``core_kills`` stat delta → identifies the killer.
    2. ``core.uid`` change (or core disappearance) → identifies the victim
       (a respawned core has a new uid because ``Core.__init__`` calls
       ``next_id()``).
    """
    events: list[dict] = []
    prev_kills: dict[str, int] = {cid: 0 for cid in roster}
    prev_core_uid: dict[str, object] = {cid: None for cid in roster}
    for frame in trace:
        tick = int(frame.get("tick", 0))
        players_raw = frame.get("players")
        players: dict[str, dict] = players_raw if isinstance(players_raw, dict) else {}
        curr_kills: dict[str, int] = {}
        curr_core_uid: dict[str, object] = {}
        for cid in roster:
            p = players.get(cid) or {}
            stats = p.get("stats") or {}
            curr_kills[cid] = int(stats.get("core_kills", 0))
            core = p.get("core")
            curr_core_uid[cid] = core.get("uid") if isinstance(core, dict) else None
        killers = [k for k in roster if curr_kills.get(k, 0) > prev_kills.get(k, 0)]
        victims: list[str] = []
        for cid in roster:
            prev_uid = prev_core_uid.get(cid)
            curr_uid = curr_core_uid.get(cid)
            if prev_uid is not None and curr_uid != prev_uid:
                victims.append(cid)
        if killers or victims:
            events.append(
                {
                    "tick": tick,
                    "victim": victims[0] if victims else None,
                    "destroyedBy": killers,
                }
            )
        prev_kills = curr_kills
        prev_core_uid = curr_core_uid
    return events


def _per_tick_samples(
    trace: list[dict], roster: Sequence[str], ticks: int, step: int = _SAMPLE_STEP
) -> list[dict]:
    samples: list[dict] = []
    for tick in range(0, ticks + 1, step):
        frame = _frame_at(trace, tick)
        players: dict[str, dict] = {}
        for cid in roster:
            player = frame.get("players", {}).get(cid) or {}
            core = player.get("core") or {}
            players[cid] = {
                "resources": int(core.get("resources", 0)) if core else 0,
                "population": len(player.get("units", [])),
            }
        samples.append({"tick": tick, "players": players})
    return samples


def extract_match_obs(
    report: FfaReport,
    ranked: Sequence[Mapping[str, float | str]],
    roster: Sequence[str],
    seed: int,
    scenario_id: str,
    ticks: int,
) -> dict[str, Any]:
    """Compact per-match observation consumed by the bench report builder."""
    rank_by = _rank_map(ranked)
    first_kill = _first_kill_ticks(report.trace, roster)
    kill_events = _kill_events(report.trace, roster)
    samples = _per_tick_samples(report.trace, roster, ticks)

    peak_population: dict[str, float] = {cid: 0.0 for cid in roster}
    for frame in report.trace:
        raw_players = frame.get("players")
        players_frame: dict[str, dict] = raw_players if isinstance(raw_players, dict) else {}
        for cid, player in players_frame.items():
            pop = int(player.get("population", len(player.get("units", []))))
            if pop > peak_population.get(cid, 0.0):
                peak_population[cid] = float(pop)

    players: dict[str, dict] = {}
    terminal_by = {t.contestant_id: t for t in report.terminal}
    for cid in roster:
        t = terminal_by.get(cid)
        if t is None:
            continue
        stats = dict(t.stats)
        players[cid] = {
            "aliveTicks": int(t.ticks_alive),
            "beaconTicks": int(stats.get("beacon_ticks", 0)),
            "damageDealt": int(stats.get("damage_dealt", 0)),
            "deposited": int(stats.get("deposited", 0)),
            "finalPopulation": int(t.population_final),
            "finalResources": int(t.final_resources),
            "firstKillTick": first_kill.get(cid),
            "harvested": int(stats.get("harvested", 0)),
            "kills": int(stats.get("core_kills", 0)),
            "populationPeak": int(peak_population.get(cid, 0.0)),
            "unitsLost": int(stats.get("units_lost", 0)),
            "isWinner": rank_by.get(cid) == 1,
        }

    winner = next((cid for cid, m in rank_by.items() if m == 1), None)
    return {
        "seed": seed,
        "scenario": scenario_id,
        "winner": winner,
        "rank": rank_by,
        "players": players,
        "killEvents": kill_events,
        "perTickSamples": samples,
    }


def build_bench_payload(
    *,
    rows: Sequence[LeaderboardRow],
    subboards: Mapping[str, list[dict]],
    match_records: Sequence[dict[str, Any]],
    scenario_ids: Sequence[str],
    scenario_infos: Sequence[dict[str, Any]],
    roster: Sequence[str],
    seeds: Sequence[int],
    ticks: int,
    generated_at: str,
    source_label: str,
) -> dict[str, Any]:
    """Assemble the full v4 website payload from aggregated battery output."""
    leaderboard: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        scenario_ranks: dict[str, float | None] = {}
        # per-scenario mean rank
        for sid in scenario_ids:
            ranks = [
                m["rank"][row.contestant]
                for m in match_records
                if m["scenario"] == sid and row.contestant in m["rank"]
            ]
            scenario_ranks[sid] = round(sum(ranks) / len(ranks), 3) if ranks else None
        per_match_ranks = [
            m["rank"][row.contestant] for m in match_records if row.contestant in m["rank"]
        ]
        mean_rank = (
            round(sum(per_match_ranks) / len(per_match_ranks), 3) if per_match_ranks else 0.0
        )
        rank_values = list(scenario_ranks.values())
        rank_values = [v for v in rank_values if v is not None]
        rank_stddev = round(pstdev(rank_values), 3) if len(rank_values) > 1 else 0.0
        kill_rate = round(row.total_kills / row.matches, 3) if row.matches else 0.0
        leaderboard.append(
            {
                "rank": index + 1,
                "contestantId": row.contestant,
                "composite": row.composite,
                "avgRank": mean_rank,
                "rankStddev": rank_stddev,
                "killRate": kill_rate,
                "killScore": row.kill_score,
                "rankScore": row.rank_score,
                "economyScore": row.economy_score,
                "survivalMedian": round(row.survival_rate, 4),
                "survivalScore": round(row.survival_rate, 4),
                "scenarioRanks": scenario_ranks,
            }
        )

    # scenario objects with perEntry + matches
    scenarios: list[dict[str, Any]] = []
    for info in scenario_infos:
        sid = info["id"]
        matches = [m for m in match_records if m["scenario"] == sid]
        per_entry: dict[str, dict | None] = {}
        for cid in roster:
            cid_matches = [m for m in matches if cid in m["players"]]
            if not cid_matches:
                per_entry[cid] = None
                continue
            n = len(cid_matches)
            avg_rank = sum(m["rank"][cid] for m in cid_matches) / n
            ranks = [m["rank"][cid] for m in cid_matches]
            best = min(ranks)
            worst = max(ranks)
            kills = sum(m["players"][cid]["kills"] for m in cid_matches)
            damage = sum(m["players"][cid]["damageDealt"] for m in cid_matches)
            harvested = sum(m["players"][cid]["harvested"] for m in cid_matches)
            deposited = sum(m["players"][cid]["deposited"] for m in cid_matches)
            peak = sum(m["players"][cid]["populationPeak"] for m in cid_matches) / n
            final_pop = sum(m["players"][cid]["finalPopulation"] for m in cid_matches) / n
            units_lost = sum(m["players"][cid]["unitsLost"] for m in cid_matches)
            alive = sum(m["players"][cid]["aliveTicks"] for m in cid_matches) / n
            beacon = sum(m["players"][cid]["beaconTicks"] for m in cid_matches) / n
            fk = [
                m["players"][cid]["firstKillTick"]
                for m in cid_matches
                if m["players"][cid]["firstKillTick"] is not None
            ]
            first_kill = min(fk) if fk else None
            kill_matches = sum(1 for m in cid_matches if m["players"][cid]["kills"] > 0)
            per_entry[cid] = {
                "avgRank": round(avg_rank, 3),
                "bestRank": best,
                "worstRank": worst,
                "kills": kills,
                "killRate": round(kills / n, 3),
                "killMatches": kill_matches,
                "damageDealt": damage,
                "harvested": harvested,
                "deposited": deposited,
                "populationPeak": round(peak, 2),
                "finalPopulation": round(final_pop, 2),
                "unitsLost": units_lost,
                "aliveTicks": round(alive, 2),
                "beaconTicks": round(beacon, 2),
                "firstKillTick": first_kill,
                "resourcesPerTick": round(harvested / (info["params"]["ticks"] * n), 4),
                "damagePerLoss": round(damage / units_lost, 3) if units_lost else 0.0,
                "survivalMedian": round(
                    median(
                        [1.0 if m["players"][cid]["aliveTicks"] > 0 else 0.0 for m in cid_matches]
                    ),
                    4,
                ),
                "matchCount": n,
            }
        scenarios.append(
            {
                "name": sid,
                "label": SCENARIO_LABELS.get(sid, sid),
                "template": info["template"],
                "perEntry": per_entry,
                "matches": [
                    {
                        "seed": m["seed"],
                        "winner": m["winner"],
                        "rank": m["rank"],
                        "players": m["players"],
                        "killEvents": m["killEvents"],
                        "perTickSamples": m["perTickSamples"],
                    }
                    for m in matches
                ],
            }
        )

    entry_scenario_stats: dict[str, dict[str, dict]] = {}
    for cid in roster:
        entry_scenario_stats[cid] = {}
        for scenario in scenarios:
            stat = scenario["perEntry"].get(cid)
            if stat:
                entry_scenario_stats[cid][scenario["name"]] = stat

    return {
        "schema": SCHEMA,
        "generatedAt": generated_at,
        "convertedAt": generated_at,
        "source": source_label,
        "params": {
            "players": len(roster),
            "rulesVersion": RULES_VERSION,
            "scenarios": list(scenario_ids),
            "seeds": list(seeds),
            "ticks": ticks,
        },
        "contestants": [_contestant_entry(cid) for cid in roster],
        "leaderboard": leaderboard,
        "subLeaderboards": subboards,
        "scenarios": scenarios,
        "entryScenarioStats": entry_scenario_stats,
        "scenarioOrder": list(scenario_ids),
    }


__all__ = [
    "CONTESTANT_META",
    "SCENARIO_LABELS",
    "build_bench_payload",
    "extract_match_obs",
]
