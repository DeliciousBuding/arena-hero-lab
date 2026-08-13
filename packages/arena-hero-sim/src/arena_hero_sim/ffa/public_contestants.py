"""Public leaderboard roster: third-party agents + deterministic controls.

The public leaderboard deliberately excludes our own ``arena-hero-agent``
(``python``) and ``HunterBot`` (``hunter``) so we are never both referee and
competitor.  It contains the five public third-party agents plus two
deterministic control bots.
"""

from __future__ import annotations

from .contestants import RandomBot, WaitStrategy
from .evolve_shim import EvolveHeuristicStrategy
from .sdk_agent_shim import SdkAgentStrategy
from .strategy import Strategy

PUBLIC_ROSTER: tuple[str, ...] = (
    "evolve",
    "drew-z",
    "guide",
    "waaiging",
    "tactic",
    "rand",
    "wait",
)

_SDK_IDS: tuple[str, ...] = ("drew-z", "guide", "waaiging", "tactic")


def build_public_leaderboard_contestants() -> tuple[dict[str, Strategy], list[SdkAgentStrategy]]:
    """Return the public roster plus the SDK strategies that need ``close()``.

    The SDK strategies spawn a subprocess lazily on first ``decide``; callers
    must ``close()`` them after a match to avoid orphan processes.
    """

    contestants: dict[str, Strategy] = {
        "evolve": EvolveHeuristicStrategy(),
        "drew-z": SdkAgentStrategy("drew-z"),
        "guide": SdkAgentStrategy("guide"),
        "waaiging": SdkAgentStrategy("waaiging"),
        "tactic": SdkAgentStrategy("tactic"),
        "rand": RandomBot(),
        "wait": WaitStrategy(),
    }
    sdk = [contestants[agent_id] for agent_id in _SDK_IDS]
    return contestants, sdk


__all__ = ["PUBLIC_ROSTER", "build_public_leaderboard_contestants"]
