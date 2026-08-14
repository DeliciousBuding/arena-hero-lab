"""Stage/strategy sub-leaderboard aggregation: normalization + payload shape."""

from __future__ import annotations

from arena_hero_sim.ffa.stage_metrics import SUBBOARD_DEFS, aggregate_stages


def test_aggregate_stages_normalizes_and_ranks() -> None:
    roster = ("a", "b", "c")
    per_match = {
        "a": {
            "early_harvested": 10.0,
            "early_deposited": 5.0,
            "early_resources": 3.0,
            "early_workers": 1.0,
            "mid_population": 4.0,
            "mid_combat": 2.0,
            "mid_resources": 3.0,
            "mid_deposited": 5.0,
            "late_resources": 3.0,
            "late_population": 4.0,
            "late_deposited": 5.0,
            "late_alive": 1.0,
            "military_damage": 20.0,
            "military_core_kills": 2.0,
            "military_peak_combat": 3.0,
        },
        "b": {
            "early_harvested": 5.0,
            "early_deposited": 2.0,
            "early_resources": 1.0,
            "early_workers": 1.0,
            "mid_population": 2.0,
            "mid_combat": 1.0,
            "mid_resources": 1.0,
            "mid_deposited": 2.0,
            "late_resources": 1.0,
            "late_population": 2.0,
            "late_deposited": 2.0,
            "late_alive": 1.0,
            "military_damage": 5.0,
            "military_core_kills": 0.0,
            "military_peak_combat": 1.0,
        },
        "c": {
            "early_harvested": 0.0,
            "early_deposited": 0.0,
            "early_resources": 0.0,
            "early_workers": 0.0,
            "mid_population": 1.0,
            "mid_combat": 0.0,
            "mid_resources": 0.0,
            "mid_deposited": 0.0,
            "late_resources": 0.0,
            "late_population": 1.0,
            "late_deposited": 0.0,
            "late_alive": 0.0,
            "military_damage": 0.0,
            "military_core_kills": 0.0,
            "military_peak_combat": 0.0,
        },
    }
    boards = aggregate_stages([per_match], roster)

    assert set(boards) == set(SUBBOARD_DEFS)
    for board_id, rows in boards.items():
        assert [r["rank"] for r in rows] == [1, 2, 3]
        for r in rows:
            assert 0.0 <= r["score"] <= 1.0
            assert set(r["components"]) == set(SUBBOARD_DEFS[board_id])
    assert boards["early_economy"][0]["contestant"] == "a"
    assert boards["military"][0]["contestant"] == "a"
