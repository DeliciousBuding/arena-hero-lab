"""arena-hero-agent (WuDiWangWaSai, ``codex/mass-army`` branch) adapter: 暴兵流.

Same official-SDK ``CoreFarmer`` entrypoint as the wuwd mainline, but this branch
switches the force-stage ladder to the aggressive mass-army profile
(6/2/2 -> 12/6/8 -> 18/14/16, 48-pop cap) with 4+2 / 2+2 squad pushes and a
stalled-core decapitation loop.  Pinned at commit 0e351f6.
"""

from __future__ import annotations

import arena_farmer as massarmy  # type: ignore


class Adapter:
    def __init__(self) -> None:
        self._tactic = massarmy.CoreFarmer()

    def run_turn(self, turn) -> None:
        self._tactic.choose_actions(turn)


def make_adapter() -> Adapter:
    return Adapter()
