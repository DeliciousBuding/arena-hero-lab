"""State-seed replay harness for offline stall debugging and A/B algorithm design.

Reads one production ``tick_state`` record from a JSONL log (external path),
maps the tenant-visible slice into an FFA engine initial state, and replays the
python agent against a wait bot for N ticks.  Output is a diag-style per-tick
transcript plus terminal stats, written to stdout and to the output directory.

Approximation contract — this is a state-seed replay, NOT an exact replay:
- only the tenant-visible slice is seeded: terrain obstacles, own core
  position/hp/shield, own units (role/pos/hp/cargo), core resources, visible
  resource cells; everything else is a blank world
- production tick_state stores terrainObstacles/resourceCells as int counts
  without coordinates; in that case the replay world is obstacle-free and a
  deterministic resource ring is synthesized around the seeded core so the
  agent's harvest/deposit loop actually runs (explicit APPROXIMATION line)
- opponents are wait bots; no enemy units/cores are placed (no global state)
- cross-tick state (plan / deciderState / submitResult / events /
  visibleEnemies / nearest* distances / stats counters) cannot be mapped and
  is ignored with explicit WARNING lines
- cumulative stats restart at zero; beacon is replayed as ground position only

Usage (from the arena-hero-lab repo root):
    uv run python scripts/replay_state_seed.py --jsonl <path> --record-index 0 --ticks 500
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from arena_hero_sim.ffa import WaitStrategy, run_ffa
from arena_hero_sim.ffa.config import CORE_HP, CORE_SHIELD
from arena_hero_sim.ffa.python_agent_shim import PythonAgentStrategy

DEFAULT_TICKS = 500
DEFAULT_STALL_TICKS = 100
DEFAULT_WORLD_SIZE = 256

# 生产记录里单位 role 的域值（小写 domain）与 FFA 大写类型双兼容。
_ROLE_ALIASES = {
    "worker": "WORKER",
    "vanguard": "VANGUARD",
    "ranger": "RANGER",
    "WORKER": "WORKER",
    "VANGUARD": "VANGUARD",
    "RANGER": "RANGER",
}

# 顶层字段 -> 映射方式。不在表内的字段会被明确降级并打 warning。
_MAPPED_FIELDS = {
    "tick": "label",
    "tenantId": "label",
    "recordType": "checked",
    "population": "cross-checked",
    "resources": "core resources",
    "resourceCells": "visible resource cells",
    "terrainObstacles": "terrain obstacles",
    "units": "own units",
    "core": "core position/hp/shield",
    "beacon": "beacon ground position",
}


@dataclass
class ParsedUnit:
    prod_id: str | None
    utype: str
    pos: tuple[int, int]
    hp: int | None
    cargo: int


@dataclass
class ParsedSeed:
    source_tick: int | None
    tenant: str | None
    core_pos: tuple[int, int]
    core_hp: int | None
    core_shield: int | None
    core_resources: int | None
    units: list[ParsedUnit]
    obstacles: list[tuple[int, int]]
    resource_cells: list[tuple[int, int]]
    beacon_ground: tuple[int, int] | None
    population: int | None
    world_size: int
    warnings: list[str] = field(default_factory=list)


def _default_out_dir() -> Path:
    """Output dir default: the wave workspace runs dir on the script's own drive.

    The path is derived at runtime (script drive + /Code/Temp/arena-wave2/E/runs)
    so the source tree carries no machine-specific absolute path; override with
    the ARENA_REPLAY_OUT environment variable or the --out flag.
    """
    env = os.environ.get("ARENA_REPLAY_OUT")
    if env:
        return Path(env)
    drive = Path(__file__).resolve().drive
    if drive:
        return Path(f"{drive}/") / "Code" / "Temp" / "arena-wave2" / "E" / "runs"
    return Path.cwd() / "runs"


def load_jsonl_records(jsonl_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with jsonl_path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{jsonl_path}:{lineno}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{jsonl_path}:{lineno}: record is not a JSON object")
            records.append(value)
    return records


def parse_cell_entry(entry: object, context: str, warnings: list[str]) -> tuple[int, int] | None:
    """Accept "[x, y]", "x,y", or {"x": .., "y": ..}; None + warning otherwise."""
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        try:
            return int(entry[0]), int(entry[1])
        except (TypeError, ValueError):
            pass
    elif isinstance(entry, str):
        parts = entry.split(",")
        if len(parts) == 2:
            try:
                return int(parts[0].strip()), int(parts[1].strip())
            except ValueError:
                pass
    elif isinstance(entry, dict) and "x" in entry and "y" in entry:
        try:
            return int(entry["x"]), int(entry["y"])
        except (TypeError, ValueError):
            pass
    warnings.append(f"WARNING: {context}: cannot parse cell {entry!r}; skipped")
    return None


def parse_cell_list(value: object, context: str, warnings: list[str]) -> list[tuple[int, int]]:
    if value is None:
        warnings.append(f"WARNING: {context}: field missing; treated as empty")
        return []
    if isinstance(value, int):
        # 生产日志 tick_state 只存计数（如 terrainObstacles=15），不含坐标列表。
        warnings.append(
            f"WARNING: {context}: production count-only field ({value}); "
            "coordinates unavailable; treated as empty"
        )
        return []
    if not isinstance(value, list):
        warnings.append(f"WARNING: {context}: expected a list, got {type(value).__name__}")
        return []
    cells: list[tuple[int, int]] = []
    for entry in value:
        parsed = parse_cell_entry(entry, context, warnings)
        if parsed is not None:
            cells.append(parsed)
    return cells


def _world_size_for(cells: list[tuple[int, int]], base_size: int, warnings: list[str]) -> int:
    """Grow the world so every seeded coordinate is in bounds (chunk-aligned)."""
    max_abs = 0
    for x, y in cells:
        max_abs = max(max_abs, abs(x), abs(y))
    needed = 2 * (max_abs + 1)  # in_bounds requires offset > max_abs
    if needed <= base_size:
        return base_size
    size = ((needed + 31) // 32) * 32
    warnings.append(
        f"WARNING: coordinates need a world of at least {needed}; "
        f"growing world size from {base_size} to {size}"
    )
    return size


def parse_tick_state(record: dict[str, Any], base_size: int = DEFAULT_WORLD_SIZE) -> ParsedSeed:
    warnings: list[str] = []
    for key in record:
        if key not in _MAPPED_FIELDS:
            shape = f"({len(record[key])} entries)" if isinstance(record[key], list) else ""
            warnings.append(f"WARNING: field {key!r} cannot be mapped; ignored {shape}".rstrip())

    record_type = record.get("recordType")
    if record_type is not None and record_type != "tick_state":
        warnings.append(
            f"WARNING: recordType={record_type!r} is not 'tick_state'; replaying anyway"
        )

    source_tick = record.get("tick")
    if source_tick is not None and not isinstance(source_tick, int):
        warnings.append(f"WARNING: tick={source_tick!r} is not an integer; using label only")
        source_tick = None

    core = record.get("core")
    if not isinstance(core, dict):
        raise ValueError("record has no usable core (expected an object)")
    # 生产日志用 core.pos；合成样本用 core.position，两者都接受。
    raw_core_pos = core.get("pos")
    if raw_core_pos is None:
        raw_core_pos = core.get("position")
    if raw_core_pos is None:
        raise ValueError("record has no usable core.position")
    core_pos = parse_cell_entry(raw_core_pos, "core.position", warnings)
    if core_pos is None:
        raise ValueError("record core.position is unparseable")

    core_hp: int | None = None
    if "hp" in core:
        core_hp = int(core["hp"])
    else:
        warnings.append(f"WARNING: core.hp missing; defaulting to {CORE_HP}")
    core_shield: int | None = None
    if "shield" in core:
        core_shield = int(core["shield"])
    else:
        warnings.append(f"WARNING: core.shield missing; defaulting to {CORE_SHIELD}")
    core_resources: int | None = None
    raw_resources = record.get("resources")
    if isinstance(raw_resources, int):
        core_resources = raw_resources
    else:
        warnings.append(f"WARNING: resources={raw_resources!r} unusable; defaulting to 0")

    units: list[ParsedUnit] = []
    raw_units = record.get("units")
    if not isinstance(raw_units, list):
        warnings.append("WARNING: units field missing or not a list; replay starts with no units")
    else:
        for index, unit in enumerate(raw_units):
            if not isinstance(unit, dict):
                warnings.append(f"WARNING: units[{index}] is not an object; skipped")
                continue
            role = unit.get("role")
            utype = _ROLE_ALIASES.get(role)
            if utype is None:
                warnings.append(f"WARNING: units[{index}] role={role!r} unknown; skipped")
                continue
            pos = parse_cell_entry(unit.get("pos"), f"units[{index}].pos", warnings)
            if pos is None:
                warnings.append(f"WARNING: units[{index}] pos unparseable; skipped")
                continue
            hp = int(unit["hp"]) if "hp" in unit else None
            if hp is None:
                warnings.append(f"WARNING: units[{index}] hp missing; defaulting to role max")
            cargo = int(unit["cargo"]) if "cargo" in unit else 0
            prod_id = unit.get("id")
            units.append(
                ParsedUnit(
                    prod_id=str(prod_id) if prod_id is not None else None,
                    utype=utype,
                    pos=pos,
                    hp=hp,
                    cargo=cargo,
                )
            )

    obstacles = parse_cell_list(record.get("terrainObstacles"), "terrainObstacles", warnings)
    resource_cells = parse_cell_list(record.get("resourceCells"), "resourceCells", warnings)

    population: int | None = None
    raw_population = record.get("population")
    if isinstance(raw_population, int):
        population = raw_population
        if population != len(units):
            warnings.append(
                f"WARNING: population={population} but units[] has {len(units)} entries; "
                "using the units list"
            )

    beacon_ground: tuple[int, int] | None = None
    beacon = record.get("beacon")
    if isinstance(beacon, dict):
        status = str(beacon.get("status", "")).upper()
        raw_beacon_pos = beacon.get("pos")
        if raw_beacon_pos is None:
            raw_beacon_pos = beacon.get("position")
        if status == "GROUND" and raw_beacon_pos is not None:
            pos = parse_cell_entry(raw_beacon_pos, "beacon.position", warnings)
            if pos is not None:
                beacon_ground = pos
        else:
            warnings.append(
                f"WARNING: beacon status={status or 'missing'} not replayed "
                "(carried/unknown needs global state); using default ground beacon"
            )
    elif beacon is not None:
        warnings.append(f"WARNING: beacon={beacon!r} unparseable; using default ground beacon")

    world_size = _world_size_for(
        [core_pos]
        + [u.pos for u in units]
        + obstacles
        + resource_cells
        + ([beacon_ground] if beacon_ground is not None else []),
        base_size,
        warnings,
    )

    return ParsedSeed(
        source_tick=source_tick,
        tenant=record.get("tenantId"),
        core_pos=core_pos,
        core_hp=core_hp,
        core_shield=core_shield,
        core_resources=core_resources,
        units=units,
        obstacles=obstacles,
        resource_cells=resource_cells,
        beacon_ground=beacon_ground,
        population=population,
        world_size=world_size,
        warnings=warnings,
    )


@dataclass
class ReplayResult:
    lines: list[str]
    stalled: bool
    report: object


def _format_seed_summary(parsed: ParsedSeed, record_index: int) -> list[str]:
    tenant = parsed.tenant or "?"
    tick = parsed.source_tick if parsed.source_tick is not None else "?"
    lines = [
        f"state-seed replay: record_index={record_index} tenant={tenant} source_tick={tick}",
        f"seed core pos={parsed.core_pos} hp={parsed.core_hp} shield={parsed.core_shield} "
        f"resources={parsed.core_resources}",
        f"seed units={len(parsed.units)} obstacles={len(parsed.obstacles)} "
        f"resource_cells={len(parsed.resource_cells)} world_size={parsed.world_size} "
        f"beacon_ground={parsed.beacon_ground}",
    ]
    for unit in parsed.units:
        lines.append(
            f"  unit {unit.prod_id or '-'}: {unit.utype}@{unit.pos} hp={unit.hp} cargo={unit.cargo}"
        )
    return lines


def _synthetic_resource_patch(parsed: ParsedSeed, cap: int = 12) -> list[tuple[int, int]]:
    """Deterministic resource ring around the seeded core (logs without cell coords).

    Production tick_state only stores ``resourceCells`` as an int count, so no
    coordinates survive to the replay world.  An empty world starves workers
    structurally (no harvest -> no deposit -> every seed "stalls") and makes
    behavior attribution impossible.  Instead we place a fixed ring (radius
    2-3, row-major, skipping the core and occupied unit cells) so the agent's
    real harvest/deposit loop runs.  This is a documented approximation, not a
    reconstruction of the live world.
    """
    core_x, core_y = parsed.core_pos
    occupied = {unit.pos for unit in parsed.units}
    cells: list[tuple[int, int]] = []
    for radius in (2, 3):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                pos = (core_x + dx, core_y + dy)
                if pos in occupied or pos == parsed.core_pos:
                    continue
                cells.append(pos)
                if len(cells) >= cap:
                    return cells
    return cells


def _resolve_resource_cells(
    parsed: ParsedSeed,
) -> tuple[list[tuple[int, int]], str | None]:
    """Pick the replay world's resource cells; synthesize when the log lacks coords."""
    if parsed.resource_cells:
        return parsed.resource_cells, None
    patch = _synthetic_resource_patch(parsed)
    note = (
        "APPROXIMATION: log has no resource cell coordinates (count-only "
        f"field); synthesized {len(patch)}-cell resource ring around the core"
    )
    return patch, note


