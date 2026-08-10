"""Deterministic, preregistration-bound treatment assignment generation."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from arena_hero_research.contracts import Preregistration
from arena_hero_research.validation import (
    require_identifier,
    require_int,
    require_sequence,
    require_sha256,
    require_text,
)
from arena_hero_sim.serialization import JsonValue, content_sha256


class AssignmentError(ValueError):
    pass


@dataclass(frozen=True, slots=True, order=True)
class AssignmentUnit:
    scenario_id: str
    seat: int
    block_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", require_identifier(self.scenario_id, "scenario_id"))
        object.__setattr__(self, "block_id", require_identifier(self.block_id, "block_id"))
        if self.seat < 0:
            raise ValueError("seat must be non-negative")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "scenario_id": self.scenario_id,
            "seat": self.seat,
            "block_id": self.block_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> AssignmentUnit:
        return cls(
            scenario_id=str(value["scenario_id"]),
            seat=require_int(value["seat"], "seat"),
            block_id=str(value["block_id"]),
        )


@dataclass(frozen=True, slots=True, order=True)
class AssignmentRecord:
    replication_index: int
    seed: int
    environment: str
    treatment_factor: str
    treatment: str
    unit: AssignmentUnit

    def __post_init__(self) -> None:
        if self.replication_index < 0:
            raise ValueError("replication_index must be non-negative")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        object.__setattr__(self, "environment", require_text(self.environment, "environment"))
        object.__setattr__(
            self,
            "treatment_factor",
            require_identifier(self.treatment_factor, "treatment_factor"),
        )
        object.__setattr__(self, "treatment", require_text(self.treatment, "treatment"))

    @property
    def assignment_id(self) -> str:
        return content_sha256(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "replication_index": self.replication_index,
            "seed": self.seed,
            "environment": self.environment,
            "treatment_factor": self.treatment_factor,
            "treatment": self.treatment,
            "unit": self.unit.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> AssignmentRecord:
        unit = value["unit"]
        if not isinstance(unit, Mapping):
            raise TypeError("assignment unit must be a mapping")
        return cls(
            replication_index=require_int(value["replication_index"], "replication_index"),
            seed=require_int(value["seed"], "seed"),
            environment=str(value["environment"]),
            treatment_factor=str(value["treatment_factor"]),
            treatment=str(value["treatment"]),
            unit=AssignmentUnit.from_dict(unit),
        )


@dataclass(frozen=True, slots=True)
class AssignmentManifest:
    schema_version: str
    preregistration_sha256: str
    design_id: str
    analysis_plan_sha256: str
    treatment_factor: str
    treatment_levels: tuple[str, ...]
    records: tuple[AssignmentRecord, ...]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.assignment-manifest.v1":
            raise AssignmentError("unsupported assignment manifest schema")
        object.__setattr__(
            self,
            "preregistration_sha256",
            require_sha256(self.preregistration_sha256, "preregistration_sha256"),
        )
        object.__setattr__(self, "design_id", require_identifier(self.design_id, "design_id"))
        object.__setattr__(
            self,
            "analysis_plan_sha256",
            require_sha256(self.analysis_plan_sha256, "analysis_plan_sha256"),
        )
        object.__setattr__(
            self,
            "treatment_factor",
            require_identifier(self.treatment_factor, "treatment_factor"),
        )
        levels = tuple(require_text(item, "treatment level") for item in self.treatment_levels)
        if len(levels) < 2 or len(levels) != len(set(levels)):
            raise AssignmentError("treatment levels must contain at least two unique values")
        records = tuple(self.records)
        if not records:
            raise AssignmentError("assignment manifest requires records")
        if tuple(sorted(records)) != records:
            raise AssignmentError("assignment records must use canonical ordering")
        identities = {
            (item.replication_index, item.unit.scenario_id, item.unit.seat, item.unit.block_id)
            for item in records
        }
        if len(identities) != len(records):
            raise AssignmentError("assignment manifest contains duplicate assignment units")
        if any(item.treatment_factor != self.treatment_factor for item in records):
            raise AssignmentError("assignment record treatment factor mismatch")
        if any(item.treatment not in levels for item in records):
            raise AssignmentError("assignment record uses an undeclared treatment")
        object.__setattr__(self, "treatment_levels", levels)
        object.__setattr__(self, "records", records)
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )
        self._verify_balance()

    def _verify_balance(self) -> None:
        counts: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
        for record in self.records:
            counts[(record.replication_index, record.unit.block_id)][record.treatment] += 1
        for key, treatment_counts in counts.items():
            values = [treatment_counts[level] for level in self.treatment_levels]
            if max(values) - min(values) > 1:
                raise AssignmentError(f"unbalanced treatment allocation in replication/block {key}")

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "preregistration_sha256": self.preregistration_sha256,
            "design_id": self.design_id,
            "analysis_plan_sha256": self.analysis_plan_sha256,
            "treatment_factor": self.treatment_factor,
            "treatment_levels": list(self.treatment_levels),
            "records": [item.to_dict() for item in self.records],
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> AssignmentManifest:
        records = require_sequence(value["records"], "records")
        levels = require_sequence(value["treatment_levels"], "treatment_levels")
        parsed_records: list[AssignmentRecord] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise TypeError("assignment record must be a mapping")
            parsed_records.append(AssignmentRecord.from_dict(record))
        return cls(
            schema_version=str(value["schema_version"]),
            preregistration_sha256=str(value["preregistration_sha256"]),
            design_id=str(value["design_id"]),
            analysis_plan_sha256=str(value["analysis_plan_sha256"]),
            treatment_factor=str(value["treatment_factor"]),
            treatment_levels=tuple(str(item) for item in levels),
            records=tuple(parsed_records),
            canonical_sha256=str(value["canonical_sha256"]),
        )


def generate_assignments(
    preregistration: Preregistration,
    units: Sequence[AssignmentUnit],
    *,
    treatment_factor: str,
) -> AssignmentManifest:
    """Generate reproducible block-balanced assignments for all preregistered seeds."""

    if not preregistration.verify():
        raise AssignmentError("preregistration digest verification failed")
    factor_name = require_identifier(treatment_factor, "treatment_factor")
    factors = {item.name: item for item in preregistration.design.factors}
    try:
        factor = factors[factor_name]
    except KeyError as exc:
        raise AssignmentError("treatment factor is not declared") from exc
    if not factor.randomized:
        raise AssignmentError("treatment factor must be randomized")
    normalized_units = tuple(sorted(units))
    if not normalized_units or len(normalized_units) != len(set(normalized_units)):
        raise AssignmentError("assignment units must be non-empty and unique")

    by_block: dict[str, list[AssignmentUnit]] = defaultdict(list)
    for unit in normalized_units:
        by_block[unit.block_id].append(unit)

    records: list[AssignmentRecord] = []
    replication_plan = preregistration.design.replication_plan
    for replication_index, seed in enumerate(replication_plan.seeds):
        environment = replication_plan.environments[
            replication_index % len(replication_plan.environments)
        ]
        for block_id in sorted(by_block):
            block_units = list(sorted(by_block[block_id]))
            randomization_seed = int(
                content_sha256(
                    {
                        "schema_version": "arena.research.assignment-seed.v1",
                        "preregistration_sha256": preregistration.canonical_sha256,
                        "replication_index": replication_index,
                        "seed": seed,
                        "block_id": block_id,
                        "treatment_factor": factor_name,
                    }
                )[:16],
                16,
            )
            rng = random.Random(randomization_seed)
            rng.shuffle(block_units)
            levels = list(factor.levels)
            rng.shuffle(levels)
            for index, unit in enumerate(block_units):
                records.append(
                    AssignmentRecord(
                        replication_index=replication_index,
                        seed=seed,
                        environment=environment,
                        treatment_factor=factor_name,
                        treatment=levels[index % len(levels)],
                        unit=unit,
                    )
                )

    ordered = tuple(sorted(records))
    payload: dict[str, JsonValue] = {
        "schema_version": "arena.research.assignment-manifest.v1",
        "preregistration_sha256": preregistration.canonical_sha256,
        "design_id": preregistration.design.design_id,
        "analysis_plan_sha256": preregistration.design.analysis_plan.canonical_sha256(),
        "treatment_factor": factor_name,
        "treatment_levels": list(factor.levels),
        "records": [item.to_dict() for item in ordered],
    }
    return AssignmentManifest(
        schema_version="arena.research.assignment-manifest.v1",
        preregistration_sha256=preregistration.canonical_sha256,
        design_id=preregistration.design.design_id,
        analysis_plan_sha256=preregistration.design.analysis_plan.canonical_sha256(),
        treatment_factor=factor_name,
        treatment_levels=factor.levels,
        records=ordered,
        canonical_sha256=content_sha256(payload),
    )
