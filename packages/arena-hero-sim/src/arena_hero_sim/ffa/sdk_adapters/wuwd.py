"""arena-hero-agent (WuDiWangWaSai) adapter: resource-first ``CoreFarmer``.

Fork lineage of Drew-Z's ``arena-hero-agent``, maintained by WuDiWangWaSai.  It
keeps the same official-SDK ``CoreFarmer`` entrypoint but evolves the economy
posture (default worker target 23 vs Drew-Z's 12) and expands the resource
strategy.  Pinned at commit c2531fb.
"""

from __future__ import annotations

import arena_farmer as wuwd


class Adapter:
    def __init__(self) -> None:
        self._tactic = wuwd.CoreFarmer()

    def run_turn(self, turn) -> None:
        self._tactic.choose_actions(turn)


def make_adapter() -> Adapter:
    return Adapter()
