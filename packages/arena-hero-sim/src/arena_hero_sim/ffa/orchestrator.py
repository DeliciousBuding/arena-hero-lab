"""Interactive free-for-all host: run N contestants in one shared ahsim world."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

from .config import RESOURCE_REPLENISH_EVERY
from .game import Game
from .strategy import Strategy

FFA_REPORT_SCHEMA: Final = "arena.sim.ffa-report.v1"
GENERATOR_VERSION: Final = "0.2.0"


def _ordered_contestants(
    contestants: Mapping[str, Strategy] | Sequence[tuple[str, Strategy]],
) -> list[tuple[str, Strategy]]:
    items = list(contestants.items()) if isinstance(contestants, Mapping) else list(contestants)
    ordered: list[tuple[str, Strategy]] = []
    for contestant_id, strategy in items:
        if not isinstance(contestant_id, str) or not contestant_id.strip():
            raise ValueError("contestant ids must be non-empty strings")
        ordered.append((contestant_id, strategy))
    if len(ordered) < 2:
        raise ValueError("run_ffa requires at least 2 contestants")
    ids = [contestant_id for contestant_id, _ in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("contestant ids must be unique")
    # Stable player_id assignment is independent of input order.
    ordered.sort(key=lambda item: item[0])
    return ordered


def _beacon_json(game: Game) -> dict[str, object]:
    if game.beacon[0] == "ground":
        return {
            "status": "ground",
            "position": [game.beacon[1], game.beacon[2]],
            "carrier_uid": None,
        }
    return {
        "status": "carried",
        "position": None,
        "carrier_uid": game.beacon[1],
    }


def _frame(
    game: Game,
    tick: int,
    contestant_ids: list[str],
    player_id_of: dict[str, int],
) -> dict[str, object]:
    players: dict[str, object] = {}
    for contestant_id in contestant_ids:
        player = game.players[player_id_of[contestant_id]]
        units = [
            {
                "uid": unit.uid,
                "utype": unit.utype,
                "pos": [unit.pos[0], unit.pos[1]],
                "hp": unit.hp,
                "cargo": unit.cargo,
            }
            for unit in sorted(player.units.values(), key=lambda u: u.uid)
        ]
        core = None
        if player.core is not None:
            core = {
                "uid": player.core.uid,
                "pos": [player.core.pos[0], player.core.pos[1]],
                "hp": player.core.hp,
                "shield": player.core.shield,
                "resources": player.core.resources,
            }
        players[contestant_id] = {
            "alive": player.core is not None,
            "core": core,
            "units": units,
            "population": player.population,
            "stats": dict(sorted(player.stats.items())),
        }
    return {
        "tick": tick,
        "event_count": len(game.last_events),
        "beacon": _beacon_json(game),
        "players": players,
    }


@dataclass(frozen=True, slots=True)
class FfaTerminal:
    """Terminal outcome of one contestant, named to match the bench extractor."""

    contestant_id: str
    survival_alive: bool
    core_hp: int
    core_shield: int
    final_resources: int
    resource_growth: int
    population_final: int
    unit_count_final: int
    cargo_final: int
    respawn_count: int
    ticks_alive: int
    stats: Mapping[str, int]
    strategy_errors: int = 0
    strategy_last_error: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "contestant": self.contestant_id,
            "survival_alive": self.survival_alive,
            "core_hp": self.core_hp,
            "core_shield": self.core_shield,
            "final_resources": self.final_resources,
            "resource_growth": self.resource_growth,
            "population_final": self.population_final,
            "unit_count_final": self.unit_count_final,
            "cargo_final": self.cargo_final,
            "respawn_count": self.respawn_count,
            "ticks_alive": self.ticks_alive,
            "stats": dict(sorted(self.stats.items())),
            "strategy_errors": self.strategy_errors,
            "strategy_last_error": self.strategy_last_error,
        }


@dataclass(frozen=True, slots=True)
class FfaReport:
    """Deterministic, content-addressed free-for-all match report."""

    schema_version: str
    generator_version: str
    seed: int
    ticks: int
    contestant_ids: tuple[str, ...]
    terminal: tuple[FfaTerminal, ...]
    trace: list[dict[str, object]]
    artifact: Mapping[str, JsonValue]
    artifact_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        return {**dict(self.artifact), "artifact_sha256": self.artifact_sha256}


def _terminal(
    contestant_id: str,
    player,
    initial_resources: int,
    strategy_errors: int = 0,
    strategy_last_error: str | None = None,
) -> FfaTerminal:
    core = player.core
    alive = core is not None
    final_resources = core.resources if core is not None else 0
    cargo = sum(unit.cargo for unit in player.units.values())
    return FfaTerminal(
        contestant_id=contestant_id,
        survival_alive=alive,
        core_hp=core.hp if core is not None else 0,
        core_shield=core.shield if core is not None else 0,
        final_resources=final_resources,
        resource_growth=final_resources - initial_resources,
        population_final=player.population,
        unit_count_final=player.population,
        cargo_final=cargo,
        respawn_count=player.respawn_count,
        ticks_alive=player.alive_ticks,
        stats=dict(player.stats),
        strategy_errors=strategy_errors,
        strategy_last_error=strategy_last_error,
    )


def run_ffa(
    contestants: Mapping[str, Strategy] | Sequence[tuple[str, Strategy]],
    *,
    seed: int = 0,
    ticks: int = 500,
    size: int = 256,
    obstacle_density: float = 0.225,
    cluster_iters: int = 400,
    spawn_center: tuple[int, int] = (0, 0),
    spawn_profile: Mapping[str, Mapping[str, object]] | None = None,
    resource_scale: float = 1.0,
    resource_replenish_every: int = RESOURCE_REPLENISH_EVERY,
    respawn_style: str = "ring",
    rule_variant: str | None = None,
    progress_callback: Callable[[int], None] | None = None,
    world_obstacles: Iterable[tuple[int, int]] | None = None,
    world_resource_cells: Iterable[tuple[int, int]] | None = None,
    initial_state: Mapping[str, object] | None = None,
) -> FfaReport:
    """Run one free-for-all match in a shared world and return a report.

    contestants maps a contestant id to its Strategy. Every contestant gets one
    player slot in the same world and decides each tick from its own observation.
    The returned report carries a world-state trace plus a terminal table
    (survival / resources / population / core_hp / unit_count) and a content
    address that is stable across repeated runs with the same seed.

    world_obstacles / world_resource_cells / initial_state are the state-seed
    replay injections (all default None → classic generated-world behavior).
    See ``Game`` and ``World`` docstrings for the accepted shapes.
    """
    ordered = _ordered_contestants(contestants)
    contestant_ids = [contestant_id for contestant_id, _ in ordered]
    player_id_of = {contestant_id: index for index, (contestant_id, _) in enumerate(ordered)}
    strategies = {index: strategy for index, (_, strategy) in enumerate(ordered)}

    game_spawn_profile = None
    if spawn_profile:
        game_spawn_profile = {
            player_id_of[contestant_id]: profile for contestant_id, profile in spawn_profile.items()
        }

    game = Game(
        strategies=strategies,
        size=size,
        seed=seed,
        max_ticks=ticks,
        obstacle_density=obstacle_density,
        cluster_iters=cluster_iters,
        spawn_center=spawn_center,
        spawn_profile=game_spawn_profile,
        resource_scale=resource_scale,
        resource_replenish_every=resource_replenish_every,
        respawn_style=respawn_style,
        rule_variant=rule_variant,
        initial_state=initial_state,
        world_obstacles=world_obstacles,
        world_resource_cells=world_resource_cells,
    )

    initial_resources: dict[str, int] = {}
    for contestant_id in contestant_ids:
        player = game.players[player_id_of[contestant_id]]
        if player.core is not None:
            initial_resources[contestant_id] = player.core.resources

    trace: list[dict[str, object]] = [_frame(game, 0, contestant_ids, player_id_of)]
    try:
        for _ in range(ticks):
            game.tick += 1
            game.step()
            if progress_callback is not None:
                progress_callback(game.tick)
            trace.append(_frame(game, game.tick, contestant_ids, player_id_of))
    finally:
        game.close()

    terminal = tuple(
        _terminal(
            contestant_id,
            game.players[player_id_of[contestant_id]],
            initial_resources.get(contestant_id, 0),
            strategy_errors=game.strategy_errors.get(player_id_of[contestant_id], 0),
            strategy_last_error=game.strategy_last_errors.get(player_id_of[contestant_id]),
        )
        for contestant_id in contestant_ids
    )

    artifact = cast(
        dict[str, JsonValue],
        to_json_value(
            {
                "schema_version": FFA_REPORT_SCHEMA,
                "generator_version": GENERATOR_VERSION,
                "seed": seed,
                "ticks": ticks,
                "contestants": contestant_ids,
                "terminal": [entry.to_json() for entry in terminal],
                "trace": trace,
            }
        ),
    )
    return FfaReport(
        schema_version=FFA_REPORT_SCHEMA,
        generator_version=GENERATOR_VERSION,
        seed=seed,
        ticks=ticks,
        contestant_ids=tuple(contestant_ids),
        terminal=terminal,
        trace=trace,
        artifact=artifact,
        artifact_sha256=content_sha256(artifact),
    )
