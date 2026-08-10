"""Versioned contracts for offline preregistered replication execution."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from arena_hero_research.assignment import AssignmentManifest
from arena_hero_research.contracts import Preregistration
from arena_hero_research.lifecycle import ResearchLifecycle, ResearchPhase
from arena_hero_research.validation import (
    freeze_public_metadata,
    require_identifier,
    require_int,
    require_json_mapping,
    require_sequence,
    require_sha256,
    require_text,
)
from arena_hero_sim.serialization import JsonValue, content_sha256


class ReplicationError(ValueError):
    pass


class DataRole(StrEnum):
    PILOT = "pilot"
    EXPLORATORY = "exploratory"
    CONFIRMATORY = "confirmatory"
    REPLICATION = "replication"


class ReplicationResultStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExecutionProvenance:
    frozen_config_sha256: str
    source_build_sha256: str
    input_data_sha256: str
    environment_sha256: str
    sbom_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "frozen_config_sha256",
            "source_build_sha256",
            "input_data_sha256",
            "environment_sha256",
            "sbom_sha256",
        ):
            object.__setattr__(self, name, require_sha256(getattr(self, name), name))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "frozen_config_sha256": self.frozen_config_sha256,
            "source_build_sha256": self.source_build_sha256,
            "input_data_sha256": self.input_data_sha256,
            "environment_sha256": self.environment_sha256,
            "sbom_sha256": self.sbom_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ExecutionProvenance:
        return cls(**{name: str(value[name]) for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class ReplicationTask:
    schema_version: str
    task_id: str
    study_id: str
    data_role: DataRole
    replication_index: int
    seed: int
    environment: str
    preregistration_sha256: str
    design_id: str
    analysis_plan_sha256: str
    assignment_sha256: str
    assignment_ids: tuple[str, ...]
    provenance: ExecutionProvenance
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.replication-task.v1":
            raise ReplicationError("unsupported replication task schema")
        object.__setattr__(self, "task_id", require_identifier(self.task_id, "task_id"))
        object.__setattr__(self, "study_id", require_identifier(self.study_id, "study_id"))
        if self.data_role not in {DataRole.CONFIRMATORY, DataRole.REPLICATION}:
            raise ReplicationError("replication tasks require held-out data roles")
        if self.replication_index < 0 or self.seed < 0:
            raise ReplicationError("replication index and seed must be non-negative")
        object.__setattr__(self, "environment", require_text(self.environment, "environment"))
        object.__setattr__(self, "design_id", require_identifier(self.design_id, "design_id"))
        for name in (
            "preregistration_sha256",
            "analysis_plan_sha256",
            "assignment_sha256",
            "canonical_sha256",
        ):
            object.__setattr__(self, name, require_sha256(getattr(self, name), name))
        assignment_ids = tuple(
            require_sha256(item, "assignment_id") for item in self.assignment_ids
        )
        if not assignment_ids or len(assignment_ids) != len(set(assignment_ids)):
            raise ReplicationError("assignment ids must be non-empty and unique")
        object.__setattr__(self, "assignment_ids", assignment_ids)

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "study_id": self.study_id,
            "data_role": self.data_role.value,
            "replication_index": self.replication_index,
            "seed": self.seed,
            "environment": self.environment,
            "preregistration_sha256": self.preregistration_sha256,
            "design_id": self.design_id,
            "analysis_plan_sha256": self.analysis_plan_sha256,
            "assignment_sha256": self.assignment_sha256,
            "assignment_ids": list(self.assignment_ids),
            "provenance": self.provenance.to_dict(),
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def create(
        cls,
        *,
        study_id: str,
        data_role: DataRole,
        replication_index: int,
        seed: int,
        environment: str,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
        assignment_ids: tuple[str, ...],
        provenance: ExecutionProvenance,
    ) -> ReplicationTask:
        identity = content_sha256(
            {
                "study_id": study_id,
                "data_role": data_role.value,
                "replication_index": replication_index,
                "seed": seed,
                "environment": environment,
                "assignment_sha256": assignment.canonical_sha256,
                "provenance": provenance.to_dict(),
            }
        )
        task_id = f"rep-{replication_index}-{identity[:16]}"
        provisional = cls(
            schema_version="arena.research.replication-task.v1",
            task_id=task_id,
            study_id=study_id,
            data_role=data_role,
            replication_index=replication_index,
            seed=seed,
            environment=environment,
            preregistration_sha256=preregistration.canonical_sha256,
            design_id=preregistration.design.design_id,
            analysis_plan_sha256=preregistration.design.analysis_plan.canonical_sha256(),
            assignment_sha256=assignment.canonical_sha256,
            assignment_ids=assignment_ids,
            provenance=provenance,
            canonical_sha256="0" * 64,
        )
        return cls(
            schema_version=provisional.schema_version,
            task_id=provisional.task_id,
            study_id=provisional.study_id,
            data_role=provisional.data_role,
            replication_index=provisional.replication_index,
            seed=provisional.seed,
            environment=provisional.environment,
            preregistration_sha256=provisional.preregistration_sha256,
            design_id=provisional.design_id,
            analysis_plan_sha256=provisional.analysis_plan_sha256,
            assignment_sha256=provisional.assignment_sha256,
            assignment_ids=provisional.assignment_ids,
            provenance=provisional.provenance,
            canonical_sha256=content_sha256(provisional.payload()),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ReplicationTask:
        ids = require_sequence(value["assignment_ids"], "assignment_ids")
        provenance = value["provenance"]
        if not isinstance(provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        return cls(
            schema_version=str(value["schema_version"]),
            task_id=str(value["task_id"]),
            study_id=str(value["study_id"]),
            data_role=DataRole(str(value["data_role"])),
            replication_index=require_int(value["replication_index"], "replication_index"),
            seed=require_int(value["seed"], "seed"),
            environment=str(value["environment"]),
            preregistration_sha256=str(value["preregistration_sha256"]),
            design_id=str(value["design_id"]),
            analysis_plan_sha256=str(value["analysis_plan_sha256"]),
            assignment_sha256=str(value["assignment_sha256"]),
            assignment_ids=tuple(str(item) for item in ids),
            provenance=ExecutionProvenance.from_dict(provenance),
            canonical_sha256=str(value["canonical_sha256"]),
        )


def build_replication_tasks(
    *,
    lifecycle: ResearchLifecycle,
    preregistration: Preregistration,
    assignment: AssignmentManifest,
    provenance_by_environment: Mapping[str, ExecutionProvenance],
) -> tuple[ReplicationTask, ...]:
    if not lifecycle.verify() or not preregistration.verify() or not assignment.verify():
        raise ReplicationError("research commitments failed digest verification")
    if lifecycle.preregistration_sha256 != preregistration.canonical_sha256:
        raise ReplicationError("lifecycle preregistration mismatch")
    if lifecycle.assignment_sha256 != assignment.canonical_sha256:
        raise ReplicationError("lifecycle assignment mismatch")
    role_by_phase = {
        ResearchPhase.CONFIRMATORY: DataRole.CONFIRMATORY,
        ResearchPhase.REPLICATION: DataRole.REPLICATION,
    }
    if lifecycle.phase not in role_by_phase:
        raise ReplicationError("tasks require confirmatory or replication phase")
    data_role = role_by_phase[lifecycle.phase]
    records_by_replication: dict[int, list[str]] = defaultdict(list)
    for record in assignment.records:
        records_by_replication[record.replication_index].append(record.assignment_id)
    plan = preregistration.design.replication_plan
    tasks: list[ReplicationTask] = []
    for index, seed in enumerate(plan.seeds):
        environment = plan.environments[index % len(plan.environments)]
        if environment not in provenance_by_environment:
            raise ReplicationError(f"missing provenance for environment {environment}")
        assignment_ids = tuple(sorted(records_by_replication.get(index, ())))
        if not assignment_ids:
            raise ReplicationError("assignment manifest is missing a replication")
        tasks.append(
            ReplicationTask.create(
                study_id=lifecycle.study_id,
                data_role=data_role,
                replication_index=index,
                seed=seed,
                environment=environment,
                preregistration=preregistration,
                assignment=assignment,
                assignment_ids=assignment_ids,
                provenance=provenance_by_environment[environment],
            )
        )
    if set(records_by_replication) != set(range(plan.replications)):
        raise ReplicationError("assignment manifest contains unexpected replications")
    return tuple(tasks)


@dataclass(frozen=True, slots=True, order=True)
class PairedObservation:
    outcome_name: str
    pair_id: str
    control: float | None
    treatment: float | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        object.__setattr__(self, "pair_id", require_identifier(self.pair_id, "pair_id"))
        for name in ("control", "treatment"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite when present")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "outcome_name": self.outcome_name,
            "pair_id": self.pair_id,
            "control": self.control,
            "treatment": self.treatment,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PairedObservation:
        control = value.get("control")
        treatment = value.get("treatment")
        if control is not None and not isinstance(control, int | float):
            raise TypeError("control must be numeric or null")
        if treatment is not None and not isinstance(treatment, int | float):
            raise TypeError("treatment must be numeric or null")
        return cls(
            outcome_name=str(value["outcome_name"]),
            pair_id=str(value["pair_id"]),
            control=None if control is None else float(control),
            treatment=None if treatment is None else float(treatment),
        )


@dataclass(frozen=True, slots=True, order=True)
class DroppedPair:
    outcome_name: str
    pair_id: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        object.__setattr__(self, "pair_id", require_identifier(self.pair_id, "pair_id"))
        object.__setattr__(self, "reason", require_text(self.reason, "reason"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "outcome_name": self.outcome_name,
            "pair_id": self.pair_id,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DroppedPair:
        return cls(
            outcome_name=str(value["outcome_name"]),
            pair_id=str(value["pair_id"]),
            reason=str(value["reason"]),
        )


@dataclass(frozen=True, slots=True)
class ReplicationResult:
    schema_version: str
    task: ReplicationTask
    status: ReplicationResultStatus
    observations: tuple[PairedObservation, ...]
    dropped_pairs: tuple[DroppedPair, ...]
    metadata: Mapping[str, JsonValue]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.replication-result.v1":
            raise ReplicationError("unsupported replication result schema")
        if not self.task.verify():
            raise ReplicationError("replication task digest verification failed")
        observations = tuple(sorted(self.observations))
        identities = {(item.outcome_name, item.pair_id) for item in observations}
        if len(identities) != len(observations):
            raise ReplicationError("replication result contains duplicate observations")
        drops = tuple(sorted(self.dropped_pairs))
        drop_ids = {(item.outcome_name, item.pair_id) for item in drops}
        if len(drop_ids) != len(drops):
            raise ReplicationError("replication result contains duplicate dropped pairs")
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "dropped_pairs", drops)
        object.__setattr__(self, "metadata", freeze_public_metadata(self.metadata, "metadata"))
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "task": self.task.to_dict(),
            "status": self.status.value,
            "observations": [item.to_dict() for item in self.observations],
            "dropped_pairs": [item.to_dict() for item in self.dropped_pairs],
            "metadata": dict(self.metadata),
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def create(
        cls,
        *,
        task: ReplicationTask,
        status: ReplicationResultStatus,
        observations: tuple[PairedObservation, ...],
        dropped_pairs: tuple[DroppedPair, ...] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> ReplicationResult:
        provisional = cls(
            schema_version="arena.research.replication-result.v1",
            task=task,
            status=status,
            observations=observations,
            dropped_pairs=dropped_pairs,
            metadata=metadata or {},
            canonical_sha256="0" * 64,
        )
        return cls(
            schema_version=provisional.schema_version,
            task=provisional.task,
            status=provisional.status,
            observations=provisional.observations,
            dropped_pairs=provisional.dropped_pairs,
            metadata=provisional.metadata,
            canonical_sha256=content_sha256(provisional.payload()),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ReplicationResult:
        task = value["task"]
        observations = require_sequence(value["observations"], "observations")
        drops = require_sequence(value["dropped_pairs"], "dropped_pairs")
        if not isinstance(task, Mapping):
            raise TypeError("task must be a mapping")
        parsed_observations = []
        parsed_drops = []
        for item in observations:
            if not isinstance(item, Mapping):
                raise TypeError("observation must be a mapping")
            parsed_observations.append(PairedObservation.from_dict(item))
        for item in drops:
            if not isinstance(item, Mapping):
                raise TypeError("dropped pair must be a mapping")
            parsed_drops.append(DroppedPair.from_dict(item))
        return cls(
            schema_version=str(value["schema_version"]),
            task=ReplicationTask.from_dict(task),
            status=ReplicationResultStatus(str(value["status"])),
            observations=tuple(parsed_observations),
            dropped_pairs=tuple(parsed_drops),
            metadata=require_json_mapping(value["metadata"], "metadata"),
            canonical_sha256=str(value["canonical_sha256"]),
        )
