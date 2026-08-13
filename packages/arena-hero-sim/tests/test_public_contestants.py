"""Public leaderboard roster: third-party + controls, excluding our own."""

from __future__ import annotations

from arena_hero_sim.ffa.public_contestants import (
    PUBLIC_ROSTER,
    build_public_leaderboard_contestants,
)


def test_roster_has_eight_public_ids() -> None:
    assert PUBLIC_ROSTER == (
        "evolve",
        "drew-z",
        "guide",
        "waaiging",
        "tactic",
        "wuwd",
        "rand",
        "wait",
    )
    assert len(PUBLIC_ROSTER) == 8


def test_roster_excludes_internal_contestants() -> None:
    assert "python" not in PUBLIC_ROSTER
    assert "hunter" not in PUBLIC_ROSTER


def test_build_returns_roster_and_five_sdk_strategies() -> None:
    contestants, sdk = build_public_leaderboard_contestants()
    assert set(contestants) == set(PUBLIC_ROSTER)
    assert len(sdk) == 5
    for strategy in sdk:
        strategy.close()