def run_state_seed_replay(
    parsed: ParsedSeed,
    *,
    ticks: int = DEFAULT_TICKS,
    sim_seed: int = 0,
    stall_ticks: int = DEFAULT_STALL_TICKS,
    record_index: int = 0,
) -> ReplayResult:
    lines: list[str] = [*parsed.warnings, *_format_seed_summary(parsed, record_index), ""]

    resource_cells, resource_note = _resolve_resource_cells(parsed)
    if resource_note is not None:
        lines.append(resource_note)

    core_state: dict[str, object] = {"pos": parsed.core_pos}
    if parsed.core_hp is not None:
        core_state["hp"] = parsed.core_hp
    if parsed.core_shield is not None:
        core_state["shield"] = parsed.core_shield
    if parsed.core_resources is not None:
        core_state["resources"] = parsed.core_resources
    unit_states: list[dict[str, object]] = []
    for unit in parsed.units:
        unit_state: dict[str, object] = {"utype": unit.utype, "pos": unit.pos}
        if unit.hp is not None:
            unit_state["hp"] = unit.hp
        unit_state["cargo"] = unit.cargo
        unit_states.append(unit_state)
    initial_state: dict[str, object] = {
        "players": {
            0: {
                "core": core_state,
                "units": unit_states,
            }
        }
    }
    if parsed.beacon_ground is not None:
        initial_state["beacon"] = ("ground", parsed.beacon_ground[0], parsed.beacon_ground[1])

    strategy = PythonAgentStrategy(
        movement_guard=True,
        economy_budget=True,
        raid_quota=True,
        economy_expansion=True,
        exploration_v2=True,
        respawn_recovery=True,
    )
    try:
        report = run_ffa(
            {"python": strategy, "wait": WaitStrategy()},
            seed=sim_seed,
            ticks=ticks,
            size=parsed.world_size,
            world_obstacles=parsed.obstacles,
            world_resource_cells=resource_cells,
            initial_state=initial_state,
        )
    finally:
        strategy.close()

    python_cid = "python"
    last_deposit_tick = 0
    last_seen_deposited = 0
    stall_warned = False
    stalled = False
    for frame in report.trace:
        tick = cast(int, frame["tick"])
        players = cast(dict[str, Any], frame["players"])
        player = players.get(python_cid, {})
        core = player.get("core") or {}
        resources = core.get("resources")
        core_pos = tuple(core["pos"]) if core else None
        units = player.get("units") or []
        stats = player.get("stats") or {}
        deposited = int(stats.get("deposited", 0))
        cargo_total = sum(int(u.get("cargo", 0)) for u in units)
        unit_desc = " ".join(
            f"{u['uid']}:{u['utype'][0]}@{tuple(u['pos'])}c{u.get('cargo', 0)}" for u in units
        )
        lines.append(
            f"tick={tick:3d} res={resources} core={core_pos} "
            f"harvested={stats.get('harvested')} deposited={deposited} "
            f"cargo={cargo_total} | {unit_desc}"
        )
        if tick > 0 and deposited > last_seen_deposited:
            last_deposit_tick = tick
        if not stall_warned and tick > 0 and tick - last_deposit_tick >= stall_ticks:
            lines.append(
                f"WARNING: replay stall: no deposit for {tick - last_deposit_tick} ticks "
                f"(last deposit at tick {last_deposit_tick}, cargo={cargo_total})"
            )
            stall_warned = True
            stalled = True
        last_seen_deposited = deposited

    terminal = next(t for t in report.terminal if t.contestant_id == python_cid)
    lines.append("")
    lines.append("terminal stats: " + json.dumps(dict(terminal.stats), sort_keys=True))
    lines.append(
        f"final_resources: {terminal.final_resources} cargo_final: {terminal.cargo_final} "
        f"population_final: {terminal.population_final} core_hp: {terminal.core_hp} "
        f"core_shield: {terminal.core_shield} alive: {terminal.survival_alive}"
    )
    lines.append(f"strategy_errors: {terminal.strategy_errors} {terminal.strategy_last_error}")
    if stalled:
        lines.append(
            f"WARNING: replay ended in a deposit stall window "
            f"(last deposit tick {last_deposit_tick} of {ticks})"
        )
    return ReplayResult(lines=lines, stalled=stalled, report=report)


