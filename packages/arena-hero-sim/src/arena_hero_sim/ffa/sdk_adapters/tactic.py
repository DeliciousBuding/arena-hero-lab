"""arena-hero-tactic adapter (official SDK ``bot.strategy.decide``)."""

from __future__ import annotations

import bot.strategy as tactic


class Adapter:
    def run_turn(self, turn) -> None:
        tactic.decide(turn)


def make_adapter() -> Adapter:
    return Adapter()
