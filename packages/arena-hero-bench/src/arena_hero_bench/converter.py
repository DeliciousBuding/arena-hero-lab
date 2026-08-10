"""Deterministic conversion from benchmark reports to leaderboard web data."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "arena.bench.report.v3"

SCENARIO_LABELS = {
    "ffa-dense": "高密度冲突",
    "ffa-std": "标准地图",
    "ffa-open": "开阔地图",
    "ffa-scarce": "资源匮乏",
    "ffa-random": "随机落点",
    "ffa-resource-race": "中央矿争夺",
    "ffa-defense-pressure": "资源枯竭",
}

CONTESTANT_REPO_URL = {
    "farmer": "https://github.com/Drew-Z/arena-hero-agent",
    "farmer-eco": "https://github.com/Drew-Z/arena-hero-agent",
    "core": "https://github.com/VelvetEvening/ArenaHero-nearly-perfect-guide",
    "core-mil": "https://github.com/VelvetEvening/ArenaHero-nearly-perfect-guide",
    "waaiging": "https://github.com/Waaiging/ArenaHero",
    "waaiging-agg": "https://github.com/Waaiging/ArenaHero",
    "tactic": "https://github.com/feixingwawa/arena-hero-tactic",
    "arena-evolve": "https://github.com/Torther/arena-evolve",
    "ts-aggressive": "https://github.com/DeliciousBuding/arena-hero-agent-ts",
    "ts-safety": "https://github.com/DeliciousBuding/arena-hero-agent-ts",
}

CONTESTANT_LINUXDO_URL = {
    "farmer": "https://linux.do/t/topic/2703873",
    "farmer-eco": "https://linux.do/t/topic/2703873",
    "core": "https://linux.do/t/topic/2715054",
    "core-mil": "https://linux.do/t/topic/2715054",
    "waaiging": "https://linux.do/t/topic/2721042",
    "waaiging-agg": "https://linux.do/t/topic/2721042",
    "tactic": "https://linux.do/t/topic/2726683",
    "arena-evolve": "https://linux.do/t/topic/2723397",
}

CONTESTANT_LINUXDO_TITLE = {
    "farmer": "【开源】Arena Hero 无人值守 Agent：资源优先策略，支持本地、Docker 和 systemd",
    "farmer-eco": "【开源】Arena Hero 无人值守 Agent：资源优先策略，支持本地、Docker 和 systemd",
    "core": "近乎完美的双策略 for Arena-Hero (可满足自己扫荡和龟着换邀请码奖励两种需求)",
    "core-mil": "近乎完美的双策略 for Arena-Hero (可满足自己扫荡和龟着换邀请码奖励两种需求)",
    "waaiging": "Arena Hero 游戏体验分享",
    "waaiging-agg": "Arena Hero 游戏体验分享",
    "tactic": "【开源推广】Arena Hero的agent",
    "arena-evolve": "Arena Hero 的一套进化框架(含可直接部署 agent)",
}

CONTESTANT_LABEL = {
    "farmer": "farmer（资源优先）",
    "farmer-eco": "farmer-eco（经济变体）",
    "core": "core（双策略）",
    "core-mil": "core-mil（军事变体）",
    "waaiging": "waaiging（全能战术）",
    "waaiging-agg": "waaiging-agg（激进变体）",
    "tactic": "tactic（均衡防守）",
    "arena-evolve": "arena-evolve（进化冠军）",
    "ts-aggressive": "ts-aggressive（激进压制）",
    "ts-safety": "ts-safety（保守均衡）",
}

CONTESTANT_CONFIG_NOTE = {
    "farmer": "Drew-Z 社区开源：资源优先（resource-first），12W+4V+4R 基础舰队 + v0.14 动态价格适配",
    "farmer-eco": "Drew-Z 社区开源经济变体：worker_target=16 + beacon_policy=retreat，纯经济发育对照",
    "core": "VelvetEvening 社区开源：双策略 v3.3（arena_core_agent），扫荡/龟守可切换，mode=harvest/target=30",
    "core-mil": "VelvetEvening 社区开源军事变体：mode=control/target=8，偏重军事扩张",
    "waaiging": "Waaiging 社区开源：SmartTactic 全能战术，4 模式自适应经济、动态产兵、编队推进、Core 斩首、信标控制",
    "waaiging-agg": "Waaiging 社区开源激进变体：mode=aggress，6 先锋 + 9 游侠开局前压",
    "tactic": "feixingwawa 社区开源：资源优先 + 均衡防守战术客户端，12W/4V/4R 爬坡、矿点智能调度、Beacon 导向探索",
    "arena-evolve": "Torther 社区开源：基因启发式策略 + GA 进化研究，evolve_v7_best 冠军快照",
    "ts-aggressive": "Legacy TypeScript contestant：AGGRESSIVE_SAFETY_CONFIG（vanguardRatio=0.8 + accumulateThreshold=30），激进前压",
    "ts-safety": "Legacy TypeScript contestant：DEFAULT_SAFETY_CONFIG，前压与防守均衡",
}


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    total = 0.0
    for value in values:
        total += value
    return total / len(values)


def _stddev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    return math.sqrt(_mean([(value - average) ** 2 for value in values]))


def _resolve_player_id(key: str, seed: int, contestant_ids: Sequence[str]) -> str | None:
    if key in contestant_ids:
        return key
    suffix = f"-s{seed}"
    if key.endswith(suffix):
        candidate = key[: -len(suffix)]
        if candidate in contestant_ids:
            return candidate
    return None


def _safe_source_label(source_path: Path, source_root: Path | None) -> str:
    directory = source_path.resolve().parent
    if source_root is not None:
        try:
            relative = directory.relative_to(source_root.resolve())
        except ValueError:
            pass
        else:
            label = relative.as_posix()
            return label if label != "." else "source"
    return directory.name or "external"


def transform_report(
    raw: Mapping[str, Any],
    *,
    source_label: str,
    converted_at: str | None = None,
) -> dict[str, Any]:
    """Transform one v3 benchmark report into stable leaderboard data."""

    if raw.get("schema") != REPORT_SCHEMA:
        raise ValueError(f"unexpected schema: {raw.get('schema')} (expected {REPORT_SCHEMA})")

    contestants = raw.get("contestants")
    raw_scenarios = raw.get("scenarios")
    if not isinstance(contestants, list) or not isinstance(raw_scenarios, list):
        raise ValueError("contestants and scenarios must be arrays")

    contestant_ids = [str(contestant["id"]) for contestant in contestants]
    scenario_ids = [str(scenario["name"]) for scenario in raw_scenarios]
    entry_scenario_stats: dict[str, dict[str, dict[str, Any]]] = {}
    scenarios: list[dict[str, Any]] = []

    for scenario in raw_scenarios:
        stats_for_entry: dict[str, dict[str, Any]] = {}
        converted_matches: list[dict[str, Any]] = []
        for match in scenario["matches"]:
            seed = int(match["seed"])
            players: dict[str, dict[str, Any]] = {}
            for key, stats in match["perPlayer"].items():
                contestant_id = _resolve_player_id(key, seed, contestant_ids)
                if contestant_id is None:
                    continue
                players[contestant_id] = {**stats, "isWinner": match["winner"] == key}
                bucket = stats_for_entry.setdefault(
                    contestant_id,
                    {
                        "avgRank": 0,
                        "bestRank": math.inf,
                        "worstRank": -math.inf,
                        "kills": 0,
                        "killRate": 0,
                        "damageDealt": 0,
                        "harvested": 0,
                        "deposited": 0,
                        "populationPeak": 0,
                        "finalPopulation": 0,
                        "unitsLost": 0,
                        "aliveTicks": 0,
                        "beaconTicks": 0,
                        "firstKillTick": None,
                        "matchCount": 0,
                    },
                )
                for field in (
                    "kills",
                    "damageDealt",
                    "harvested",
                    "deposited",
                    "populationPeak",
                    "finalPopulation",
                    "unitsLost",
                    "aliveTicks",
                    "beaconTicks",
                ):
                    bucket[field] += stats[field]
                bucket["matchCount"] += 1
                first_kill_tick = stats["firstKillTick"]
                if first_kill_tick is not None:
                    previous = bucket["firstKillTick"]
                    bucket["firstKillTick"] = (
                        first_kill_tick if previous is None else min(previous, first_kill_tick)
                    )
                rank = match["rank"].get(key, match["rank"].get(contestant_id, 0))
                bucket["avgRank"] += rank
                bucket["bestRank"] = min(bucket["bestRank"], rank)
                bucket["worstRank"] = max(bucket["worstRank"], rank)

            kill_events: list[dict[str, Any]] = []
            for event in match.get("killEvents", []):
                converted_event: dict[str, Any] = {
                    "tick": event["tick"],
                    "destroyedBy": [
                        contestant_id
                        for raw_id in event["destroyedBy"]
                        if (contestant_id := _resolve_player_id(raw_id, seed, contestant_ids))
                        is not None
                    ],
                }
                if "victim" in event:
                    converted_event["victim"] = (
                        _resolve_player_id(event["victim"], seed, contestant_ids) or event["victim"]
                    )
                kill_events.append(converted_event)

            converted_match: dict[str, Any] = {
                "seed": seed,
                "winner": match["winner"],
                "rank": match["rank"],
                "players": players,
                "killEvents": kill_events,
            }
            if "perTickSamples" in match:
                converted_match["perTickSamples"] = [
                    {
                        "tick": sample["tick"],
                        "players": {
                            contestant_id: data
                            for key, data in sample["players"].items()
                            if (contestant_id := _resolve_player_id(key, seed, contestant_ids))
                            is not None
                        },
                    }
                    for sample in match["perTickSamples"]
                ]
            converted_matches.append(converted_match)

        for bucket in stats_for_entry.values():
            match_count = bucket["matchCount"]
            bucket["avgRank"] /= match_count
            bucket["killRate"] = bucket["kills"] / match_count
            bucket["bestRank"] = 0 if bucket["bestRank"] == math.inf else bucket["bestRank"]
            bucket["worstRank"] = 0 if bucket["worstRank"] == -math.inf else bucket["worstRank"]
            for field in ("kills", "damageDealt", "harvested", "deposited"):
                bucket[field] = math.floor(bucket[field] + 0.5)
            for field in (
                "populationPeak",
                "finalPopulation",
                "unitsLost",
                "aliveTicks",
                "beaconTicks",
            ):
                bucket[field] /= match_count

        for contestant_id, bucket in stats_for_entry.items():
            entry_scenario_stats.setdefault(contestant_id, {})[scenario["name"]] = bucket

        scenarios.append(
            {
                "name": scenario["name"],
                "label": SCENARIO_LABELS.get(scenario["name"], scenario["name"]),
                "template": scenario["template"],
                "perEntry": scenario["perEntry"],
                "matches": converted_matches,
            }
        )

    leaderboard_rows = [*raw.get("leaderboard", []), *raw.get("leaderboardControl", [])]
    leaderboard: list[dict[str, Any]] = []
    for index, entry in enumerate(
        sorted(leaderboard_rows, key=lambda item: item["composite"], reverse=True),
        start=1,
    ):
        scenario_ranks = {
            scenario["name"]: (
                scenario["perEntry"].get(entry["contestantId"], {}).get("avgRank")
                if scenario["perEntry"].get(entry["contestantId"]) is not None
                else None
            )
            for scenario in scenarios
        }
        rank_values = [value for value in scenario_ranks.values() if value is not None]
        leaderboard.append(
            {
                "rank": index,
                "contestantId": entry["contestantId"],
                "composite": entry["composite"],
                "avgRank": entry["avgRank"],
                "rankStddev": _stddev(rank_values),
                "killRate": entry["killRate"],
                "killScore": entry["killScore"],
                "rankScore": entry["rankScore"],
                "economyScore": entry["economyScore"],
                "survivalMedian": entry["survivalMedian"],
                "survivalScore": entry["survivalScore"],
                "scenarioRanks": scenario_ranks,
            }
        )

    converted_contestants: list[dict[str, Any]] = []
    for contestant in contestants:
        contestant_id = contestant["id"]
        converted = {
            "id": contestant_id,
            "label": CONTESTANT_LABEL.get(contestant_id, contestant["label"]),
            "kind": "python",
            "configNote": CONTESTANT_CONFIG_NOTE.get(
                contestant_id,
                contestant["configNote"],
            ),
        }
        optional_fields = {
            "repoUrl": CONTESTANT_REPO_URL,
            "linuxdoUrl": CONTESTANT_LINUXDO_URL,
            "linuxdoTitle": CONTESTANT_LINUXDO_TITLE,
        }
        for field, mapping in optional_fields.items():
            if contestant_id in mapping:
                converted[field] = mapping[contestant_id]
        converted_contestants.append(converted)

    return {
        "schema": raw["schema"],
        "generatedAt": raw["generatedAt"],
        "convertedAt": converted_at or str(raw["generatedAt"]),
        "source": source_label.replace("\\", "/"),
        "params": raw["params"],
        "contestants": converted_contestants,
        "leaderboard": leaderboard,
        "scenarios": scenarios,
        "entryScenarioStats": entry_scenario_stats,
        "scenarioOrder": scenario_ids,
    }


def convert_file(
    source_path: Path,
    output_path: Path,
    *,
    source_root: Path | None = None,
    source_label: str | None = None,
    converted_at: str | None = None,
) -> dict[str, Any]:
    """Read, transform, and atomically replace one leaderboard JSON document."""

    raw = json.loads(source_path.read_text(encoding="utf-8"))
    output = transform_report(
        raw,
        source_label=source_label or _safe_source_label(source_path, source_root),
        converted_at=converted_at,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return output


def utc_now() -> str:
    """Return a standards-compliant UTC timestamp for explicitly time-stamped exports."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