def write_outputs(result: ReplayResult, out_dir: Path, stem: str, record_index: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / f"{stem}-rec{record_index}.txt"
    transcript_path.write_text("\n".join(result.lines) + "\n", encoding="utf-8")
    report = cast(Any, result.report)
    artifact_path = out_dir / f"{stem}-rec{record_index}.report.json"
    artifact_path.write_text(
        json.dumps(report.to_json(), indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"wrote {transcript_path}")
    print(f"wrote {artifact_path}")


def make_sample_records() -> list[dict[str, Any]]:
    """Self-produced small sample of tick_state records for fixtures/e2e runs."""
    base_record: dict[str, Any] = {
        "tick": 400,
        "tenantId": "sample-tenant",
        "recordType": "tick_state",
        "population": 3,
        "resources": 12,
        "resourceCells": ["6,4", "7,4", "8,5"],
        "terrainObstacles": ["0,1", "1,1", "2,1", "4,7", "5,7"],
        "units": [
            {"id": "u-1", "role": "worker", "pos": [6, 5], "hp": 2, "cargo": 1},
            {"id": "u-2", "role": "worker", "pos": [5, 6], "hp": 1, "cargo": 0},
            {"id": "u-3", "role": "ranger", "pos": [4, 5], "hp": 2, "cargo": 0},
        ],
        "core": {"position": [5, 5], "hp": 5, "shield": 5},
        "beacon": {"position": [0, 0], "status": "ground"},
        "visibleEnemies": [],
        "nearestEnemyDist": 25,
        "nearestResourceDist": 1,
        "events": [
            {"kind": "UNIT_DEPOSITED", "tick": 400},
            {"kind": "UNIT_HARVESTED", "tick": 399},
        ],
        "plan": {"core": None, "units": {}},
        "deciderState": {"mode": "economy"},
        "submitResult": {"ok": True},
    }
    minimal_record: dict[str, Any] = {
        "tick": 900,
        "tenantId": "sample-tenant",
        "recordType": "tick_state",
        "units": [{"role": "worker", "pos": {"x": -3, "y": -3}}],
        "core": {"position": [-3, -2]},
        "terrainObstacles": [[-1, -1]],  # [x, y] coordinate form
    }
    return [base_record, minimal_record]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", required=True, type=Path, help="production JSONL log path")
    parser.add_argument("--record-index", type=int, default=0, help="0-based record index")
    parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    parser.add_argument("--seed", type=int, default=0, help="simulator seed (uid/replenish rng)")
    parser.add_argument(
        "--stall-ticks",
        type=int,
        default=DEFAULT_STALL_TICKS,
        help="no-deposit window that triggers a stall warning",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output dir (default: ARENA_REPLAY_OUT or the wave workspace runs dir)",
    )
    args = parser.parse_args()

    records = load_jsonl_records(args.jsonl)
    if args.record_index < 0 or args.record_index >= len(records):
        print(
            f"record-index {args.record_index} out of range: {len(records)} record(s) in "
            f"{args.jsonl}",
            file=sys.stderr,
        )
        return 2

    parsed = parse_tick_state(records[args.record_index])
    result = run_state_seed_replay(
        parsed,
        ticks=args.ticks,
        sim_seed=args.seed,
        stall_ticks=args.stall_ticks,
        record_index=args.record_index,
    )
    for line in result.lines:
        print(line)

    out_dir = args.out if args.out is not None else _default_out_dir()
    write_outputs(result, out_dir, args.jsonl.stem, args.record_index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
