"""arena-hero-clone-waaiging adapter (official SDK ``SmartTactic``)."""

from __future__ import annotations

import arena_hero_strategy as waaiging


class Adapter:
    def __init__(self) -> None:
        self._tactic = waaiging.SmartTactic()

    def run_turn(self, turn) -> None:
        self._tactic.choose_actions(turn)


def make_adapter() -> Adapter:
    return Adapter()
