"""Fair stage-based sub-leaderboards from FFA trace snapshots.

The public battery already ranks contestants on one pooled composite
(rank 0.6 / kill 0.3 / economy 0.1).  This module adds the *stage* and
*strategy* sub-boards the L-station post needs:

- ``early``   (25% ticks)  early economy ramp
- ``mid``     (50% ticks)  mid-game economy + army posture
- ``late``    (75% ticks)  late-game value + survival
- ``military`` (full match) offensive output

Every component is aggregated per contestant (mean across matches) and then
min-max normalized across the shared pool, exactly like the composite: controls
and third parties compete in one pool, no privileged tables.  Scores are
therefore always in ``[0, 1]`` and reproducible from the same content-addressed
trace.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from .orchestrator import FfaReport

WORKER = "WORKER"
VANGUARD = "VANGUARD"
RANGER = "RANGER"
_COMBAT_TYPES = {VANGUARD, RANGER}

# Stage name -> fraction of the scenario tick budget.
STAGE_WINDOWS: Final[dict[str, float]] = {
    "early": 0.25,
    "mid": 0.50,
    "late": 0.75,
}


@dataclass(frozen=True, slots=True)
class StageMetricSet:
    """One contestant's mean metrics for one stage, before normalization."""

    contestant: str
    values: dict[str, float]


def _frame_at(trace: list[dict], tick: int) -> dict:
    idx = min(max(int(tick), 0), len(trace) - 1)
    return trace[idx]


def _unit_counts(units: list[dict]) -> tuple[int, int]:
    workers = combat = 0
    for unit in units:
        utype = unit.get("utype")
        if utype == WORKER:
            workers += 1
        elif utype in _COMBAT_TYPES:
            combat += 1
    return workers, combat


def _snapshot_for(cid: str, frame: dict) -> dict[str, float]:
    player = frame.get("players", {}).get(cid) or {}
    core = player.get("core") or {}
    units = player.get("units", [])
    workers, combat = _unit_counts(units)
    stats = player.get("stats") or {}
    return {
        "resources": float(core.get("resources", 0)) if core else 0.0,
        "population": float(len(units)),
        "workers": float(workers),
        "combat": float(combat),
        "harvested": float(stats.get("harvested", 0)),
        "deposited": float(stats.get("deposited", 0)),
        "damage": float(stats.get("damage_dealt", 0)),
        "core_kills": float(stats.get("core_kills", 0)),
        "alive": 1.0 if player.get("alive") else 0.0,
    }


def extract_match_stages(report: FfaReport) -> dict[str, dict[str, float]]:
    """Return per-contestant stage snapshots for one match.

    Keys are stage names plus ``"military"``; ``military`` uses final totals
    and peak combat over the whole match.
    """
    trace = report.trace
    ticks = max(report.ticks, 1)
    windows = {name: _frame_at(trace, round(frac * ticks)) for name, frac in STAGE_WINDOWS.items()}
    final_frame = _frame_at(trace, ticks)

    peak_combat: dict[str, float] = {}
    for frame in trace:
        players = frame.get("players", {})
        for cid, player in players.items():
            units = player.get("units", [])
            _, combat = _unit_counts(units)
            if combat > peak_combat.get(cid, 0.0):
                peak_combat[cid] = float(combat)

    out: dict[str, dict[str, float]] = {}
    for cid in report.contestant_ids:
        early = _snapshot_for(cid, windows["early"])
        mid = _snapshot_for(cid, windows["mid"])
        late = _snapshot_for(cid, windows["late"])
        final = _snapshot_for(cid, final_frame)
        out[cid] = {
            "early_harvested": early["harvested"],
            "early_deposited": early["deposited"],
            "early_resources": early["resources"],
            "early_workers": early["workers"],
            "mid_population": mid["population"],
            "mid_combat": mid["combat"],
            "mid_resources": mid["resources"],
            "mid_deposited": mid["deposited"],
            "late_resources": late["resources"],
            "late_population": late["population"],
            "late_deposited": late["deposited"],
            "late_alive": late["alive"],
            "military_damage": final["damage"],
            "military_core_kills": final["core_kills"],
            "military_peak_combat": peak_combat.get(cid, 0.0),
        }
    return out


# Sub-board definition: component key -> weight.  Weights sum to 1.0 per board.
SUBBOARD_DEFS: Final[dict[str, dict[str, float]]] = {
    "early_economy": {
        "early_harvested": 0.40,
        "early_deposited": 0.30,
        "early_resources": 0.20,
        "early_workers": 0.10,
    },
    "mid_game": {
        "mid_population": 0.30,
        "mid_combat": 0.30,
        "mid_resources": 0.20,
        "mid_deposited": 0.20,
    },
    "late_game": {
        "late_resources": 0.30,
        "late_population": 0.30,
        "late_deposited": 0.20,
        "late_alive": 0.20,
    },
    "military": {
        "military_damage": 0.40,
        "military_core_kills": 0.30,
        "military_peak_combat": 0.30,
    },
}


def _mean_accumulate(
    acc: dict[str, dict[str, float]],
    per_match: dict[str, dict[str, float]],
) -> None:
    for cid, values in per_match.items():
        slot = acc.setdefault(cid, {})
        for key, value in values.items():
            slot[key] = slot.get(key, 0.0) + value


def aggregate_stages(
    per_match_records: Sequence[dict[str, dict[str, float]]],
    roster: Sequence[str],
) -> dict[str, list[dict]]:
    """Aggregate per-match stage snapshots into normalized sub-leaderboards.

    Returns ``{board_id: [{"contestant": ..., "score": ..., rank, ...}, ...]}``
    sorted by score descending, with per-component normalized values attached.
    """
    acc: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    for per_match in per_match_records:
        _mean_accumulate(acc, per_match)
        for cid in per_match:
            counts[cid] = counts.get(cid, 0) + 1

    means: dict[str, dict[str, float]] = {}
    for cid, values in acc.items():
        n = max(counts.get(cid, 1), 1)
        means[cid] = {key: value / n for key, value in values.items()}

    boards: dict[str, list[dict]] = {}
    for board_id, components in SUBBOARD_DEFS.items():
        # min-max normalize each component across the shared pool.
        normalized: dict[str, dict[str, float]] = {cid: {} for cid in roster}
        for key in components:
            vals = {cid: means.get(cid, {}).get(key, 0.0) for cid in roster}
            lo = min(vals.values())
            hi = max(vals.values())
            span = hi - lo
            for cid in roster:
                v = vals[cid]
                normalized[cid][key] = 0.0 if span == 0 else (v - lo) / span

        rows: list[dict] = []
        for cid in roster:
            if cid not in means:
                continue
            score = sum(components[key] * normalized[cid][key] for key in components)
            rows.append(
                {
                    "contestant": cid,
                    "score": round(score, 4),
                    "components": {key: round(normalized[cid][key], 4) for key in components},
                    "raw": {key: round(means[cid].get(key, 0.0), 2) for key in components},
                }
            )
        rows.sort(key=lambda r: r["score"], reverse=True)
        for index, row in enumerate(rows):
            row["rank"] = index + 1
        boards[board_id] = rows
    return boards


__all__ = [
    "STAGE_WINDOWS",
    "SUBBOARD_DEFS",
    "aggregate_stages",
    "extract_match_stages",
]
