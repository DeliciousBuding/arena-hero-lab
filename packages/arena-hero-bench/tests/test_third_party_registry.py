"""Fail-closed public third-party registry for the fair leaderboard."""

from __future__ import annotations

from pathlib import Path

import pytest

from arena_hero_bench.third_party_registry import (
    INTERNAL_CONTESTANT_IDS,
    PUBLIC_LEADERBOARD_IDS,
    THIRD_PARTY_AGENTS,
    ThirdPartyAgent,
    validate_registry,
)


def test_registry_has_six_unique_third_party_agents() -> None:
    ids = [agent.id for agent in THIRD_PARTY_AGENTS]
    assert len(ids) == 6
    assert len(set(ids)) == len(ids)


def test_public_leaderboard_excludes_internal_contestants() -> None:
    assert set(INTERNAL_CONTESTANT_IDS) == {"python", "hunter"}
    assert "python" not in PUBLIC_LEADERBOARD_IDS
    assert "hunter" not in PUBLIC_LEADERBOARD_IDS
    # 6 third-party + rand + wait
    assert len(PUBLIC_LEADERBOARD_IDS) == 8


def test_registry_entrypoints_resolve() -> None:
    paths = validate_registry()
    assert len(paths) == len(THIRD_PARTY_AGENTS)
    assert all(path.is_file() for path in paths)


def test_registry_is_frozen_and_typed() -> None:
    for agent in THIRD_PARTY_AGENTS:
        assert isinstance(agent, ThirdPartyAgent)
        assert agent.bridge in {"ahsim", "sdk"}
        assert agent.id and agent.entrypoint


def test_validate_registry_fails_closed_on_missing_repo(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        validate_registry(root=tmp_path)
