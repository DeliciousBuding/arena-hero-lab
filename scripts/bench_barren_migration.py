"""Barren migration benchmark: N python tenants spawned on far ring chunks.

Measurable baseline for the next wave's "R1b chunk rescan" and "migration
direction persistence" work.  Unlike ``bench_ffa.py --barren`` (which spawns
every contestant at one shared center and prints no migration metric), this
script:

- places each tenant's Core on its own random chunk on rings 8-16 (official
  ring = axis(cx) + axis(cy); official quota = max(2, 128 // (8 + ring))),
- keeps official resupply semantics (replenish every 4 resolved ticks,
  resource_scale = 1.0 so the engine applies the official per-chunk quota),
- reports per tenant: first HARVEST tick, first DEPOSIT tick, first core
  migration tick, and the total number of ticks in which the Core moved.

Deterministic: same seed -> same chunks -> same world -> same report sha.
The agent runs in a subprocess; set ``ARENA_HERO_AGENT_PYTHON`` (or pass
``--agent-python``) to an interpreter that has ``arena-hero-agent`` installed.

Usage (from the arena-hero-lab repo root):
    uv run python scripts/bench_barren_migration.py --seeds 0 1 2 --ticks 1500
    uv run python scripts/bench_barren_migration.py --seeds 0 --ticks 1500 --json
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, cast

from arena_hero_sim.ffa.config import (
    CHUNK_SIZE,
    CORE_HP,
    CORE_SHIELD,
    CORE_START_RESOURCES,
    EMPTY,
    RESOURCE_REPLENISH_EVERY,
    UNIT_STATS,
    resource_quota,
)
from arena_hero_sim.ffa.orchestrator import FfaTerminal, run_ffa
from arena_hero_sim.ffa.python_agent_shim import PythonAgentStrategy, discover_agent_python
from arena_hero_sim.ffa.world import World


def _axis(c: int) -> int:
    """Official chunk axis metric: the ring contribution of one coordinate."""
    return c if c >= 0 else -c - 1


def _ring(cx: int, cy: int) -> int:
    """Official chunk ring = axis(cx) + axis(cy)."""
    return _axis(cx) + _axis(cy)


def _draw_chunk(rng: random.Random, ring_min: int, ring_max: int) -> tuple[tuple[int, int], int]:
    """Draw a random chunk whose official ring lies in [ring_min, ring_max]."""
    ring = rng.randint(ring_min, ring_max)
    axis_x = rng.randint(0, ring)
    axis_y = ring - axis_x
    cx = axis_x if rng.random() < 0.5 else -axis_x - 1
    cy = axis_y if rng.random() < 0.5 else -axis_y - 1
    return (cx, cy), ring


def choose_tenant_chunks(
    seed: int,
    tenant_count: int = 4,
    ring_min: int = 8,
    ring_max: int = 16,
) -> list[tuple[tuple[int, int], int]]:
    """Deterministic unique chunk per tenant, all on rings [ring_min, ring_max]."""
    rng = random.Random(f"barren-migration:{seed}")
    out: list[tuple[tuple[int, int], int]] = []
    seen: set[tuple[int, int]] = set()
    for _ in range(tenant_count):
        chunk, ring = _draw_chunk(rng, ring_min, ring_max)
        attempts = 0
        while chunk in seen and attempts < 200:
            chunk, ring = _draw_chunk(rng, ring_min, ring_max)
            attempts += 1
        if chunk in seen:
            raise ValueError(f"could not draw a unique chunk for seed={seed}")
        seen.add(chunk)
        out.append((chunk, ring))
    return out


def world_size_for_chunks(chunks: list[tuple[int, int]], base_size: int = 512) -> int:
    """32-multiple world side length that keeps every chunk cell in bounds."""
    max_abs = 0
    for cx, cy in chunks:
        max_abs = max(
            max_abs,
            abs(cx) * CHUNK_SIZE + CHUNK_SIZE - 1,
            abs(cy) * CHUNK_SIZE + CHUNK_SIZE - 1,
        )
    needed = 2 * (max_abs + 1)
    return max(base_size, ((needed + 31) // 32) * 32)


def nearest_empty_cell(world: World, center: tuple[int, int]) -> tuple[int, int]:
    """Deterministic nearest cell that is EMPTY (not obstacle, not resource).

    Row-major spiral with fixed tie-breaking so the result depends only on the
    generated world.  The center itself is tried first (Core lands on the chunk
    center whenever the terrain allows it).
    """
    cx, cy = center
    if world.in_bounds(cx, cy) and world.terrain_kind(cx, cy) == EMPTY:
        return (cx, cy)
    radius = 1
    while radius <= CHUNK_SIZE + 2:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                x, y = cx + dx, cy + dy
                if world.in_bounds(x, y) and world.terrain_kind(x, y) == EMPTY:
                    return (x, y)
        radius += 1
    raise ValueError(f"no empty cell found near {center}")


@dataclass(frozen=True, slots=True)
class TenantMetrics:
    tenant_id: str
    chunk: tuple[int, int]
    ring: int
    quota: int
    core_start: tuple[int, int]
    first_harvest_tick: int | None
    first_deposit_tick: int | None
    first_migration_tick: int | None
    core_move_ticks: int
    final_resources: int
    population_final: int
    respawn_count: int
    alive: bool

    def to_json(self) -> dict[str, object]:
        return {
            "tenant": self.tenant_id,
            "chunk": [self.chunk[0], self.chunk[1]],
            "ring": self.ring,
            "quota": self.quota,
            "core_start": [self.core_start[0], self.core_start[1]],
            "first_harvest_tick": self.first_harvest_tick,
            "first_deposit_tick": self.first_deposit_tick,
            "first_migration_tick": self.first_migration_tick,
            "core_move_ticks": self.core_move_ticks,
            "final_resources": self.final_resources,
            "population_final": self.population_final,
            "respawn_count": self.respawn_count,
            "alive": self.alive,
        }


def extract_tenant_metrics(
    trace: list[dict[str, object]],
    terminal: FfaTerminal,
    tenant_id: str,
    chunk: tuple[int, int],
    ring: int,
) -> TenantMetrics:
    """Derive first-harvest / first-deposit / core-move metrics from the trace."""
    first_harvest: int | None = None
    first_deposit: int | None = None
    first_migration: int | None = None
    core_move_ticks = 0
    core_start: tuple[int, int] | None = None
    prev_pos: tuple[int, int] | None = None
    for frame in trace:
        tick = cast(int, frame["tick"])
        players = cast(dict[str, Any], frame["players"])
        player = cast(dict[str, Any], players.get(tenant_id) or {})
        stats = cast(dict[str, Any], player.get("stats") or {})
        if first_harvest is None and int(stats.get("harvested", 0)) > 0:
            first_harvest = tick
        if first_deposit is None and int(stats.get("deposited", 0)) > 0:
            first_deposit = tick
        core = cast(dict[str, Any], player.get("core") or {})
        raw_pos = core.get("pos")
        cur: tuple[int, int] | None = None
        if raw_pos is not None:
            pos_list = cast(list[Any], raw_pos)
            cur = (int(pos_list[0]), int(pos_list[1]))
        if core_start is None and cur is not None:
            core_start = cur
        if cur is not None and prev_pos is not None and cur != prev_pos:
            core_move_ticks += 1
            if first_migration is None:
                first_migration = tick
        prev_pos = cur
    if core_start is None:
        raise ValueError(f"tenant {tenant_id!r} has no core position in the trace")
    return TenantMetrics(
        tenant_id=tenant_id,
        chunk=chunk,
        ring=ring,
        quota=resource_quota(chunk[0], chunk[1]),
        core_start=core_start,
        first_harvest_tick=first_harvest,
        first_deposit_tick=first_deposit,
        first_migration_tick=first_migration,
        core_move_ticks=core_move_ticks,
        final_resources=terminal.final_resources,
        population_final=terminal.population_final,
        respawn_count=terminal.respawn_count,
        alive=terminal.survival_alive,
    )


@dataclass(frozen=True, slots=True)
class SeedResult:
    seed: int
    ticks: int
    world_size: int
    sha: str
    tenants: tuple[TenantMetrics, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "ticks": self.ticks,
            "world_size": self.world_size,
            "sha": self.sha,
            "tenants": [tenant.to_json() for tenant in self.tenants],
        }


def make_strategy(kind: str, agent_python: str | None) -> PythonAgentStrategy:
    if kind == "base":
        return PythonAgentStrategy(agent_python=agent_python)
    if kind == "production":
        return PythonAgentStrategy(
            agent_python=agent_python,
            exploration_v2=True,
            economy_expansion=True,
        )
    if kind == "recovery":
        return PythonAgentStrategy(
            agent_python=agent_python,
            movement_guard=True,
            economy_budget=True,
            raid_quota=True,
            economy_expansion=True,
            exploration_v2=True,
            respawn_recovery=True,
        )
    raise ValueError(f"unknown strategy kind {kind!r}")


def agent_version_evidence(agent_python: str | None) -> str:
    """Resolved interpreter path + installed arena-hero-agent version (evidence)."""
    resolved = agent_python or discover_agent_python()
    if resolved is None:
        return "agent python: NOT FOUND (set ARENA_HERO_AGENT_PYTHON)"
    version = "unknown"
    try:
        proc = subprocess.run(
            [
                resolved,
                "-c",
                "import importlib.metadata as m; print(m.version('arena-hero-agent'))",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            version = proc.stdout.strip().splitlines()[-1]
    except Exception as exc:  # evidence only; the decide loop fails fast on its own
        version = f"version probe failed: {exc}"
    return f"agent python: {resolved} (arena-hero-agent {version})"


def run_seed(
    seed: int,
    ticks: int,
    strategy_kind: str,
    tenant_count: int,
    ring_min: int,
    ring_max: int,
    density: float,
    agent_python: str | None,
) -> SeedResult:
    chunks = choose_tenant_chunks(seed, tenant_count, ring_min, ring_max)
    size = world_size_for_chunks([chunk for chunk, _ in chunks])

    # Scratch world mirrors Game's construction (same seed/params), so the
    # EMPTY cells found here are exactly the cells of the real run's world.
    scratch = World(
        size=size,
        seed=seed,
        obstacle_density=density,
        cluster_iters=400,
        resource_scale=1.0,
        replenish_every=RESOURCE_REPLENISH_EVERY,
    )
    starts: list[tuple[int, int]] = []
    for chunk, _ring_value in chunks:
        center = (
            chunk[0] * CHUNK_SIZE + CHUNK_SIZE // 2,
            chunk[1] * CHUNK_SIZE + CHUNK_SIZE // 2,
        )
        starts.append(nearest_empty_cell(scratch, center))

    initial_state: dict[str, object] = {
        "players": {
            index: {
                "core": {
                    "pos": pos,
                    "hp": CORE_HP,
                    "shield": CORE_SHIELD,
                    "resources": CORE_START_RESOURCES,
                },
                "units": [
                    {
                        "utype": "WORKER",
                        "pos": pos,
                        "hp": UNIT_STATS["WORKER"]["hp"],
                        "cargo": 0,
                    }
                ],
            }
            for index, pos in enumerate(starts)
        }
    }

    strategies: list[tuple[str, PythonAgentStrategy]] = []
    for index in range(tenant_count):
        strategies.append((f"t{index + 1}", make_strategy(strategy_kind, agent_python)))
    try:
        report = run_ffa(
            {cid: strat for cid, strat in strategies},
            seed=seed,
            ticks=ticks,
            size=size,
            obstacle_density=density,
            cluster_iters=400,
            resource_scale=1.0,
            resource_replenish_every=RESOURCE_REPLENISH_EVERY,
            respawn_style="ring",
            initial_state=initial_state,
        )
    finally:
        for _cid, strat in strategies:
            strat.close()

    tenants: list[TenantMetrics] = []
    for chunk, ring in chunks:
        cid = f"t{len(tenants) + 1}"
        terminal = next(t for t in report.terminal if t.contestant_id == cid)
        tenants.append(extract_tenant_metrics(report.trace, terminal, cid, chunk, ring))
    return SeedResult(
        seed=seed,
        ticks=ticks,
        world_size=size,
        sha=report.artifact_sha256,
        tenants=tuple(tenants),
    )


def _fmt_first(value: int | None) -> str:
    return "-" if value is None else str(value)


def print_seed(result: SeedResult) -> None:
    print(f"seed={result.seed} sha={result.sha[:16]} size={result.world_size}")
    for tenant in result.tenants:
        print(
            f"  {tenant.tenant_id} chunk={tenant.chunk} ring={tenant.ring} "
            f"quota={tenant.quota} core={tenant.core_start} "
            f"first_harvest={_fmt_first(tenant.first_harvest_tick)} "
            f"first_deposit={_fmt_first(tenant.first_deposit_tick)} "
            f"first_migration={_fmt_first(tenant.first_migration_tick)} "
            f"move_ticks={tenant.core_move_ticks} "
            f"res={tenant.final_resources} pop={tenant.population_final} "
            f"respawn={tenant.respawn_count} alive={int(tenant.alive)}"
        )


def print_aggregate(results: list[SeedResult]) -> None:
    print("\naggregate (mean over seeds)")
    tenant_ids = [tenant.tenant_id for tenant in results[0].tenants]
    for tenant_id in tenant_ids:
        subs = [
            tenant
            for result in results
            for tenant in result.tenants
            if tenant.tenant_id == tenant_id
        ]
        n = len(subs)
        first_harvests = [t.first_harvest_tick for t in subs if t.first_harvest_tick is not None]
        first_deposits = [t.first_deposit_tick for t in subs if t.first_deposit_tick is not None]
        first_migrations = [
            t.first_migration_tick for t in subs if t.first_migration_tick is not None
        ]

        def mean(values: list[int]) -> float | None:
            return sum(values) / len(values) if values else None

        print(
            f"  {tenant_id} first_harvest={_fmt_mean(mean(first_harvests))}({len(first_harvests)}/{n}) "
            f"first_deposit={_fmt_mean(mean(first_deposits))}({len(first_deposits)}/{n}) "
            f"first_migration={_fmt_mean(mean(first_migrations))}({len(first_migrations)}/{n}) "
            f"move_ticks={sum(t.core_move_ticks for t in subs) / n:.1f} "
            f"res={sum(t.final_resources for t in subs) / n:.1f} "
            f"pop={sum(t.population_final for t in subs) / n:.1f} "
            f"respawn={sum(t.respawn_count for t in subs) / n:.2f} "
            f"alive={sum(int(t.alive) for t in subs)}/{n}"
        )


def _fmt_mean(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4, 5, 6, 7])
    parser.add_argument("--ticks", type=int, default=1500)
    parser.add_argument("--tenants", type=int, default=4)
    parser.add_argument(
        "--strategy",
        choices=["base", "production", "recovery"],
        default="production",
        help="agent research switches: production = exploration-v2 + economy-expansion",
    )
    parser.add_argument("--ring-min", type=int, default=8)
    parser.add_argument("--ring-max", type=int, default=16)
    parser.add_argument("--density", type=float, default=0.225)
    parser.add_argument(
        "--agent-python",
        default=None,
        help="interpreter with arena-hero-agent installed (default: env/discovery)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one machine-readable JSON manifest instead of the text table",
    )
    args = parser.parse_args()

    if args.tenants < 2:
        print("--tenants must be at least 2", file=sys.stderr)
        return 2

    print(agent_version_evidence(args.agent_python))
    results = [
        run_seed(
            seed,
            args.ticks,
            args.strategy,
            args.tenants,
            args.ring_min,
            args.ring_max,
            args.density,
            args.agent_python,
        )
        for seed in args.seeds
    ]

    if args.json:
        manifest: dict[str, object] = {
            "schema": "arena.bench.barren-migration.v1",
            "strategy": args.strategy,
            "ticks": args.ticks,
            "ring_min": args.ring_min,
            "ring_max": args.ring_max,
            "density": args.density,
            "seeds": [result.to_json() for result in results],
        }
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    for result in results:
        print_seed(result)
    print_aggregate(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
