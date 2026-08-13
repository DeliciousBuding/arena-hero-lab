"""arena-hero-guide adapter (official SDK ``plan_turn``)."""

from __future__ import annotations

import arena_core_agent as guide


class Adapter:
    def __init__(self) -> None:
        self._memory = guide.AgentMemory()

    def run_turn(self, turn) -> None:
        guide.plan_turn(turn, self._memory)


def make_adapter() -> Adapter:
    return Adapter()
