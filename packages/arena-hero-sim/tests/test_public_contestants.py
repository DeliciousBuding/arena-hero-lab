"""Public leaderboard roster: third-party + controls, excluding our own."""

from __future__ import annotations

from pathlib import Path

import pytest

from arena_hero_sim.ffa.public_contestants import (
    PUBLIC_ROSTER,
    build_public_leaderboard_contestants,
)

_EVOLVE_GENES = (
    Path(__file__).resolve().parents[4]
    / "reference"
    / "third-party"
    / "arena-evolve"
    / "genes"
    / "evolve_v7_best.json"
)


def test_roster_has_nine_public_ids() -> None:
    assert PUBLIC_ROSTER == (
        "evolve",
        "drew-z",
        "guide",
        "waaiging",
        "tactic",
        "wuwd",
        "massarmy",
        "rand",
        "wait",
    )
    assert len(PUBLIC_ROSTER) == 9


def test_roster_excludes_internal_contestants() -> None:
    assert "python" not in PUBLIC_ROSTER
    assert "hunter" not in PUBLIC_ROSTER


@pytest.mark.skipif(
    not _EVOLVE_GENES.is_file(),
    reason="reference/third-party/arena-evolve not present (vendored workspace dependency)",
)
def test_build_returns_roster_and_six_sdk_strategies() -> None:
    contestants, sdk = build_public_leaderboard_contestants()
    assert set(contestants) == set(PUBLIC_ROSTER)
    assert len(sdk) == 6
    for strategy in sdk:
        strategy.close()
