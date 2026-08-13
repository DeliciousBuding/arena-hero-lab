"""Evolve champion heuristic as an FFA contestant.

This shim wires the real evolved heuristic from
``reference/third-party/arena-evolve`` into the FFA host so it can be entered as
a comparison contestant next to ``RandomBot``.

The champion heuristic is *not* a stateless ``Observation -> Plan`` reducer. It
keeps multi-tick memory: obstacle/resource/enemy maps, an A* path cache, and a
large set of per-unit goal/back-off dicts. A faithful contestant therefore wraps
one ``HeuristicStrategy`` instance and forwards every tick's observation to it,
exactly as ``deploy.py --local`` does. The only representational bridge is that
evolve's absolute ``ahsim.*`` imports are pointed at the vendored FFA modules
(``arena_hero_sim.ffa`` is the same engine), so the heuristic runs against the
exact world the FFA host simulates and receives the FFA ``Observation`` directly.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

from .observation import Observation
from .strategy import Plan, Strategy

# arena-hero-lab/packages/arena-hero-sim/src/arena_hero_sim/ffa/evolve_shim.py
# parents: 0=ffa 1=arena_hero_sim 2=src 3=arena-hero-sim 4=packages
#          5=arena-hero-lab 6=arena (repo root)
_ARENA_ROOT = Path(__file__).resolve().parents[6]
_EVOLVE_ROOT = _ARENA_ROOT / "reference" / "third-party" / "arena-evolve"
_DEFAULT_GENES_PATH = _EVOLVE_ROOT / "genes" / "evolve_v7_best.json"

# Standard 256x256 world bounds (matches deploy.py --local and run_ffa default).
DEFAULT_BOUNDS = (-128, 127, -128, 127)

_STRATEGY_PKG = "_arena_evolve_strategies"
_STRATEGIES: types.ModuleType | None = None


def _load_strategies() -> types.ModuleType:
    """Load evolve's strategies.base + strategies.heuristic against FFA modules."""
    global _STRATEGIES
    if _STRATEGIES is not None:
        return _STRATEGIES

    # Point evolve's absolute ``ahsim.*`` imports at the vendored FFA engine so
    # the heuristic shares config/vision/observation with the FFA host.
    from . import config, observation, vision

    sys.modules.setdefault("ahsim", sys.modules[__package__])
    sys.modules.setdefault("ahsim.config", config)
    sys.modules.setdefault("ahsim.observation", observation)
    sys.modules.setdefault("ahsim.vision", vision)

    strategies_dir = _EVOLVE_ROOT / "strategies"
    pkg = types.ModuleType(_STRATEGY_PKG)
    pkg.__path__ = [str(strategies_dir)]
    sys.modules[_STRATEGY_PKG] = pkg

    for name in ("base", "heuristic"):
        fullname = f"{_STRATEGY_PKG}.{name}"
        spec = importlib.util.spec_from_file_location(fullname, strategies_dir / f"{name}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load evolve strategy module: {fullname}")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = _STRATEGY_PKG
        sys.modules[fullname] = module
        setattr(pkg, name, module)
        spec.loader.exec_module(module)

    _STRATEGIES = pkg
    return pkg


class EvolveHeuristicStrategy(Strategy):
    """FFA ``Strategy`` wrapping the evolve v7 champion heuristic.

    The underlying ``HeuristicStrategy`` keeps state across ticks, so one
    instance must be reused for an entire match (which ``run_ffa`` already does:
    it holds the same strategy object per contestant for all ticks).
    """

    name = "evolve_v7_best"

    def __init__(self, genes: dict | None = None, *, bounds=None, genes_path=None):
        strategies = _load_strategies()
        self._HeuristicStrategy = strategies.heuristic.HeuristicStrategy
        if genes is None:
            path = Path(genes_path) if genes_path is not None else _DEFAULT_GENES_PATH
            genes = json.loads(path.read_text(encoding="utf-8"))
        self.genes = dict(genes)
        self.bounds = DEFAULT_BOUNDS if bounds is None else bounds
        self._impl = self._HeuristicStrategy(genes=self.genes, bounds=self.bounds)

    def decide(self, observation: Observation) -> Plan:
        return self._impl.decide(observation)

    def reset(self) -> None:
        self._impl.reset()

    def reset_transient(self) -> None:
        self._impl.reset_transient()

