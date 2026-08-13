"""arena-hero-agent (Drew-Z) adapter (official SDK ``CoreFarmer``)."""

from __future__ import annotations

import arena_farmer as drew_z


class Adapter:
    def __init__(self) -> None:
        self._tactic = drew_z.CoreFarmer()

    def run_turn(self, turn) -> None:
        self._tactic.choose_actions(turn)


def make_adapter() -> Adapter:
    return Adapter()
