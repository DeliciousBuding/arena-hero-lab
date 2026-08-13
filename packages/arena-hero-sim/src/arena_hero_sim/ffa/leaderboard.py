"""Fair public leaderboard: ranking chain, composite score, scenario battery.

Faithful to ``docs/design/arena-leaderboard-v1.md``:

- Per-match ranking chain (first wins): alive -> core kills -> deposits ->
  resources -> population.  Equal metrics share the same competitive rank.
- Cross-scenario composite: ``rankScore*0.6 + killScore*0.3 + economyScore*0.1``
  with each component min-max normalized across the shared pool (no privileged
  sub-tables for the built-in control bots).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from .config import RESOURCE_REPLENISH_EVERY
from .orchestrator import FfaTerminal

# Per-match ranking chain (metric name in a terminal metric dict).
RANK_CHAIN: tuple[str, ...] = (
    "survival_alive",
    "core_kills",
    "deposited",
    "final_resources",
    "population_final",
)

# Weights must sum to 1.0.
COMPOSITE_WEIGHTS: Mapping[str, float] = {
    "rank": 0.6,
    "kill": 0.3,
    "economy": 0.1,
}

_SCENARIO_BASE_TICKS = 2000
_SCENARIO_LONG_TICKS = 4000


@dataclass(frozen=True, slots=True)
class ScenarioPreset:
    id: str
    name: str
    size: int
    obstacle_density: float
    resource_scale: float
    spawn_center: tuple[int, int]
    ticks: int
    resource_replenish_every: int = RESOURCE_REPLENISH_EVERY
    respawn_style: str = "ring"


# Ordered battery (design doc section 5).  Order is the official presentation
# order and does not confer any advantage: ranking is pooled across scenarios.
SCENARIOS: tuple[ScenarioPreset, ...] = (
    ScenarioPreset("ffa-std", "Standard ring", 256, 0.225, 1.0, (0, 0), _SCENARIO_BASE_TICKS),
    ScenarioPreset("ffa-open", "Open field", 384, 0.225, 1.0, (0, 0), _SCENARIO_BASE_TICKS),
    ScenarioPreset("ffa-scarce", "Scarce resources", 256, 0.225, 0.5, (0, 0), _SCENARIO_BASE_TICKS),
    ScenarioPreset("ffa-maze", "Maze stress", 256, 0.5, 1.0, (-96, 128), _SCENARIO_LONG_TICKS),
    ScenarioPreset("ffa-remote", "Remote spawn", 256, 0.225, 1.0, (-96, 128), _SCENARIO_BASE_TICKS),
    ScenarioPreset("ffa-long", "Long horizon", 256, 0.225, 1.0, (0, 0), _SCENARIO_LONG_TICKS),
    ScenarioPreset(
        "ffa-respawn", "Respawn pressure", 256, 0.225, 1.0, (0, 0), _SCENARIO_BASE_TICKS
    ),
)

# Opt-in research scenario (not part of the public leaderboard battery):
# large sparse map with resource replenishment disabled and far-random respawn,
# reproducing the production "destroyed core respawns in a depleted area" regime
# documented in docs/design/production-world-model-v1.md.
# size=512 (not 1024): exploration_v2's BFS frontier flood and the evolve A*
# scale with map area, so 1024 is impractically slow per tick for the current
# strategy compute. 512 is still 4x the std map area and reproduces the regime.
BARREN_RESPAWN_SCENARIO: Final = ScenarioPreset(
    id="ffa-barren-respawn",
    name="Barren far respawn (depletion)",
    size=512,
    obstacle_density=0.225,
    resource_scale=0.25,
    spawn_center=(0, 0),
    ticks=_SCENARIO_LONG_TICKS,
    resource_replenish_every=0,
    respawn_style="barren",
)


def terminal_metrics(entry: FfaTerminal) -> dict[str, float]:
    """Extract the ranking/reporting metrics from one terminal row."""
    stats = dict(entry.stats)
    return {
        "survival_alive": 1.0 if entry.survival_alive else 0.0,
        "core_kills": float(stats.get("core_kills", 0)),
        "deposited": float(stats.get("deposited", 0)),
        "final_resources": float(entry.final_resources),
        "population_final": float(entry.population_final),
        "harvested": float(stats.get("harvested", 0)),
        "damage_dealt": float(stats.get("damage_dealt", 0)),
    }


def rank_metrics(metrics: Sequence[dict[str, float]]) -> list[dict[str, float]]:
    """Assign competitive ranks (1 = winner) to metric dicts in place order.

    Returns a new list of dicts with an added ``rank`` key.  Ties share a rank
    and the next distinct position skips (standard competition ranking).
    """

    def key(m: Mapping[str, float]) -> tuple[float, ...]:
        return tuple(m[name] for name in RANK_CHAIN)

    ordered = sorted(range(len(metrics)), key=lambda i: key(metrics[i]), reverse=True)
    ranked: list[dict[str, float]] = [dict(m) for m in metrics]
    for position, index in enumerate(ordered):
        if position == 0:
            ranked[index]["rank"] = 1.0
            continue
        prev = ordered[position - 1]
        ranked[index]["rank"] = (
            ranked[prev]["rank"] if key(ranked[index]) == key(ranked[prev]) else float(position + 1)
        )
    return ranked


@dataclass(frozen=True, slots=True)
class LeaderboardRow:
    contestant: str
    mean_rank: float
    wins: int
    matches: int
    survival_rate: float
    total_kills: float
    total_deposited: float
    total_resources: float
    total_harvested: float
    total_damage: float
    rank_score: float
    kill_score: float
    economy_score: float
    composite: float

    def to_json(self) -> dict[str, float | int | str]:
        return {
            "contestant": self.contestant,
            "mean_rank": round(self.mean_rank, 3),
            "wins": self.wins,
            "matches": self.matches,
            "survival_rate": round(self.survival_rate, 4),
            "total_kills": round(self.total_kills, 2),
            "total_deposited": round(self.total_deposited, 2),
            "total_resources": round(self.total_resources, 2),
            "total_harvested": round(self.total_harvested, 2),
            "total_damage": round(self.total_damage, 2),
            "rank_score": round(self.rank_score, 4),
            "kill_score": round(self.kill_score, 4),
            "economy_score": round(self.economy_score, 4),
            "composite": round(self.composite, 4),
        }


def aggregate_leaderboard(
    records: Iterable[Mapping[str, float]],
    roster: Sequence[str],
) -> list[LeaderboardRow]:
    """Pool per-match metric records into a composite leaderboard.

    ``records`` are already-ranked metric dicts (each carries ``contestant`` and
    ``rank`` keys).  The composite is pooled across every match so control bots
    and third parties compete in one table.
    """
    by_contestant: dict[str, list[Mapping[str, float]]] = {cid: [] for cid in roster}
    for record in records:
        cid = str(record["contestant"])
        if cid not in by_contestant:
            by_contestant[cid] = []
        by_contestant[cid].append(record)

    n = max(len(roster), 1)
    rows: list[LeaderboardRow] = []
    for cid in roster:
        matches = by_contestant[cid]
        if not matches:
            continue
        count = len(matches)
        mean_rank = sum(float(m["rank"]) for m in matches) / count
        wins = sum(1 for m in matches if float(m["rank"]) == 1.0)
        survival_rate = sum(float(m["survival_alive"]) for m in matches) / count
        total_kills = sum(float(m["core_kills"]) for m in matches)
        total_deposited = sum(float(m["deposited"]) for m in matches)
        total_resources = sum(float(m["final_resources"]) for m in matches)
        total_harvested = sum(float(m["harvested"]) for m in matches)
        total_damage = sum(float(m["damage_dealt"]) for m in matches)
        rows.append(
            LeaderboardRow(
                contestant=cid,
                mean_rank=mean_rank,
                wins=wins,
                matches=count,
                survival_rate=survival_rate,
                total_kills=total_kills,
                total_deposited=total_deposited,
                total_resources=total_resources,
                total_harvested=total_harvested,
                total_damage=total_damage,
                rank_score=0.0,
                kill_score=0.0,
                economy_score=0.0,
                composite=0.0,
            )
        )

    max_kills = max((r.total_kills for r in rows), default=0.0)
    max_economy = max((r.total_deposited + r.total_harvested for r in rows), default=0.0)

    scored: list[LeaderboardRow] = []
    for row in rows:
        rank_score = (n - row.mean_rank) / max(n - 1, 1)
        kill_score = row.total_kills / max_kills if max_kills > 0 else 0.0
        economy_score = (
            (row.total_deposited + row.total_harvested) / max_economy if max_economy > 0 else 0.0
        )
        composite = (
            COMPOSITE_WEIGHTS["rank"] * rank_score
            + COMPOSITE_WEIGHTS["kill"] * kill_score
            + COMPOSITE_WEIGHTS["economy"] * economy_score
        )
        scored.append(
            LeaderboardRow(
                contestant=row.contestant,
                mean_rank=row.mean_rank,
                wins=row.wins,
                matches=row.matches,
                survival_rate=row.survival_rate,
                total_kills=row.total_kills,
                total_deposited=row.total_deposited,
                total_resources=row.total_resources,
                total_harvested=row.total_harvested,
                total_damage=row.total_damage,
                rank_score=rank_score,
                kill_score=kill_score,
                economy_score=economy_score,
                composite=composite,
            )
        )

    scored.sort(key=lambda r: r.composite, reverse=True)
    return scored


__all__ = [
    "COMPOSITE_WEIGHTS",
    "RANK_CHAIN",
    "SCENARIOS",
    "LeaderboardRow",
    "ScenarioPreset",
    "aggregate_leaderboard",
    "rank_metrics",
    "terminal_metrics",
]
