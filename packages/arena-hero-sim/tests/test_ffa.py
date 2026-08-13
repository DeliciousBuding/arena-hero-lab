"""Acceptance for the vendored interactive free-for-all (FFA) host.

These tests pin the three requirements of the ahsim vendor task: deterministic
content addressing, a two-contestant smoke match with a real world-state trace,
and terminal-table schema validation that downstream head_to_head/melee
extractors can consume without importing arena_hero_bench.
"""

from __future__ import annotations

from typing import Any, cast

from arena_hero_sim.ffa import (
    FFA_REPORT_SCHEMA,
    FfaReport,
    RandomBot,
    WaitStrategy,
    run_ffa,
)
from arena_hero_sim.serialization import content_sha256

_REQUIRED_TERMINAL_KEYS = (
    "survival_alive",
    "core_hp",
    "final_resources",
    "population_final",
    "unit_count_final",
)


def _smoke() -> FfaReport:
    return run_ffa(
        {"wait": WaitStrategy(), "rand": RandomBot()},
        seed=7,
        ticks=24,
    )


def _unit_pos(frame: dict[str, object], contestant: str) -> tuple[int, int]:
    players = cast(Any, frame["players"])
    pos = players[contestant]["units"][0]["pos"]
    return (pos[0], pos[1])


def test_ffa_run_is_deterministic_across_repeated_executions() -> None:
    first = _smoke()
    second = _smoke()

    assert first.artifact_sha256 == second.artifact_sha256
    assert first.artifact == second.artifact
    assert first.terminal == second.terminal


def test_ffa_content_address_is_recomputed_from_the_artifact() -> None:
    report = _smoke()

    assert report.schema_version == FFA_REPORT_SCHEMA
    assert report.artifact_sha256 == content_sha256(report.artifact)
    assert report.to_json() == {**dict(report.artifact), "artifact_sha256": report.artifact_sha256}


def test_ffa_smoke_runs_two_contestants_in_one_shared_world() -> None:
    report = _smoke()

    assert report.contestant_ids == ("rand", "wait")
    assert report.ticks == 24
    # tick 0 plus one frame per resolved tick
    assert len(report.trace) == 25
    assert all(frame["tick"] == index for index, frame in enumerate(report.trace))
    assert all(frame["players"] for frame in report.trace)

    # The random bot moves in the shared world; the wait bot stays put.
    rand_start = _unit_pos(report.trace[0], "rand")
    wait_start = _unit_pos(report.trace[0], "wait")
    assert any(_unit_pos(frame, "rand") != rand_start for frame in report.trace)
    assert _unit_pos(report.trace[-1], "wait") == wait_start


def test_ffa_maze_stress_remote_spawn_is_deterministic() -> None:
    """High-obstacle + far-ring spawn reproduces the t1 maze profile.

    The default bench (density 0.225, center spawn) cannot expose the
    t1 production failure; this scenario is the fidelity knob that does.
    """

    def run_maze() -> FfaReport:
        return run_ffa(
            {"wait": WaitStrategy(), "rand": RandomBot()},
            seed=11,
            ticks=24,
            obstacle_density=0.5,
            spawn_center=(-96, 128),
        )

    first = run_maze()
    second = run_maze()
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.artifact == second.artifact
    # Remote spawn center is honored: units start far from the origin.
    for contestant in ("rand", "wait"):
        x, y = _unit_pos(first.trace[0], contestant)
        assert abs(x) + abs(y) > 100


def test_ffa_terminal_table_has_required_fields_for_every_contestant() -> None:
    report = _smoke()

    assert len(report.terminal) == 2
    by_id = {entry.contestant_id: entry for entry in report.terminal}
    assert set(by_id) == set(report.contestant_ids)

    for entry in report.terminal:
        payload = entry.to_json()
        for key in _REQUIRED_TERMINAL_KEYS:
            assert key in payload
        assert isinstance(payload["survival_alive"], bool)
        for key in ("core_hp", "final_resources", "population_final", "unit_count_final"):
            assert isinstance(payload[key], int)
            assert not isinstance(payload[key], bool)
