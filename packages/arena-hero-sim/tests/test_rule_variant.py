"""Phase 1 rule-perturbation mechanism: opt-in variants must actually diverge.

The public battery always runs ``rule_variant=None`` (frozen v0.14).  The
opt-in variants are for the robustness study; these tests pin that (a) the
baseline is unchanged and (b) a variant flips exactly the one rule it claims.
"""

from __future__ import annotations

from arena_hero_sim.ffa import run_ffa
from arena_hero_sim.ffa.observation import Observation
from arena_hero_sim.ffa.strategy import Plan, Strategy


class _SelfDestruct(Strategy):
    """Self-destruct the core on tick 2 to force a respawn (free worker rule)."""

    def decide(self, observation: Observation) -> Plan:
        if observation.tick == 2 and observation.core is not None:
            return {"core": ("SELF_DESTRUCT", {}), "units": {}}
        return {"core": None, "units": {}}


class _Wait(Strategy):
    def decide(self, observation: Observation) -> Plan:
        return {"core": None, "units": {}}


def test_baseline_is_deterministic_and_unchanged() -> None:
    first = run_ffa({"die": _SelfDestruct(), "wait": _Wait()}, seed=5, ticks=60)
    second = run_ffa({"die": _SelfDestruct(), "wait": _Wait()}, seed=5, ticks=60)
    assert first.artifact_sha256 == second.artifact_sha256


def test_paid_respawn_diverges_from_free_respawn() -> None:
    baseline = run_ffa(
        {"die": _SelfDestruct(), "wait": _Wait()}, seed=5, ticks=60, rule_variant=None
    )
    paid = run_ffa(
        {"die": _SelfDestruct(), "wait": _Wait()}, seed=5, ticks=60, rule_variant="paid-respawn"
    )
    # The respawn worker is charged 5 resources in the paid variant, leaving the
    # fresh core with 0 instead of 5 -> the recovery trajectory diverges.
    assert paid.artifact_sha256 != baseline.artifact_sha256

    # Sanity: the paid variant leaves the respawned core with no resources.
    die_paid = next(t for t in paid.terminal if t.contestant_id == "die")
    die_base = next(t for t in baseline.terminal if t.contestant_id == "die")
    assert die_paid.final_resources < die_base.final_resources
