"""FFA economy accounting regression tests (deposit -> core resources).

These pin the economic ledger end to end without the live agent subprocess:

- a worker harvest from a natural resource and the later deposit must move the
  cargo into ``core.resources`` and the ``deposited`` stat (mirrors the frozen
  reference ``harvest-deposit-golden`` scenario),
- harvesting from a dropped cargo pile must count into ``harvested`` exactly
  like a natural-source harvest,
- a core self-destruct must record the destroyed stock in ``resources_lost``
  so the economy conservation equation closes,
- the shim must map the agent's ``deposit``/``harvest`` intents to engine
  ``DEPOSIT``/``HARVEST`` plan actions.
"""

from __future__ import annotations

from arena_hero_sim.ffa import WaitStrategy, run_ffa
from arena_hero_sim.ffa.engine import Engine
from arena_hero_sim.ffa.entities import Core, Player, Unit
from arena_hero_sim.ffa.observation import Observation
from arena_hero_sim.ffa.python_agent_shim import decision_to_plan
from arena_hero_sim.ffa.strategy import Plan, Strategy
from arena_hero_sim.ffa.world import World

_BEACON_GROUND: tuple = ("ground", 0, 0)


def _engine_with_one_worker(
    worker_pos: tuple[int, int] = (1, 0),
) -> tuple[Engine, Player, Unit, World]:
    world = World(size=32, seed=11, plain=True)
    world.resources.clear()
    engine = Engine(world)
    player = Player("me")
    player.core = Core("me", (0, 0))
    worker = Unit("me", "WORKER", worker_pos)
    worker.just_spawned = False
    player.units[worker.uid] = worker
    return engine, player, worker, world


def _plan(core_action=None, unit_actions=None) -> Plan:
    return {"core": core_action, "units": unit_actions or {}}


def test_engine_harvest_then_deposit_grows_core_resources() -> None:
    """Natural-resource harvest -> deposit must credit the core and the stat.

    Initial stock is 5; after one harvest (cargo 1) and one deposit the core
    must hold 6 and ``deposited`` must equal 1 — the same economics the frozen
    reference engine pins in its ``harvest-deposit-golden`` scenario.
    """
    engine, player, worker, world = _engine_with_one_worker(worker_pos=(1, 0))
    world.resources.add((1, 0))

    # Tick 1: harvest the natural resource under the worker.
    _, events = engine.resolve(
        {"me": player},
        {"me": _plan(unit_actions={worker.uid: ("HARVEST", {})})},
        _BEACON_GROUND,
        1,
    )
    assert worker.cargo == 1
    assert player.stats["harvested"] == 1
    assert (1, 0) not in world.resources
    assert any(ev["type"] == "HARVESTED" for ev in events)

    # Tick 2: walk back onto the core cell.
    engine.resolve(
        {"me": player},
        {"me": _plan(unit_actions={worker.uid: ("MOVE", {"direction": "LEFT"})})},
        _BEACON_GROUND,
        2,
    )
    assert worker.pos == (0, 0)

    # Tick 3: deposit the cargo; resources must grow 5 -> 6.
    _, events = engine.resolve(
        {"me": player},
        {"me": _plan(unit_actions={worker.uid: ("DEPOSIT", {})})},
        _BEACON_GROUND,
        3,
    )
    assert worker.cargo == 0
    assert player.core is not None
    assert player.core.resources == 6
    assert player.stats["deposited"] == 1
    assert any(ev["type"] == "DEPOSITED" and ev["amount"] == 1 for ev in events)


def test_cargo_pile_harvest_counts_into_harvested_stat() -> None:
    """Harvesting a dropped cargo pile must count into ``harvested``.

    A worker killed mid-route leaves its cargo as a pile; picking that pile up
    is still economic inflow for the bench's harvest metric, so it must count
    exactly like the natural-source branch (previously it silently did not,
    which under-reported economic output in matches with unit deaths).
    """
    engine, player, worker, world = _engine_with_one_worker(worker_pos=(1, 0))
    world.resources.discard((1, 0))
    engine.cargo[(1, 0)] = 2

    _, events = engine.resolve(
        {"me": player},
        {"me": _plan(unit_actions={worker.uid: ("HARVEST", {})})},
        _BEACON_GROUND,
        1,
    )

    # No beacon: one unit per harvest from the pile.
    assert worker.cargo == 1
    assert engine.cargo[(1, 0)] == 1
    assert player.stats["harvested"] == 1
    assert any(ev["type"] == "HARVESTED" and ev["from"] == "cargo" for ev in events)


