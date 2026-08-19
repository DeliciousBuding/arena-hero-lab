"""Regression tests for the state-seed replay parser (scripts/replay_state_seed.py).

The parser lives in the scripts directory (not a package), so it is imported
via a sys.path entry.  These tests pin the dual-form cell parsing, role
aliases, explicit degradation warnings, and world-size growth.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# The parser lives in scripts/ (not a package); import it as a module by path.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
rss = importlib.import_module("replay_state_seed")


def _base_record() -> dict:
    return {
        "tick": 123,
        "tenantId": "t",
        "recordType": "tick_state",
        "population": 1,
        "resources": 9,
        "resourceCells": ["3,3"],
        "terrainObstacles": ["1,1", "2,1"],
        "units": [{"id": "u", "role": "worker", "pos": [2, 2], "hp": 2, "cargo": 1}],
        "core": {"position": [2, 3], "hp": 4, "shield": 6},
        "beacon": {"position": [0, 0], "status": "ground"},
    }


def test_parse_dual_form_cells_and_roles() -> None:
    record = _base_record()
    record["terrainObstacles"] = ["1,1", [2, 1], {"x": 3, "y": 1}]
    record["resourceCells"] = [[3, 3], "4,4"]
    record["units"] = [
        {"role": "worker", "pos": [2, 2], "hp": 2, "cargo": 1},
        {"role": "RANGER", "pos": "2,3", "hp": 1},
        {"role": "vanguard", "pos": {"x": 2, "y": 4}},
    ]

    parsed = rss.parse_tick_state(record)

    assert parsed.obstacles == [(1, 1), (2, 1), (3, 1)]
    assert parsed.resource_cells == [(3, 3), (4, 4)]
    assert [u.utype for u in parsed.units] == ["WORKER", "RANGER", "VANGUARD"]
    assert parsed.units[1].pos == (2, 3)
    assert parsed.units[1].hp == 1
    assert parsed.units[1].cargo == 0
    assert parsed.units[2].pos == (2, 4)
    assert parsed.core_pos == (2, 3)
    assert parsed.core_hp == 4
    assert parsed.core_shield == 6
    assert parsed.core_resources == 9
    assert parsed.beacon_ground == (0, 0)


def test_parse_warns_on_unmapped_and_malformed_fields() -> None:
    record = _base_record()
    record["plan"] = {"core": None}
    record["deciderState"] = {"mode": "economy"}
    record["events"] = [{"kind": "UNIT_DEPOSITED"}]
    record["terrainObstacles"] = ["not-a-cell"]
    record["units"] = [{"role": "unknown-role", "pos": [2, 2]}]

    parsed = rss.parse_tick_state(record)

    text = "\n".join(parsed.warnings)
    assert "plan" in text
    assert "deciderState" in text
    assert "events" in text
    assert "terrainObstacles" in text
    assert "unknown-role" in text
    assert parsed.obstacles == []
    assert parsed.units == []


def test_parse_warns_on_missing_core_fields_and_population_mismatch() -> None:
    record = _base_record()
    del record["core"]["hp"]
    del record["core"]["shield"]
    del record["resources"]
    record["population"] = 99

    parsed = rss.parse_tick_state(record)

    text = "\n".join(parsed.warnings)
    assert "core.hp missing" in text
    assert "core.shield missing" in text
    assert "resources" in text
    assert "population=99" in text
    assert parsed.core_hp is None
    assert parsed.core_shield is None
    assert parsed.core_resources is None
    assert parsed.population == 99


def test_parse_grows_world_size_for_far_coordinates() -> None:
    record = _base_record()
    record["units"] = [{"role": "worker", "pos": [150, -3]}]

    parsed = rss.parse_tick_state(record, base_size=256)

    assert parsed.world_size >= 302
    assert any("growing world size" in warning for warning in parsed.warnings)


def test_parse_rejects_record_without_core_position() -> None:
    record = _base_record()
    record["core"] = {"hp": 5}

    try:
        rss.parse_tick_state(record)
    except ValueError as exc:
        assert "core.position" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing core.position")


def test_parse_beacon_carried_is_degraded_with_warning() -> None:
    record = _base_record()
    record["beacon"] = {"status": "carried", "carrier_id": "u"}

    parsed = rss.parse_tick_state(record)

    assert parsed.beacon_ground is None
    assert any("carried" in warning for warning in parsed.warnings)


def test_make_sample_records_parse_cleanly() -> None:
    records = rss.make_sample_records()

    assert len(records) == 2
    parsed_full = rss.parse_tick_state(records[0])
    assert parsed_full.core_pos == (5, 5)
    assert len(parsed_full.units) == 3
    assert parsed_full.beacon_ground == (0, 0)

    parsed_minimal = rss.parse_tick_state(records[1])
    assert parsed_minimal.core_pos == (-3, -2)
    assert parsed_minimal.units[0].pos == (-3, -3)
    assert parsed_minimal.obstacles == [(-1, -1)]
