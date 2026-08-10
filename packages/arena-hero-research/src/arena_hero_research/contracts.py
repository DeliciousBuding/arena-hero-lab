"""Immutable contracts for preregistered, reproducible research."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from arena_hero_sim.serialization import JsonValue, content_sha256

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _identifier(value: str, field_name: str) -> str:
    normalized = _text(value, field_name)
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase portable identifier")
    return normalized


def _sha(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


class HypothesisDirection(StrEnum):
    TWO_SIDED = "two-sided"
    GREATER = "greater"
    LESS = "less"


class OutcomeRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    EXPLORATORY = "exploratory"


class MissingDataPolicy(StrEnum):
    FAIL = "fail"
    DROP_PAIR = "drop-pair"


class MultipleComparisonPolicy(StrEnum):
    NONE = "none"
    BENJAMINI_HOCHBERG = "benjamini-hochberg"


class ResearchRunStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ResearchQuestion:
    question_id: str
    statement: str
    estimand: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "question_id", _identifier(self.question_id, "question_id"))
        object.__setattr__(self, "statement", _text(self.statement, "statement"))
        object.__setattr__(self, "estimand", _text(self.estimand, "estimand"))


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: str
    question_id: str
    outcome_name: str
    direction: HypothesisDirection
    null_value: float = 0.0
    minimum_effect: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "hypothesis_id", _identifier(self.hypothesis_id, "hypothesis_id"))
        object.__setattr__(self, "question_id", _identifier(self.question_id, "question_id"))
        object.__setattr__(self, "outcome_name", _identifier(self.outcome_name, "outcome_name"))
        if self.minimum_effect < 0:
            raise ValueError("minimum_effect must be non-negative")


@dataclass(frozen=True, slots=True)
class Factor:
    name: str
    levels: tuple[str, ...]
    randomized: bool
    assignment_unit: str
    blocking_keys: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "factor name"))
        levels = tuple(_text(item, "factor level") for item in self.levels)
        if len(levels) < 2 or len(levels) != len(set(levels)):
            raise ValueError("a factor requires at least two unique levels")
        blocking_keys = tuple(_identifier(item, "blocking key") for item in self.blocking_keys)
        if len(blocking_keys) != len(set(blocking_keys)):
            raise ValueError("blocking keys must be unique")
        object.__setattr__(self, "levels", levels)
        object.__setattr__(self, "assignment_unit", _text(self.assignment_unit, "assignment_unit"))
        object.__setattr__(self, "blocking_keys", blocking_keys)


@dataclass(frozen=True, slots=True)
class Outcome:
    name: str
    metric: str
    role: OutcomeRole
    unit: str
    higher_is_better: bool
    missing_data_policy: MissingDataPolicy

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "outcome name"))
        object.__setattr__(self, "metric", _text(self.metric, "metric"))
        object.__setattr__(self, "unit", _text(self.unit, "unit"))


@dataclass(frozen=True, slots=True)
class ReplicationPlan:
    replications: int
    minimum_successful: int
    seeds: tuple[int, ...]
    independent_seeds: bool
    observations_per_replication: int = 1
    environments: tuple[str, ...] = field(default_factory=lambda: ("local",))

    def __post_init__(self) -> None:
        if self.replications < 1:
            raise ValueError("replications must be positive")
        if not 1 <= self.minimum_successful <= self.replications:
            raise ValueError("minimum_successful must be within the replication count")
        if len(self.seeds) != self.replications:
            raise ValueError("one seed is required for each replication")
        if self.independent_seeds and len(self.seeds) != len(set(self.seeds)):
            raise ValueError("independent replication seeds must be unique")
        if any(seed < 0 for seed in self.seeds):
            raise ValueError("replication seeds must be non-negative")
        if self.observations_per_replication < 1:
            raise ValueError("observations_per_replication must be positive")
        environments = tuple(_text(item, "environment") for item in self.environments)
        if not environments or len(environments) != len(set(environments)):
            raise ValueError("environment classes must be non-empty and unique")
        object.__setattr__(self, "seeds", tuple(self.seeds))
        object.__setattr__(self, "environments", environments)

    @property
    def minimum_observations(self) -> int:
        return self.minimum_successful * self.observations_per_replication


@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    estimator: str
    effect_size: str
    confidence_level: float
    ci_method: str
    bootstrap_samples: int
    alpha: float
    multiple_comparison_policy: MultipleComparisonPolicy
    comparison_family: str
    missing_data_policy: MissingDataPolicy
    target_power: float
    minimum_detectable_effect: float
    planned_sample_size: int
    sample_size_method: str = "normal-approximation"

    def __post_init__(self) -> None:
        for field_name in (
            "estimator",
            "effect_size",
            "ci_method",
            "comparison_family",
            "sample_size_method",
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        if self.estimator != "paired-mean-difference":
            raise ValueError("only paired-mean-difference is currently implemented")
        if self.effect_size != "cohen-dz":
            raise ValueError("only cohen-dz is currently implemented")
        if self.ci_method != "paired-bootstrap-percentile":
            raise ValueError("only paired-bootstrap-percentile is currently implemented")
        if not 0 < self.confidence_level < 1 or not 0 < self.alpha < 1:
            raise ValueError("confidence_level and alpha must be between zero and one")
        if self.bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be at least 100")
        if not 0 < self.target_power < 1:
            raise ValueError("target_power must be between zero and one")
        if self.minimum_detectable_effect <= 0 or self.planned_sample_size < 2:
            raise ValueError("effect and sample-size planning values must be positive")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "estimator": self.estimator,
            "effect_size": self.effect_size,
            "confidence_level": self.confidence_level,
            "ci_method": self.ci_method,
            "bootstrap_samples": self.bootstrap_samples,
            "alpha": self.alpha,
            "multiple_comparison_policy": self.multiple_comparison_policy.value,
            "comparison_family": self.comparison_family,
            "missing_data_policy": self.missing_data_policy.value,
            "target_power": self.target_power,
            "minimum_detectable_effect": self.minimum_detectable_effect,
            "planned_sample_size": self.planned_sample_size,
            "sample_size_method": self.sample_size_method,
        }

    def canonical_sha256(self) -> str:
        return content_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class ExperimentDesign:
    design_id: str
    factors: tuple[Factor, ...]
    outcomes: tuple[Outcome, ...]
    pairing_keys: tuple[str, ...]
    randomization_unit: str
    seed_policy: str
    replication_plan: ReplicationPlan
    analysis_plan: AnalysisPlan

    def __post_init__(self) -> None:
        object.__setattr__(self, "design_id", _identifier(self.design_id, "design_id"))
        if not self.factors or not self.outcomes:
            raise ValueError("experiment design requires factors and outcomes")
        factor_names = [item.name for item in self.factors]
        if len(factor_names) != len(set(factor_names)):
            raise ValueError("factor names must be unique")
        if not any(item.randomized for item in self.factors):
            raise ValueError("at least one factor must be randomized")
        outcome_names = [item.name for item in self.outcomes]
        if len(outcome_names) != len(set(outcome_names)):
            raise ValueError("outcome names must be unique")
        if not any(item.role is OutcomeRole.PRIMARY for item in self.outcomes):
            raise ValueError("at least one primary outcome is required")
        if any(
            item.role is not OutcomeRole.EXPLORATORY
            and item.missing_data_policy is not self.analysis_plan.missing_data_policy
            for item in self.outcomes
        ):
            raise ValueError("confirmatory outcome missing-data policies must match analysis plan")
        confirmatory_count = sum(
            item.role in {OutcomeRole.PRIMARY, OutcomeRole.SECONDARY} for item in self.outcomes
        )
        if confirmatory_count > 1 and (
            self.analysis_plan.multiple_comparison_policy is MultipleComparisonPolicy.NONE
        ):
            raise ValueError("multiple confirmatory outcomes require a comparison policy")
        if self.replication_plan.minimum_observations < self.analysis_plan.planned_sample_size:
            raise ValueError("replication plan cannot supply the planned observation count")
        pairing_keys = tuple(_identifier(item, "pairing key") for item in self.pairing_keys)
        if not pairing_keys or len(pairing_keys) != len(set(pairing_keys)):
            raise ValueError("paired designs require unique pairing keys")
        object.__setattr__(self, "factors", tuple(self.factors))
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        object.__setattr__(self, "pairing_keys", pairing_keys)
        object.__setattr__(
            self, "randomization_unit", _text(self.randomization_unit, "randomization_unit")
        )
        object.__setattr__(self, "seed_policy", _text(self.seed_policy, "seed_policy"))


@dataclass(frozen=True, slots=True)
class Preregistration:
    schema_version: str
    question: ResearchQuestion
    hypotheses: tuple[Hypothesis, ...]
    design: ExperimentDesign
    registered_at: str
    canonical_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _text(self.schema_version, "schema_version"))
        registered_at = _text(self.registered_at, "registered_at")
        try:
            parsed_registered_at = datetime.fromisoformat(registered_at)
        except ValueError as exc:
            raise ValueError("registered_at must be an ISO-8601 timestamp") from exc
        if parsed_registered_at.tzinfo is None:
            raise ValueError("registered_at must include an explicit UTC offset")
        object.__setattr__(self, "registered_at", registered_at)
        object.__setattr__(
            self, "canonical_sha256", _sha(self.canonical_sha256, "canonical_sha256")
        )
        hypothesis_ids = [item.hypothesis_id for item in self.hypotheses]
        hypothesis_outcomes = [item.outcome_name for item in self.hypotheses]
        if not self.hypotheses or len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("hypotheses must be non-empty and uniquely identified")
        if len(hypothesis_outcomes) != len(set(hypothesis_outcomes)):
            raise ValueError("each outcome may have only one preregistered hypothesis")
        confirmatory_outcomes = {
            item.name for item in self.design.outcomes if item.role is not OutcomeRole.EXPLORATORY
        }
        if set(hypothesis_outcomes) != confirmatory_outcomes:
            raise ValueError(
                "hypotheses must cover every confirmatory outcome and no exploratory outcome"
            )
        object.__setattr__(self, "hypotheses", tuple(self.hypotheses))
        outcomes = {item.name for item in self.design.outcomes}
        for hypothesis in self.hypotheses:
            if hypothesis.question_id != self.question.question_id:
                raise ValueError("hypothesis question_id does not match the research question")
            if hypothesis.outcome_name not in outcomes:
                raise ValueError("hypothesis references an undeclared outcome")

    @classmethod
    def create(
        cls,
        *,
        question: ResearchQuestion,
        hypotheses: tuple[Hypothesis, ...],
        design: ExperimentDesign,
        registered_at: str,
    ) -> Preregistration:
        payload = preregistration_payload(question, hypotheses, design, registered_at)
        return cls(
            schema_version="arena.research.preregistration.v1",
            question=question,
            hypotheses=hypotheses,
            design=design,
            registered_at=registered_at,
            canonical_sha256=content_sha256(payload),
        )

    def verify(self) -> bool:
        payload = preregistration_payload(
            self.question, self.hypotheses, self.design, self.registered_at
        )
        return content_sha256(payload) == self.canonical_sha256


def preregistration_payload(
    question: ResearchQuestion,
    hypotheses: tuple[Hypothesis, ...],
    design: ExperimentDesign,
    registered_at: str,
) -> dict[str, JsonValue]:
    return {
        "schema_version": "arena.research.preregistration.v1",
        "question": {
            "question_id": question.question_id,
            "statement": question.statement,
            "estimand": question.estimand,
        },
        "hypotheses": [
            {
                "hypothesis_id": item.hypothesis_id,
                "question_id": item.question_id,
                "outcome_name": item.outcome_name,
                "direction": item.direction.value,
                "null_value": item.null_value,
                "minimum_effect": item.minimum_effect,
            }
            for item in hypotheses
        ],
        "design": {
            "design_id": design.design_id,
            "factors": [
                {
                    "name": item.name,
                    "levels": list(item.levels),
                    "randomized": item.randomized,
                    "assignment_unit": item.assignment_unit,
                    "blocking_keys": list(item.blocking_keys),
                }
                for item in design.factors
            ],
            "outcomes": [
                {
                    "name": item.name,
                    "metric": item.metric,
                    "role": item.role.value,
                    "unit": item.unit,
                    "higher_is_better": item.higher_is_better,
                    "missing_data_policy": item.missing_data_policy.value,
                }
                for item in design.outcomes
            ],
            "pairing_keys": list(design.pairing_keys),
            "randomization_unit": design.randomization_unit,
            "seed_policy": design.seed_policy,
            "replication_plan": {
                "replications": design.replication_plan.replications,
                "minimum_successful": design.replication_plan.minimum_successful,
                "seeds": list(design.replication_plan.seeds),
                "independent_seeds": design.replication_plan.independent_seeds,
                "observations_per_replication": design.replication_plan.observations_per_replication,
                "environments": list(design.replication_plan.environments),
            },
            "analysis_plan": design.analysis_plan.to_dict(),
        },
        "registered_at": registered_at,
    }
