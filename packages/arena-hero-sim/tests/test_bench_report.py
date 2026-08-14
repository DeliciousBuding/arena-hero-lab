"""Website bench payload (v4) shape and clean contestant metadata."""

from __future__ import annotations

from arena_hero_sim.ffa.bench_report import CONTESTANT_META, build_bench_payload
from arena_hero_sim.ffa.leaderboard import LeaderboardRow


def _row(cid: str, rank: int) -> LeaderboardRow:
    return LeaderboardRow(
        contestant=cid,
        mean_rank=float(rank),
        wins=0,
        matches=1,
        survival_rate=1.0,
        total_kills=0.0,
        total_deposited=0.0,
        total_resources=0.0,
        total_harvested=0.0,
        total_damage=0.0,
        rank_score=0.0,
        kill_score=0.0,
        economy_score=0.0,
        composite=0.0,
    )


def test_payload_has_clean_contestants_and_four_subboards() -> None:
    roster = ("evolve", "drew-z", "guide", "waaiging", "tactic", "wuwd", "massarmy", "rand", "wait")
    rows = [_row(cid, i + 1) for i, cid in enumerate(roster)]
    subboards = {board: [] for board in ("early_economy", "mid_game", "late_game", "military")}
    scenario_ids = ["ffa-std"]
    infos = [
        {
            "id": "ffa-std",
            "params": {"ticks": 2000},
            "template": {
                "configNote": "Standard ring",
                "radius": 128,
                "randomDrop": False,
                "resources": "1.0",
            },
        }
    ]
    match_records = [
        {
            "seed": 0,
            "scenario": "ffa-std",
            "winner": "massarmy",
            "rank": {cid: i + 1 for i, cid in enumerate(roster)},
            "players": {
                cid: {
                    "aliveTicks": 2000,
                    "beaconTicks": 0,
                    "damageDealt": 0,
                    "deposited": 0,
                    "finalPopulation": 1,
                    "finalResources": 5,
                    "firstKillTick": None,
                    "harvested": 0,
                    "kills": 0,
                    "populationPeak": 1,
                    "unitsLost": 0,
                    "isWinner": cid == "massarmy",
                }
                for cid in roster
            },
            "killEvents": [],
            "perTickSamples": [
                {"tick": 0, "players": {cid: {"resources": 5, "population": 1} for cid in roster}}
            ],
        }
    ]
    payload = build_bench_payload(
        rows=rows,
        subboards=subboards,
        match_records=match_records,
        scenario_ids=scenario_ids,
        scenario_infos=infos,
        roster=roster,
        seeds=[0],
        ticks=2000,
        generated_at="2026-08-14T00:00:00Z",
        source_label="test",
    )

    assert payload["schema"] == "arena.bench.report.v4"
    assert len(payload["contestants"]) == 9
    ids = {c["id"] for c in payload["contestants"]}
    assert "massarmy" in ids
    assert "ts-aggressive" not in ids and "ts-safety" not in ids
    labels = " ".join(c["label"] for c in payload["contestants"])
    notes = " ".join(c["configNote"] for c in payload["contestants"])
    assert "进化冠军" not in labels and "进化冠军" not in notes
    assert set(payload["subLeaderboards"]) == {"early_economy", "mid_game", "late_game", "military"}
    assert payload["scenarios"][0]["perEntry"]["massarmy"]["avgRank"] == 7.0
    assert "进化冠军" not in CONTESTANT_META["evolve"]["configNote"]
