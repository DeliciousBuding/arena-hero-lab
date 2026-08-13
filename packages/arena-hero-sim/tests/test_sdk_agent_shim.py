"""Integration wiring for one official-SDK third-party agent (guide)."""

from __future__ import annotations

import pytest

from arena_hero_sim.ffa.observation import Observation
from arena_hero_sim.ffa.sdk_agent_shim import AGENT_SPECS, SdkAgentStrategy, discover_sdk_python

pytestmark = pytest.mark.skipif(
    discover_sdk_python() is None,
    reason="arena-hero SDK venv not present; set ARENA_HERO_SDK_PYTHON to enable",
)


def _core(**overrides) -> dict:
    core = {"uid": 123, "pos": (5, 5), "hp": 5, "shield": 5, "resources": 12, "migration": None}
    core.update(overrides)
    return core


def _obs(tick: int = 10) -> Observation:
    return Observation(
        player_id=0,
        tick=tick,
        core=_core(),
        units=[
            {
                "uid": 456,
                "utype": "WORKER",
                "pos": (5, 5),
                "hp": 2,
                "cargo": 0,
                "carries_beacon": False,
            }
        ],
        enemies=[],
        enemy_cores=[],
        resources={(9, 9), (11, 9)},
        obstacles={(1, 1)},
        beacon={"position": [0, 0], "status": "UNKNOWN"},
        population=1,
    )


def test_unknown_agent_raises() -> None:
    with pytest.raises(KeyError):
        SdkAgentStrategy("does-not-exist")


def test_guide_spec_is_registered() -> None:
    assert "guide" in AGENT_SPECS


def test_guide_decides_and_maps_plan() -> None:
    strategy = SdkAgentStrategy("guide")
    try:
        plan = strategy.decide(_obs(tick=10))
        assert isinstance(plan, dict)
        assert "core" in plan and "units" in plan
        assert all(isinstance(uid, int) for uid in plan["units"])
    finally:
        strategy.close()
