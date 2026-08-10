"""Deterministic approximate power simulation bound to a preregistered plan."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import NormalDist

from arena_hero_research.contracts import HypothesisDirection, Preregistration
from arena_hero_research.lifecycle import ResearchLifecycle, ResearchPhase
from arena_hero_research.validation import (
    require_float,
    require_identifier,
    require_int,
    require_sequence,
    require_sha256,
    require_text,
)
from arena_hero_sim.serialization import JsonValue, content_sha256


class PowerPlanningError(ValueError):
    pass


_METHOD = "paired-difference-monte-carlo-student-t-cornish-fisher"
_LIMITATIONS = (
    "synthetic paired differences are sampled from a normal distribution",
    "the decision rule uses a Cornish-Fisher approximation to Student t critical values",
    "the estimate has Monte Carlo error and is not an exact analytical power result",
    "the simulation must be frozen before confirmatory outcomes are inspected",
)


@dataclass(frozen=True, slots=True)
class MonteCarloPowerResult:
    schema_version: str
    preregistration_sha256: str
    analysis_plan_sha256: str
    outcome_name: str
    method: str
    simulation_seed: int
    simulations: int
    sample_size: int
    assumed_effect: float
    assumed_standard_deviation: float
    alpha: float
    direction: HypothesisDirection
    rejections: int
    estimated_power: float
    monte_carlo_standard_error: float
    limitations: tuple[str, ...]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.monte-carlo-power.v1":
            raise PowerPlanningError("unsupported power result schema")
        for name in ("preregistration_sha256", "analysis_plan_sha256", "canonical_sha256"):
            object.__setattr__(self, name, require_sha256(getattr(self, name), name))
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        if self.method != _METHOD:
            raise PowerPlanningError("unsupported power simulation method")
        if self.simulation_seed < 0 or self.simulations < 100 or self.sample_size < 2:
            raise PowerPlanningError("power simulation seed/count/sample size is invalid")
        if self.assumed_standard_deviation <= 0:
            raise PowerPlanningError("assumed standard deviation must be positive")
        if not 0 < self.alpha < 1 or not 0 <= self.rejections <= self.simulations:
            raise PowerPlanningError("power simulation alpha or rejection count is invalid")
        expected_power = self.rejections / self.simulations
        if not math.isclose(self.estimated_power, expected_power, abs_tol=1e-15):
            raise PowerPlanningError("estimated power must equal the rejection frequency")
        expected_error = math.sqrt(expected_power * (1 - expected_power) / self.simulations)
        if not math.isclose(self.monte_carlo_standard_error, expected_error, abs_tol=1e-15):
            raise PowerPlanningError("Monte Carlo standard error mismatch")
        limitations = tuple(require_text(item, "limitation") for item in self.limitations)
        if limitations != _LIMITATIONS:
            raise PowerPlanningError("power result must disclose the registered limitations")
        object.__setattr__(self, "limitations", limitations)

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "preregistration_sha256": self.preregistration_sha256,
            "analysis_plan_sha256": self.analysis_plan_sha256,
            "outcome_name": self.outcome_name,
            "method": self.method,
            "simulation_seed": self.simulation_seed,
            "simulations": self.simulations,
            "sample_size": self.sample_size,
            "assumed_effect": self.assumed_effect,
            "assumed_standard_deviation": self.assumed_standard_deviation,
            "alpha": self.alpha,
            "direction": self.direction.value,
            "rejections": self.rejections,
            "estimated_power": self.estimated_power,
            "monte_carlo_standard_error": self.monte_carlo_standard_error,
            "limitations": list(self.limitations),
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> MonteCarloPowerResult:
        limitations = require_sequence(value["limitations"], "limitations")
        return cls(
            schema_version=str(value["schema_version"]),
            preregistration_sha256=str(value["preregistration_sha256"]),
            analysis_plan_sha256=str(value["analysis_plan_sha256"]),
            outcome_name=str(value["outcome_name"]),
            method=str(value["method"]),
            simulation_seed=require_int(value["simulation_seed"], "simulation_seed"),
            simulations=require_int(value["simulations"], "simulations"),
            sample_size=require_int(value["sample_size"], "sample_size"),
            assumed_effect=require_float(value["assumed_effect"], "assumed_effect"),
            assumed_standard_deviation=require_float(
                value["assumed_standard_deviation"], "assumed_standard_deviation"
            ),
            alpha=require_float(value["alpha"], "alpha"),
            direction=HypothesisDirection(str(value["direction"])),
            rejections=require_int(value["rejections"], "rejections"),
            estimated_power=require_float(value["estimated_power"], "estimated_power"),
            monte_carlo_standard_error=require_float(
                value["monte_carlo_standard_error"], "monte_carlo_standard_error"
            ),
            limitations=tuple(str(item) for item in limitations),
            canonical_sha256=str(value["canonical_sha256"]),
        )


def _student_t_critical(probability: float, degrees_of_freedom: int) -> float:
    """Cornish-Fisher approximation; explicitly not an exact Student t quantile."""

    z = NormalDist().inv_cdf(probability)
    freedom = float(degrees_of_freedom)
    return (
        z
        + (z**3 + z) / (4 * freedom)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * freedom**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * freedom**3)
    )


def _rejects(sample: tuple[float, ...], *, alpha: float, direction: HypothesisDirection) -> bool:
    mean = sum(sample) / len(sample)
    variance = sum((item - mean) ** 2 for item in sample) / (len(sample) - 1)
    if variance == 0:
        if mean == 0:
            return False
        statistic = math.copysign(math.inf, mean)
    else:
        statistic = mean / math.sqrt(variance / len(sample))
    degrees_of_freedom = len(sample) - 1
    if direction is HypothesisDirection.GREATER:
        return statistic > _student_t_critical(1 - alpha, degrees_of_freedom)
    if direction is HypothesisDirection.LESS:
        return statistic < -_student_t_critical(1 - alpha, degrees_of_freedom)
    return abs(statistic) > _student_t_critical(1 - alpha / 2, degrees_of_freedom)


def simulate_monte_carlo_power(
    *,
    lifecycle: ResearchLifecycle,
    preregistration: Preregistration,
    outcome_name: str,
    assumed_effect: float,
    assumed_standard_deviation: float,
    simulations: int,
    simulation_seed: int,
) -> MonteCarloPowerResult:
    """Estimate power before confirmatory freeze using a disclosed approximate model."""

    if lifecycle.phase not in {ResearchPhase.PILOT, ResearchPhase.EXPLORATORY}:
        raise PowerPlanningError("power planning is forbidden after confirmatory freeze")
    if not lifecycle.verify() or not preregistration.verify():
        raise PowerPlanningError("research commitments failed digest verification")
    if lifecycle.preregistration_sha256 != preregistration.canonical_sha256:
        raise PowerPlanningError("lifecycle preregistration mismatch")
    outcome = require_identifier(outcome_name, "outcome_name")
    hypotheses = {item.outcome_name: item for item in preregistration.hypotheses}
    if outcome not in hypotheses:
        raise PowerPlanningError("power simulation requires a confirmatory outcome")
    if simulations < 100 or simulation_seed < 0:
        raise PowerPlanningError("simulations must be at least 100 and seed non-negative")
    if not math.isfinite(assumed_effect) or assumed_standard_deviation <= 0:
        raise PowerPlanningError("effect and standard deviation assumptions must be finite")
    plan = preregistration.design.analysis_plan
    rng = random.Random(simulation_seed)
    rejections = 0
    for _ in range(simulations):
        sample = tuple(
            rng.gauss(assumed_effect, assumed_standard_deviation)
            for _ in range(plan.planned_sample_size)
        )
        rejections += _rejects(
            sample,
            alpha=plan.alpha,
            direction=hypotheses[outcome].direction,
        )
    estimated_power = rejections / simulations
    provisional = MonteCarloPowerResult(
        schema_version="arena.research.monte-carlo-power.v1",
        preregistration_sha256=preregistration.canonical_sha256,
        analysis_plan_sha256=plan.canonical_sha256(),
        outcome_name=outcome,
        method=_METHOD,
        simulation_seed=simulation_seed,
        simulations=simulations,
        sample_size=plan.planned_sample_size,
        assumed_effect=assumed_effect,
        assumed_standard_deviation=assumed_standard_deviation,
        alpha=plan.alpha,
        direction=hypotheses[outcome].direction,
        rejections=rejections,
        estimated_power=estimated_power,
        monte_carlo_standard_error=math.sqrt(estimated_power * (1 - estimated_power) / simulations),
        limitations=_LIMITATIONS,
        canonical_sha256="0" * 64,
    )
    return MonteCarloPowerResult(
        schema_version=provisional.schema_version,
        preregistration_sha256=provisional.preregistration_sha256,
        analysis_plan_sha256=provisional.analysis_plan_sha256,
        outcome_name=provisional.outcome_name,
        method=provisional.method,
        simulation_seed=provisional.simulation_seed,
        simulations=provisional.simulations,
        sample_size=provisional.sample_size,
        assumed_effect=provisional.assumed_effect,
        assumed_standard_deviation=provisional.assumed_standard_deviation,
        alpha=provisional.alpha,
        direction=provisional.direction,
        rejections=provisional.rejections,
        estimated_power=provisional.estimated_power,
        monte_carlo_standard_error=provisional.monte_carlo_standard_error,
        limitations=provisional.limitations,
        canonical_sha256=content_sha256(provisional.payload()),
    )
