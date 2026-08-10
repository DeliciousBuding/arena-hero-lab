"""Fail-closed replication quality gates and deterministic evidence merge."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from arena_hero_research.assignment import AssignmentManifest
from arena_hero_research.contracts import (
    Hypothesis,
    HypothesisDirection,
    MissingDataPolicy,
    Outcome,
    OutcomeRole,
    Preregistration,
)
from arena_hero_research.execution import (
    DataRole,
    DroppedPair,
    PairedObservation,
    ReplicationResult,
    ReplicationResultStatus,
    ReplicationTask,
)
from arena_hero_research.validation import (
    require_float,
    require_identifier,
    require_int,
    require_sequence,
    require_sha256,
)
from arena_hero_sim.serialization import JsonValue, content_sha256


class ReplicationQualityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MergedOutcomePairs:
    outcome_name: str
    control: tuple[float, ...]
    treatment: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        if len(self.control) != len(self.treatment) or not self.control:
            raise ReplicationQualityError("merged outcome pairs must be non-empty and aligned")
        if any(not math.isfinite(item) for item in (*self.control, *self.treatment)):
            raise ReplicationQualityError("merged outcome pairs must be finite")
        object.__setattr__(self, "control", tuple(self.control))
        object.__setattr__(self, "treatment", tuple(self.treatment))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "outcome_name": self.outcome_name,
            "control": list(self.control),
            "treatment": list(self.treatment),
        }


@dataclass(frozen=True, slots=True, order=True)
class ReplicationOutcomeEvidence:
    replication_index: int
    outcome_name: str
    sample_size: int
    mean_difference: float
    meets_minimum_effect: bool
    direction_supported: bool

    def __post_init__(self) -> None:
        if self.replication_index < 0 or self.sample_size < 1:
            raise ValueError("replication evidence indices and sample size must be valid")
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        if not math.isfinite(self.mean_difference):
            raise ValueError("replication mean difference must be finite")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "replication_index": self.replication_index,
            "outcome_name": self.outcome_name,
            "sample_size": self.sample_size,
            "mean_difference": self.mean_difference,
            "meets_minimum_effect": self.meets_minimum_effect,
            "direction_supported": self.direction_supported,
        }


@dataclass(frozen=True, slots=True, order=True)
class ReplicationDroppedPair:
    replication_index: int
    outcome_name: str
    pair_id: str
    reason: str

    def __post_init__(self) -> None:
        if self.replication_index < 0:
            raise ValueError("replication_index must be non-negative")
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        object.__setattr__(self, "pair_id", require_identifier(self.pair_id, "pair_id"))
        if not self.reason.strip():
            raise ValueError("dropped-pair reason must not be empty")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "replication_index": self.replication_index,
            "outcome_name": self.outcome_name,
            "pair_id": self.pair_id,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ReplicationDroppedPair:
        return cls(
            replication_index=require_int(value["replication_index"], "replication_index"),
            outcome_name=str(value["outcome_name"]),
            pair_id=str(value["pair_id"]),
            reason=str(value["reason"]),
        )


@dataclass(frozen=True, slots=True)
class ReplicationMerge:
    schema_version: str
    data_role: DataRole
    preregistration_sha256: str
    design_id: str
    analysis_plan_sha256: str
    assignment_sha256: str
    expected_task_sha256s: tuple[str, ...]
    result_sha256s: tuple[str, ...]
    successful_replications: int
    merged_outcomes: tuple[MergedOutcomePairs, ...]
    replication_evidence: tuple[ReplicationOutcomeEvidence, ...]
    dropped_pairs: tuple[ReplicationDroppedPair, ...]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.replication-merge.v1":
            raise ReplicationQualityError("unsupported replication merge schema")
        if self.data_role not in {DataRole.CONFIRMATORY, DataRole.REPLICATION}:
            raise ReplicationQualityError("merged data must be held-out evidence")
        object.__setattr__(self, "design_id", require_identifier(self.design_id, "design_id"))
        for name in (
            "preregistration_sha256",
            "analysis_plan_sha256",
            "assignment_sha256",
            "canonical_sha256",
        ):
            object.__setattr__(self, name, require_sha256(getattr(self, name), name))
        expected = tuple(require_sha256(item, "task digest") for item in self.expected_task_sha256s)
        results = tuple(require_sha256(item, "result digest") for item in self.result_sha256s)
        if not expected or len(expected) != len(set(expected)):
            raise ReplicationQualityError("expected task digests must be non-empty and unique")
        if len(results) != len(set(results)):
            raise ReplicationQualityError("result digests must be unique")
        if self.successful_replications != len(results):
            raise ReplicationQualityError("successful replication count must match results")
        outcomes = tuple(self.merged_outcomes)
        outcome_names = {item.outcome_name for item in outcomes}
        if not outcomes or len(outcome_names) != len(outcomes):
            raise ReplicationQualityError("merged outcomes must be non-empty and unique")
        evidence = tuple(sorted(self.replication_evidence))
        evidence_keys = {(item.replication_index, item.outcome_name) for item in evidence}
        expected_evidence = {
            (index, outcome_name)
            for index in range(self.successful_replications)
            for outcome_name in outcome_names
        }
        if evidence_keys != expected_evidence or len(evidence_keys) != len(evidence):
            raise ReplicationQualityError(
                "replication evidence is incomplete or selectively reported"
            )
        drops = tuple(sorted(self.dropped_pairs))
        drop_keys = {(item.replication_index, item.outcome_name, item.pair_id) for item in drops}
        if len(drop_keys) != len(drops):
            raise ReplicationQualityError("merged dropped-pair records must be unique")
        object.__setattr__(self, "expected_task_sha256s", expected)
        object.__setattr__(self, "result_sha256s", results)
        object.__setattr__(self, "merged_outcomes", outcomes)
        object.__setattr__(self, "replication_evidence", evidence)
        object.__setattr__(self, "dropped_pairs", drops)

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "data_role": self.data_role.value,
            "preregistration_sha256": self.preregistration_sha256,
            "design_id": self.design_id,
            "analysis_plan_sha256": self.analysis_plan_sha256,
            "assignment_sha256": self.assignment_sha256,
            "expected_task_sha256s": list(self.expected_task_sha256s),
            "result_sha256s": list(self.result_sha256s),
            "successful_replications": self.successful_replications,
            "merged_outcomes": [item.to_dict() for item in self.merged_outcomes],
            "replication_evidence": [item.to_dict() for item in self.replication_evidence],
            "dropped_pairs": [item.to_dict() for item in self.dropped_pairs],
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    def observations(self) -> dict[str, tuple[tuple[float, ...], tuple[float, ...]]]:
        return {item.outcome_name: (item.control, item.treatment) for item in self.merged_outcomes}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ReplicationMerge:
        expected = require_sequence(value["expected_task_sha256s"], "expected_task_sha256s")
        results = require_sequence(value["result_sha256s"], "result_sha256s")
        outcomes = require_sequence(value["merged_outcomes"], "merged_outcomes")
        evidence = require_sequence(value["replication_evidence"], "replication_evidence")
        drops = require_sequence(value["dropped_pairs"], "dropped_pairs")
        parsed_outcomes: list[MergedOutcomePairs] = []
        parsed_evidence: list[ReplicationOutcomeEvidence] = []
        parsed_drops: list[ReplicationDroppedPair] = []
        for item in outcomes:
            if not isinstance(item, Mapping):
                raise TypeError("merged outcome must be a mapping")
            control = require_sequence(item["control"], "control")
            treatment = require_sequence(item["treatment"], "treatment")
            if not all(isinstance(value, int | float) for value in (*control, *treatment)):
                raise TypeError("merged observations must be numeric")
            parsed_outcomes.append(
                MergedOutcomePairs(
                    outcome_name=str(item["outcome_name"]),
                    control=tuple(require_float(number, "control") for number in control),
                    treatment=tuple(require_float(number, "treatment") for number in treatment),
                )
            )
        for item in evidence:
            if not isinstance(item, Mapping):
                raise TypeError("replication evidence must be a mapping")
            mean = item["mean_difference"]
            if not isinstance(mean, int | float):
                raise TypeError("mean_difference must be numeric")
            parsed_evidence.append(
                ReplicationOutcomeEvidence(
                    replication_index=require_int(item["replication_index"], "replication_index"),
                    outcome_name=str(item["outcome_name"]),
                    sample_size=require_int(item["sample_size"], "sample_size"),
                    mean_difference=mean,
                    meets_minimum_effect=item["meets_minimum_effect"] is True,
                    direction_supported=item["direction_supported"] is True,
                )
            )
        for item in drops:
            if not isinstance(item, Mapping):
                raise TypeError("dropped pair must be a mapping")
            parsed_drops.append(ReplicationDroppedPair.from_dict(item))
        return cls(
            schema_version=str(value["schema_version"]),
            data_role=DataRole(str(value["data_role"])),
            preregistration_sha256=str(value["preregistration_sha256"]),
            design_id=str(value["design_id"]),
            analysis_plan_sha256=str(value["analysis_plan_sha256"]),
            assignment_sha256=str(value["assignment_sha256"]),
            expected_task_sha256s=tuple(str(item) for item in expected),
            result_sha256s=tuple(str(item) for item in results),
            successful_replications=require_int(
                value["successful_replications"], "successful_replications"
            ),
            merged_outcomes=tuple(parsed_outcomes),
            replication_evidence=tuple(parsed_evidence),
            dropped_pairs=tuple(parsed_drops),
            canonical_sha256=str(value["canonical_sha256"]),
        )


def _supports_direction(effect: float, hypothesis: Hypothesis) -> tuple[bool, bool]:
    if hypothesis.direction is HypothesisDirection.GREATER:
        return effect > hypothesis.null_value, effect >= hypothesis.minimum_effect
    if hypothesis.direction is HypothesisDirection.LESS:
        return effect < hypothesis.null_value, effect <= -hypothesis.minimum_effect
    return effect != hypothesis.null_value, abs(effect) >= hypothesis.minimum_effect


def _quality_check_result(
    result: ReplicationResult,
    *,
    expected_outcomes: Mapping[str, Outcome],
    hypotheses: Mapping[str, Hypothesis],
    observations_per_replication: int,
) -> tuple[
    dict[str, tuple[list[float], list[float]]], list[DroppedPair], list[ReplicationOutcomeEvidence]
]:
    if result.status is not ReplicationResultStatus.COMPLETE:
        raise ReplicationQualityError("partial or failed replication results fail closed")
    by_outcome: dict[str, list[PairedObservation]] = defaultdict(list)
    for observation in result.observations:
        by_outcome[observation.outcome_name].append(observation)
    if set(by_outcome) != set(expected_outcomes):
        raise ReplicationQualityError("result must contain every confirmatory outcome")
    drop_index = {(item.outcome_name, item.pair_id): item for item in result.dropped_pairs}
    used_drops: set[tuple[str, str]] = set()
    cleaned: dict[str, tuple[list[float], list[float]]] = {}
    evidence: list[ReplicationOutcomeEvidence] = []
    for outcome_name, outcome in expected_outcomes.items():
        rows = by_outcome[outcome_name]
        if len(rows) != observations_per_replication:
            raise ReplicationQualityError("replication observation count does not match plan")
        control: list[float] = []
        treatment: list[float] = []
        for row in rows:
            key = (row.outcome_name, row.pair_id)
            if row.control is None or row.treatment is None:
                if outcome.missing_data_policy is MissingDataPolicy.FAIL:
                    raise ReplicationQualityError("missing pair violates preregistered FAIL policy")
                if key not in drop_index:
                    raise ReplicationQualityError("DROP_PAIR requires an explicit record")
                used_drops.add(key)
                continue
            if key in drop_index:
                raise ReplicationQualityError("complete pair cannot be declared dropped")
            control.append(row.control)
            treatment.append(row.treatment)
        if not control:
            raise ReplicationQualityError("each replication needs complete evidence per outcome")
        differences = [right - left for left, right in zip(control, treatment, strict=True)]
        effect = sum(differences) / len(differences)
        direction, minimum = _supports_direction(effect, hypotheses[outcome_name])
        evidence.append(
            ReplicationOutcomeEvidence(
                replication_index=result.task.replication_index,
                outcome_name=outcome_name,
                sample_size=len(control),
                mean_difference=effect,
                meets_minimum_effect=minimum,
                direction_supported=direction,
            )
        )
        cleaned[outcome_name] = (control, treatment)
    if used_drops != set(drop_index):
        raise ReplicationQualityError("dropped-pair records must exactly match missing pairs")
    return cleaned, list(result.dropped_pairs), evidence


def merge_replications(
    *,
    preregistration: Preregistration,
    assignment: AssignmentManifest,
    expected_tasks: Sequence[ReplicationTask],
    results: Sequence[ReplicationResult],
) -> ReplicationMerge:
    if not preregistration.verify() or not assignment.verify():
        raise ReplicationQualityError("research commitments failed digest verification")
    ordered_tasks = tuple(sorted(expected_tasks, key=lambda item: item.replication_index))
    task_by_id = {item.task_id: item for item in ordered_tasks}
    if not ordered_tasks or len(task_by_id) != len(ordered_tasks):
        raise ReplicationQualityError("expected tasks must be non-empty and unique")
    if len({item.data_role for item in ordered_tasks}) != 1:
        raise ReplicationQualityError("mixed confirmatory and replication roles are forbidden")
    result_by_task: dict[str, ReplicationResult] = {}
    for result in results:
        task_id = result.task.task_id
        if task_id in result_by_task:
            raise ReplicationQualityError("duplicate replication result")
        if task_id not in task_by_id:
            raise ReplicationQualityError("unexpected replication result")
        if result.task != task_by_id[task_id]:
            raise ReplicationQualityError("replication identity or provenance mismatch")
        if not result.verify():
            raise ReplicationQualityError("replication result digest verification failed")
        result_by_task[task_id] = result
    if set(task_by_id) - set(result_by_task):
        raise ReplicationQualityError("missing replication result")

    expected_outcomes = {
        item.name: item
        for item in preregistration.design.outcomes
        if item.role is not OutcomeRole.EXPLORATORY
    }
    hypotheses = {item.outcome_name: item for item in preregistration.hypotheses}
    merged = {name: ([], []) for name in expected_outcomes}
    all_drops: list[ReplicationDroppedPair] = []
    all_evidence: list[ReplicationOutcomeEvidence] = []
    ordered_results = tuple(result_by_task[item.task_id] for item in ordered_tasks)
    for result in ordered_results:
        cleaned, drops, evidence = _quality_check_result(
            result,
            expected_outcomes=expected_outcomes,
            hypotheses=hypotheses,
            observations_per_replication=preregistration.design.replication_plan.observations_per_replication,
        )
        for outcome_name, (control, treatment) in cleaned.items():
            merged[outcome_name][0].extend(control)
            merged[outcome_name][1].extend(treatment)
        all_drops.extend(
            ReplicationDroppedPair(
                replication_index=result.task.replication_index,
                outcome_name=item.outcome_name,
                pair_id=item.pair_id,
                reason=item.reason,
            )
            for item in drops
        )
        all_evidence.extend(evidence)

    plan = preregistration.design.replication_plan
    if len(ordered_results) < plan.minimum_successful:
        raise ReplicationQualityError("minimum successful replication count was not reached")
    required_pairs = preregistration.design.analysis_plan.planned_sample_size
    if any(len(control) < required_pairs for control, _ in merged.values()):
        raise ReplicationQualityError("complete pairs are below the planned sample size")
    merged_outcomes = tuple(
        MergedOutcomePairs(name, tuple(merged[name][0]), tuple(merged[name][1]))
        for name in sorted(merged)
    )
    task_digests = tuple(item.canonical_sha256 for item in ordered_tasks)
    result_digests = tuple(item.canonical_sha256 for item in ordered_results)
    role = ordered_tasks[0].data_role
    provisional = ReplicationMerge(
        schema_version="arena.research.replication-merge.v1",
        data_role=role,
        preregistration_sha256=preregistration.canonical_sha256,
        design_id=preregistration.design.design_id,
        analysis_plan_sha256=preregistration.design.analysis_plan.canonical_sha256(),
        assignment_sha256=assignment.canonical_sha256,
        expected_task_sha256s=task_digests,
        result_sha256s=result_digests,
        successful_replications=len(ordered_results),
        merged_outcomes=merged_outcomes,
        replication_evidence=tuple(all_evidence),
        dropped_pairs=tuple(all_drops),
        canonical_sha256="0" * 64,
    )
    return ReplicationMerge(
        schema_version=provisional.schema_version,
        data_role=provisional.data_role,
        preregistration_sha256=provisional.preregistration_sha256,
        design_id=provisional.design_id,
        analysis_plan_sha256=provisional.analysis_plan_sha256,
        assignment_sha256=provisional.assignment_sha256,
        expected_task_sha256s=provisional.expected_task_sha256s,
        result_sha256s=provisional.result_sha256s,
        successful_replications=provisional.successful_replications,
        merged_outcomes=provisional.merged_outcomes,
        replication_evidence=provisional.replication_evidence,
        dropped_pairs=provisional.dropped_pairs,
        canonical_sha256=content_sha256(provisional.payload()),
    )
