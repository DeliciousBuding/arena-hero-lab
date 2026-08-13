"""Trivial deterministic contestants for FFA smoke tests and wiring checks."""

from __future__ import annotations

from collections import deque

from .config import DIRECTIONS, RANGER, RANGER_RANGE, UNIT_STATS, VANGUARD, unit_cost
from .vision import is_shot_line, shot_intermediate_cells

_DIRECTION_ORDER = tuple(DIRECTIONS)
_MIX = 2654435761  # Knuth multiplicative hash constant

# Deterministic tie-break order for the HunterBot's greedy moves.
_MOVE_ORDER = ("UP", "RIGHT", "DOWN", "LEFT")


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _chebyshev(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


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


class HunterBot:
    """Deterministic aggressive contestant that actually triggers combat.

    The bot keeps a small, fully deterministic cross-tick memory of the passable
    and obstacle cells it has seen, then routes every move with a BFS over that
    known map. When a goal is not yet visible it walks toward the nearest
    exploration frontier in the goal's direction, so units never oscillate in
    obstacle pockets.

    Priority ladder each tick:

    * Combat units (VANGUARD / RANGER) converge on the nearest visible enemy
      core by Chebyshev distance, falling back to the nearest visible enemy
      unit, then to the always-public beacon coordinate as a rendezvous anchor.
    * A RANGER SHOOTs the nearest enemy unit/core on a legal, unobstructed shot
      line within range; otherwise it advances toward the target.
    * A VANGUARD SWEEPs an adjacent enemy unit/core; otherwise it advances.
    * The starting WORKER runs a minimal economy loop: HARVEST a visible natural
      resource, DEPOSIT at the core, otherwise explore the frontier for more.
    * The core SPAWNs VANGUARD / RANGER alternately while it can afford them.
    """

    def __init__(self):
        self._obstacles = set()
        self._empty = set()

    def decide(self, observation):
        self._observe(observation)
        obstacles = observation.obstacles
        enemies = observation.enemies
        enemy_cores = observation.enemy_cores
        core = observation.core
        frontier = self._frontier()

        units = {}
        for unit in observation.units:
            uid = unit["uid"]
            utype = unit["utype"]
            pos = unit["pos"]
            action = None
            if utype == RANGER:
                target = self._shoot_target(pos, enemies, enemy_cores, obstacles)
                if target is not None:
                    action = ("SHOOT", {"expected_cell": target})
                else:
                    action = self._advance(pos, observation, frontier)
            elif utype == VANGUARD:
                direction = self._sweep_direction(pos, enemies, enemy_cores)
                if direction is not None:
                    action = ("SWEEP", {"direction": direction})
                else:
                    action = self._advance(pos, observation, frontier)
            else:  # WORKER
                action = self._worker_action(unit, observation, frontier)
            if action is not None:
                units[uid] = action

        core_action = None
        if core is not None:
            utype = VANGUARD if observation.population % 2 == 1 else RANGER
            cost = unit_cost(UNIT_STATS[utype]["cost"], observation.population)
            if core["resources"] >= cost:
                core_action = ("SPAWN", {"unit_type": utype})

        return {"core": core_action, "units": units}

    # ---- map memory ----

    def _observe(self, observation):
        self._obstacles |= observation.obstacles
        for cell in observation.visible_cells:
            if cell not in observation.obstacles:
                self._empty.add(cell)

    def _frontier(self):
        frontier = set()
        for x, y in self._empty:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (x + dx, y + dy)
                if nxt not in self._empty and nxt not in self._obstacles:
                    frontier.add((x, y))
                    break
        return frontier

    def _bfs_step(self, pos, goals):
        if pos in goals:
            return None
        prev = {pos: None}
        queue = deque([pos])
        while queue:
            cur = queue.popleft()
            if cur in goals:
                step = cur
                while prev[step] != pos:
                    step = prev[step]
                return ("MOVE", {"direction": self._direction_between(pos, step)})
            x, y = cur
            for name in _MOVE_ORDER:
                dx, dy = DIRECTIONS[name]
                nxt = (x + dx, y + dy)
                if nxt in prev or nxt not in self._empty:
                    continue
                prev[nxt] = cur
                queue.append(nxt)
        return None

    @staticmethod
    def _direction_between(a, b):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        for name, (mx, my) in DIRECTIONS.items():
            if (dx, dy) == (mx, my):
                return name
        return None

    # ---- combat helpers ----

    @staticmethod
    def _enemy_positions(enemies, enemy_cores):
        positions = [c["pos"] for c in enemy_cores]
        positions.extend(e["pos"] for e in enemies)
        return positions

    @staticmethod
    def _beacon_pos(observation):
        beacon = observation.beacon.get("position")
        return (beacon[0], beacon[1]) if beacon else None

    def _shoot_target(self, pos, enemies, enemy_cores, obstacles):
        best = None
        for target in self._enemy_positions(enemies, enemy_cores):
            if not is_shot_line(pos[0], pos[1], target[0], target[1], RANGER_RANGE):
                continue
            if any(
                cell in obstacles
                for cell in shot_intermediate_cells(pos[0], pos[1], target[0], target[1])
            ):
                continue
            key = (_chebyshev(pos, target), target)
            if best is None or key < best[0]:
                best = (key, target)
        return best[1] if best is not None else None

    def _sweep_direction(self, pos, enemies, enemy_cores):
        adjacent = set(self._enemy_positions(enemies, enemy_cores))
        x, y = pos
        for name in _MOVE_ORDER:
            dx, dy = DIRECTIONS[name]
            if (x + dx, y + dy) in adjacent:
                return name
        return None

    def _move_target(self, pos, observation):
        cores = [c["pos"] for c in observation.enemy_cores]
        if cores:
            return min(cores, key=lambda p: (_chebyshev(pos, p), p))
        enemies = [e["pos"] for e in observation.enemies]
        if enemies:
            return min(enemies, key=lambda p: (_chebyshev(pos, p), p))
        return self._beacon_pos(observation)

    def _advance(self, pos, observation, frontier):
        target = self._move_target(pos, observation)
        if target is None:
            return None
        if target in self._empty:
            return self._bfs_step(pos, {target})
        if not frontier:
            return None
        best = min(frontier, key=lambda c: (_chebyshev(c, target), c))
        return self._bfs_step(pos, {best})

    def _worker_action(self, unit, observation, frontier):
        pos = unit["pos"]
        core_pos = observation.core["pos"]
        if unit["cargo"] > 0:
            if pos == core_pos:
                return ("DEPOSIT", {})
            return self._bfs_step(pos, {core_pos})
        if pos in observation.resources:
            return ("HARVEST", {})
        if observation.resources:
            nearest = min(observation.resources, key=lambda p: (_manhattan(pos, p), p))
            return self._bfs_step(pos, {nearest})
        if not frontier:
            return None
        best = min(frontier, key=lambda c: (_manhattan(pos, c), c))
        return self._bfs_step(pos, {best})
