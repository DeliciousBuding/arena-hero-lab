"""Public third-party agent registry for the fair leaderboard.

The leaderboard only ranks *public* third-party agents plus deterministic control
bots.  Our own ``arena-hero-agent`` (``python``) and ``HunterBot`` (``hunter``)
are internal research contestants and are deliberately excluded from the public
board so we are never both referee and competitor.

Every entry is data, not code: it records the agent repo, its decision entrypoint,
the bridge mode (``ahsim`` for evolve's absolute-import strategy, ``sdk`` for the
official ``arena-hero`` SDK agents), and the pinned SDK package.  The registry is
fail-closed: :func:`validate_registry` raises if a referenced repo or entrypoint
is missing so a broken contestant cannot silently vanish from the board.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

# arena-hero-lab/packages/arena-hero-bench/src/arena_hero_bench/third_party_registry.py
# parents: 0=arena_hero_bench 1=src 2=arena-hero-bench 3=packages 4=arena-hero-lab 5=arena
_ARENA_ROOT = Path(__file__).resolve().parents[5]

THIRD_PARTY_DIR: Final = "reference/third-party"


@dataclass(frozen=True, slots=True)
class ThirdPartyAgent:
    """One public third-party contestant entry."""

    id: str
    display_name: str
    repo: str
    entrypoint: str
    bridge: str
    sdk: str
    note: str = ""

    def repo_path(self, root: Path | None = None) -> Path:
        return (root or _ARENA_ROOT) / self.repo

    def entrypoint_path(self, root: Path | None = None) -> Path:
        return self.repo_path(root) / self.entrypoint


THIRD_PARTY_AGENTS: Final[tuple[ThirdPartyAgent, ...]] = (
    ThirdPartyAgent(
        id="evolve",
        display_name="arena-evolve",
        repo=f"{THIRD_PARTY_DIR}/arena-evolve",
        entrypoint="strategies/heuristic.py",
        bridge="ahsim",
        sdk="n/a (ahsim shim)",
        note="进化冠军 evolve_v7_best",
    ),
    ThirdPartyAgent(
        id="drew-z",
        display_name="arena-hero-agent (Drew-Z)",
        repo=f"{THIRD_PARTY_DIR}/arena-hero-agent",
        entrypoint="arena_farmer.py",
        bridge="sdk",
        sdk="arena-hero",
        note="官方 SDK farmer",
    ),
    ThirdPartyAgent(
        id="waaiging",
        display_name="arena-hero-clone-waaiging",
        repo=f"{THIRD_PARTY_DIR}/arena-hero-clone-waaiging",
        entrypoint="arena_hero_strategy.py",
        bridge="sdk",
        sdk="arena-hero",
        note="10k 行战术 SmartTactic",
    ),
    ThirdPartyAgent(
        id="guide",
        display_name="arena-hero-guide",
        repo=f"{THIRD_PARTY_DIR}/arena-hero-guide",
        entrypoint="arena_core_agent.py",
        bridge="sdk",
        sdk="arena-hero",
        note="官方 SDK core agent",
    ),
    ThirdPartyAgent(
        id="tactic",
        display_name="arena-hero-tactic",
        repo=f"{THIRD_PARTY_DIR}/arena-hero-tactic",
        entrypoint="bot/strategy.py",
        bridge="sdk",
        sdk="arena-hero",
        note="均衡防守战术",
    ),
)

CONTROL_IDS: Final[tuple[str, ...]] = ("rand", "wait")
INTERNAL_CONTESTANT_IDS: Final[tuple[str, ...]] = ("python", "hunter")

#: Contestant ids that may appear on the public leaderboard (5 third-party + 2 controls).
PUBLIC_LEADERBOARD_IDS: Final[tuple[str, ...]] = (
    tuple(agent.id for agent in THIRD_PARTY_AGENTS) + CONTROL_IDS
)


def validate_registry(root: Path | None = None) -> tuple[Path, ...]:
    """Resolve every third-party entrypoint, raising if any is missing.

    Fail-closed: a broken or moved reference repository must fail the leaderboard
    run loudly rather than silently dropping a contestant.
    """

    base = root or _ARENA_ROOT
    paths: list[Path] = []
    for agent in THIRD_PARTY_AGENTS:
        repo = agent.repo_path(base)
        if not repo.is_dir():
            raise FileNotFoundError(f"third-party repo missing: {repo}")
        entry = agent.entrypoint_path(base)
        if not entry.is_file():
            raise FileNotFoundError(f"third-party entrypoint missing: {entry}")
        paths.append(entry)
    return tuple(paths)


__all__ = [
    "CONTROL_IDS",
    "INTERNAL_CONTESTANT_IDS",
    "PUBLIC_LEADERBOARD_IDS",
    "THIRD_PARTY_AGENTS",
    "THIRD_PARTY_DIR",
    "ThirdPartyAgent",
    "validate_registry",
]
