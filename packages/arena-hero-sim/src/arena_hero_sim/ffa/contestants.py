"""Trivial deterministic contestants for FFA smoke tests and wiring checks."""

from __future__ import annotations

from .config import DIRECTIONS

_DIRECTION_ORDER = tuple(DIRECTIONS)
_MIX = 2654435761  # Knuth multiplicative hash constant


class WaitStrategy:
    """Contestant that never acts: every unit and the core wait each tick."""

    def decide(self, observation):
        return {"core": None, "units": {}}


class RandomBot:
    """Deterministic pseudo-random mover.

    Each unit picks a direction from a stable function of (tick, uid), so the
    same seed always yields the same movement trace. It deliberately keeps no
    stored RNG state, which makes it order-independent and reproducible.
    """

    def decide(self, observation):
        units = {}
        for unit in observation.units:
            uid = unit["uid"]
            direction = _DIRECTION_ORDER[(uid + observation.tick * _MIX) % 4]
            units[uid] = ("MOVE", {"direction": direction})
        return {"core": None, "units": units}
