"""Pure-Python optimized backend with immutable visibility-cache boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from arena_hero_sim.backend import BackendDescriptor
from arena_hero_sim.contracts import BackendCapabilities, SimulationRequest
from arena_hero_sim.reference import (
    REFERENCE_FEATURES,
    REFERENCE_PROTOCOL_VERSION,
    ReferenceEngineBackend,
)
from arena_hero_sim.reference_contracts import (
    REFERENCE_RULES,
    Position,
    ReferenceActionKind,
    ReferenceObservation,
    ReferenceRules,
    ReferenceScenario,
    ReferenceWorld,
)
from arena_hero_sim.reference_engine import (
    ReferenceEpisodeResult,
    UnsupportedReferenceSliceError,
    _supercover_line,
    run_reference_episode,
)

OPTIMIZED_BACKEND_ID = "optimized-python-v1"
OPTIMIZED_ENGINE_VERSION = "0.1.0"
OPTIMIZED_VISIBILITY_FEATURE = "optimized-static-visibility-cache-v1"


@dataclass(frozen=True, slots=True)
class _VisibilityCacheKey:
    width: int
    height: int
    obstacles: tuple[Position, ...]
    origin: Position
    radius: int


class _StaticVisibilityCache:
    """Cache static geometry without exposing mutable state to public results."""

    def __init__(self) -> None:
        self._rays_by_radius: dict[int, tuple[tuple[Position, tuple[Position, ...]], ...]] = {}
        self._visible_by_key: dict[_VisibilityCacheKey, frozenset[Position]] = {}

    @property
    def entry_count(self) -> int:
        return len(self._visible_by_key)

    def visible_from(
        self,
        *,
        width: int,
        height: int,
        obstacles: frozenset[Position],
        origin: Position,
        radius: int,
    ) -> frozenset[Position]:
        key = _VisibilityCacheKey(
            width=width,
            height=height,
            obstacles=tuple(sorted(obstacles)),
            origin=origin,
            radius=radius,
        )
        cached = self._visible_by_key.get(key)
        if cached is not None:
            return cached
        visible: set[Position] = set()
        for offset, relative_line in self._rays(radius):
            target = (origin[0] + offset[0], origin[1] + offset[1])
            absolute_line = (
                (origin[0] + cell[0], origin[1] + cell[1]) for cell in relative_line[:-1]
            )
            if not any(cell in obstacles for cell in absolute_line):
                visible.add(target)
        result = frozenset(visible)
        self._visible_by_key[key] = result
        return result

    def _rays(self, radius: int) -> tuple[tuple[Position, tuple[Position, ...]], ...]:
        cached = self._rays_by_radius.get(radius)
        if cached is not None:
            return cached
        rays: list[tuple[Position, tuple[Position, ...]]] = []
        origin = (0, 0)
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if abs(dx) + abs(dy) <= radius:
                    target = (dx, dy)
                    rays.append((target, _supercover_line(origin, target)))
        result = tuple(rays)
        self._rays_by_radius[radius] = result
        return result


class OptimizedEngineBackend(ReferenceEngineBackend):
    """Semantically equivalent backend optimizing only static visibility geometry."""

    _descriptor = BackendDescriptor(
        backend_id=OPTIMIZED_BACKEND_ID,
        engine_version=OPTIMIZED_ENGINE_VERSION,
        capabilities=BackendCapabilities(
            protocol_versions=(REFERENCE_PROTOCOL_VERSION,),
            features=REFERENCE_FEATURES | frozenset({OPTIMIZED_VISIBILITY_FEATURE}),
            execution_modes=frozenset({"in-process"}),
            max_batch_size=1024,
            supports_batch=True,
            supports_incremental_world_hash=False,
            supports_zero_copy=False,
            interchange_formats=frozenset({"canonical-json"}),
        ),
    )

    def __init__(self, scenarios: tuple[ReferenceScenario, ...]) -> None:
        super().__init__(scenarios)
        self._visibility_cache = _StaticVisibilityCache()

    def execute(self, request: SimulationRequest) -> ReferenceEpisodeResult:
        problem = self._support_problem(request)
        if problem is not None:
            raise UnsupportedReferenceSliceError(problem)
        assert request.input_artifact_sha256 is not None
        scenario = self._scenarios[request.input_artifact_sha256]
        return run_reference_episode(
            scenario,
            request_id=request.request_id,
            episode_id=request.episode_id,
            max_ticks=request.config.max_ticks,
            observer=self._observe_world,
        )

    def _observe_world(
        self, world: ReferenceWorld, rules: ReferenceRules = REFERENCE_RULES
    ) -> tuple[ReferenceObservation, ...]:
        observations: list[ReferenceObservation] = []
        terrain = world.terrain
        positions = {(0, 0), *terrain.obstacles, *terrain.resource_cells}
        for player in world.players:
            positions.add(player.core.position)
            positions.update(unit.position for unit in player.units)
        xs = tuple(position[0] for position in positions)
        ys = tuple(position[1] for position in positions)
        width = max(xs) - min(xs) + 1
        height = max(ys) - min(ys) + 1
        for player in world.players:
            visible = set(
                self._visibility_cache.visible_from(
                    width=width,
                    height=height,
                    obstacles=terrain.obstacles,
                    origin=player.core.position,
                    radius=rules.core_vision_radius,
                )
            )
            for unit in player.units:
                visible.update(
                    self._visibility_cache.visible_from(
                        width=width,
                        height=height,
                        obstacles=terrain.obstacles,
                        origin=unit.position,
                        radius=rules.worker_vision_radius,
                    )
                )
            legal = tuple(
                (unit.id, tuple(action.value for action in ReferenceActionKind))
                for unit in player.units
            )
            observations.append(ReferenceObservation(player.id, tuple(sorted(visible)), legal))
        return tuple(observations)