class _SelfDestructOnce(Strategy):
    """Self-destruct the core on tick 2 (the live agent's deadlock escape)."""

    def decide(self, observation: Observation) -> Plan:
        if observation.tick == 2 and observation.core is not None:
            return {"core": ("SELF_DESTRUCT", {}), "units": {}}
        return {"core": None, "units": {}}


def test_core_self_destruct_records_destroyed_resources_and_conserves() -> None:
    """Self-destruct must book the destroyed stock; the ledger must balance.

    Without the ``resources_lost`` entry the self-destruct path silently wiped
    the core's stock, so a bench terminal row like ``harvest=13, res=5`` was
    indistinguishable from "deposits never landed".  Conservation here:
    initial(5) + deposited(0) + respawn grant(5) ==
    final(5) + spawn(0) + heal(0) + repair(0) + overflow(0) + lost(5).
    """
    report = run_ffa({"die": _SelfDestructOnce(), "wait": WaitStrategy()}, seed=5, ticks=60)

    terminal = next(t for t in report.terminal if t.contestant_id == "die")
    stats = dict(terminal.stats)
    assert terminal.respawn_count == 1
    assert terminal.final_resources == 5
    assert stats["resources_lost"] == 5

    initial = 5
    respawn_grant = terminal.respawn_count * 5
    assert (
        initial + stats["deposited"] + respawn_grant
        == terminal.final_resources
        + stats["spawn_cost"]
        + stats["heal_cost"]
        + stats["repair_cost"]
        + stats["overflow_destroyed"]
        + stats["resources_lost"]
    )


def test_shim_maps_deposit_and_harvest_intents_to_engine_actions() -> None:
    """The shim must not drop the agent's economy intents on the way down."""
    decision = {
        "tick": 3,
        "unit_intents": [
            {
                "unit_id": "11",
                "action": "deposit",
                "direction": None,
                "target_id": None,
                "expected_cell": None,
            },
            {
                "unit_id": "12",
                "action": "harvest",
                "direction": None,
                "target_id": None,
                "expected_cell": None,
            },
        ],
        "core_intent": None,
    }

    plan = decision_to_plan(decision)
    assert plan["core"] is None
    assert plan["units"] == {11: ("DEPOSIT", {}), 12: ("HARVEST", {})}


def test_pop_zero_with_stock_can_still_spawn_worker() -> None:
    """pop=0 / res=5 is settlement timing, not a blocked spawn path.

    A last-tick unit wipe leaves the terminal row at ``pop=0, res=5`` (core
    alive, stock untouched).  The engine must still allow the follow-up spawn:
    population 0 prices the worker at base cost 5 and the core cell is free,
    so one SPAWN resolves and restores the economy.  If the live agent does
    not recover from this state, the cause is decider policy (out of this
    repository's scope), not the sim's spawn accounting or mapping.
    """
    engine, player, worker, _world = _engine_with_one_worker(worker_pos=(1, 0))

    _, events = engine.resolve(
        {"me": player},
        {"me": _plan(unit_actions={worker.uid: ("SELF_DESTRUCT", {})})},
        _BEACON_GROUND,
        1,
    )
    assert player.population == 0
    assert player.core is not None
    assert player.core.resources == 5
    assert any(ev["type"] == "UNIT_REMOVED" for ev in events)

    engine.resolve(
        {"me": player},
        {"me": _plan(core_action=("SPAWN", {"unit_type": "WORKER"}))},
        _BEACON_GROUND,
        2,
    )
    assert player.population == 1
    assert player.core.resources == 0
    assert player.stats["spawn_cost"] == 5
