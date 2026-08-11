"""Unit tests for the official Python leaderboard data producer."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from arena_hero_bench.converter import REPORT_SCHEMA, transform_report
from arena_hero_bench.leaderboard_data import (
    LEADERBOARD_DATA_FIELDS,
    produce_leaderboard_data,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = WORKSPACE_ROOT / "apps" / "leaderboard-web" / "scripts" / "input" / "results.json"
FIXED_TIME = "2026-01-01T00:00:00Z"
FIXED_SOURCE = "fixtures/benchmark-v3"


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _minimal_report() -> dict[str, Any]:
    """Smallest report that passes fail-closed validation end-to-end."""
    return {
        "schema": REPORT_SCHEMA,
        "generatedAt": "2026-01-01T00:00:00Z",
        "params": {
            "players": 2,
            "rulesVersion": "v3",
            "scenarios": ["ffa-std"],
            "seeds": [1],
            "ticks": 1000,
        },
        "contestants": [
            {"id": "farmer", "label": "farmer", "kind": "python", "configNote": "default"}
        ],
        "leaderboard": [
            {
                "contestantId": "farmer",
                "avgRank": 1.0,
                "composite": 1.0,
                "economyScore": 1.0,
                "killRate": 1.0,
                "killScore": 1.0,
                "rankScore": 1.0,
                "survivalMedian": 1.0,
                "survivalScore": 1.0,
            }
        ],
        "scenarios": [
            {
                "name": "ffa-std",
                "seedCount": 1,
                "template": {
                    "configNote": "default",
                    "radius": 100,
                    "randomDrop": False,
                    "resources": "std",
                },
                "perEntry": {},
                "matches": [
                    {
                        "seed": 1,
                        "winner": "farmer",
                        "rank": {"farmer": 1},
                        "perPlayer": {
                            "farmer": {
                                "aliveTicks": 100,
                                "beaconTicks": 0,
                                "damageDealt": 0,
                                "deposited": 0,
                                "finalPopulation": 10,
                                "finalResources": 0,
                                "firstKillTick": None,
                                "harvested": 0,
                                "kills": 1,
                                "populationPeak": 10,
                                "unitsLost": 0,
                            }
                        },
                    }
                ],
            }
        ],
    }


def test_produce_leaderboard_data_matches_legacy_conversion_contract() -> None:
    raw = _load_fixture()
    output = produce_leaderboard_data(
        raw,
        source_label=FIXED_SOURCE,
        converted_at=FIXED_TIME,
    )

    assert set(output) == set(LEADERBOARD_DATA_FIELDS)
    assert output["schema"] == REPORT_SCHEMA
    assert output["convertedAt"] == FIXED_TIME
    assert output["source"] == FIXED_SOURCE
    assert output["generatedAt"] == raw["generatedAt"]
    assert output["params"] == raw["params"]
    assert output["scenarioOrder"] == [scenario["name"] for scenario in raw["scenarios"]]

    # Leaderboard rows are 1-based ranks, sorted by composite descending, and
    # carry the per-scenario rank map the web app renders.
    composites = [row["composite"] for row in output["leaderboard"]]
    assert composites == sorted(composites, reverse=True)
    assert [row["rank"] for row in output["leaderboard"]] == list(
        range(1, len(output["leaderboard"]) + 1)
    )
    assert all(
        set(row)
        == {
            "rank",
            "contestantId",
            "composite",
            "avgRank",
            "rankStddev",
            "killRate",
            "killScore",
            "rankScore",
            "economyScore",
            "survivalMedian",
            "survivalScore",
            "scenarioRanks",
        }
        for row in output["leaderboard"]
    )

    # Scenarios keep labels, per-entry data, and resolved matches.
    scenario_by_name = {scenario["name"]: scenario for scenario in output["scenarios"]}
    assert set(scenario_by_name) == {scenario["name"] for scenario in raw["scenarios"]}
    assert scenario_by_name["ffa-dense"]["label"] == "高密度冲突"
    assert scenario_by_name["ffa-std"]["matches"][0]["players"]
    assert "farmer" in scenario_by_name["ffa-std"]["matches"][0]["players"]

    # Entry-level scenario stats are populated and jointly cover every scenario.
    assert output["entryScenarioStats"]
    scenario_names = {scenario["name"] for scenario in raw["scenarios"]}
    entry_scenario_names = {
        name for stats in output["entryScenarioStats"].values() for name in stats
    }
    assert scenario_names <= entry_scenario_names


def test_produce_leaderboard_data_reuses_transform_report_without_drift() -> None:
    raw = _load_fixture()
    produced = produce_leaderboard_data(raw, source_label=FIXED_SOURCE, converted_at=FIXED_TIME)
    transformed = transform_report(raw, source_label=FIXED_SOURCE, converted_at=FIXED_TIME)
    assert produced == transformed


def test_produce_leaderboard_data_defaults_converted_at_to_generated_at() -> None:
    raw = _load_fixture()
    output = produce_leaderboard_data(raw, source_label=FIXED_SOURCE)
    assert output["convertedAt"] == raw["generatedAt"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda report: report.pop("schema"), ValueError),
        (lambda report: report.__setitem__("schema", "arena.bench.report.v2"), ValueError),
        (lambda report: report.pop("contestants"), ValueError),
        (lambda report: report.__setitem__("contestants", []), ValueError),
        (lambda report: report.__setitem__("contestants", "farmer"), ValueError),
        (lambda report: report.pop("scenarios"), ValueError),
        (lambda report: report.__setitem__("scenarios", []), ValueError),
        (lambda report: report.__setitem__("scenarios", "ffa-std"), ValueError),
        (lambda report: report.pop("leaderboard"), ValueError),
        (lambda report: report.__setitem__("leaderboard", []), ValueError),
        (
            lambda report: (
                report.pop("leaderboard"),
                report.pop("leaderboardControl", None),
            ),
            ValueError,
        ),
        (
            lambda report: (
                report.__setitem__("leaderboard", []),
                report.__setitem__("leaderboardControl", []),
            ),
            ValueError,
        ),
        (lambda report: report.pop("generatedAt"), ValueError),
        (lambda report: report.__setitem__("generatedAt", ""), ValueError),
        (lambda report: report.pop("params"), ValueError),
        (lambda report: report.__setitem__("params", {}), ValueError),
        (lambda report: report.__setitem__("params", []), ValueError),
    ],
)
def test_produce_leaderboard_data_fails_closed(
    mutation: Any,
    expected: type[Exception],
) -> None:
    report = copy.deepcopy(_minimal_report())
    mutation(report)
    with pytest.raises(expected):
        produce_leaderboard_data(report, source_label=FIXED_SOURCE, converted_at=FIXED_TIME)


def test_produce_leaderboard_data_rejects_non_mapping_input() -> None:
    non_mapping = cast(Any, ["not", "a", "mapping"])
    with pytest.raises(TypeError):
        produce_leaderboard_data(non_mapping, source_label=FIXED_SOURCE)
