"""Interactive FFA contestant contract: observation in, plan out.

The vendored ahsim engine resolves a per-tick plan of the shape
{"core": (action_type, kwargs) | None, "units": {uid: (action_type, kwargs)}}
against one shared world. A Strategy is any object exposing decide.
"""

from __future__ import annotations

from typing import Protocol

from .observation import Observation

type UnitAction = tuple[str, dict[str, object]]
type CoreAction = tuple[str, dict[str, object]]
type Plan = dict[str, object]  # {"core": CoreAction | None, "units": dict[int, UnitAction]}


class Strategy(Protocol):
    """A contestant that maps a per-tick observation to a plan.

    observation is the player's visibility-limited view (Observation). The
    returned plan uses raw object ids (ints) for unit actions; the engine
    validates each action lazily, so an incompatible action degrades to a no-op
    instead of raising.
    """

    def decide(self, observation: Observation) -> Plan:
        """Return {"core": CoreAction | None, "units": {uid: UnitAction}}."""
        ...
