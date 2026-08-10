"""Replication-aware scientific conclusion artifacts without selective reporting."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from arena_hero_research.assignment import AssignmentManifest
from arena_hero_research.contracts import Hypothesis, HypothesisDirection, Preregistration
from arena_hero_research.replication import (
    ReplicationDroppedPair,
    ReplicationMerge,
    ReplicationOutcomeEvidence,
)
from arena_hero_research.results import ResultBundle
from arena_hero_research.validation import (
    freeze_public_metadata,
    require_float,
    require_identifier,
    require_int,
    require_json_mapping,
    require_sequence,
    require_sha256,
    require_text,
)
from arena_hero_sim.serialization import JsonValue, content_sha256


class ConclusionError(ValueError):
    pass


_LIMITATIONS = (
    "qualification requires effect magnitude, confidence interval, multiplicity-adjusted evidence, data quality, and replication support together",
    "replication support is evaluated against the preregistered minimum effect and direction",
    "a qualified artifact is evidence under the registered design, not universal proof",
    "all confirmatory and replication outcomes are retained, including null and adverse results",
)


@dataclass(frozen=True, slots=True, order=True)
class OutcomeConclusion:
    outcome_name: str
    mean_difference: float
    confidence_lower: float
    confidence_upper: float
    adjusted_p_value: float
    minimum_effect: float
    combined_effect_supported: bool
    confidence_interval_supported: bool
    multiplicity_adjusted_supported: bool
    supporting_replications: int
    total_replications: int
    qualified: bool
    estimator: str
    effect_size_method: str
    ci_method: str
    p_value_method: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        if not 0 <= self.adjusted_p_value <= 1 or self.minimum_effect < 0:
            raise ConclusionError("outcome conclusion probability or minimum effect is invalid")
        if not 0 <= self.supporting_replications <= self.total_replications:
            raise ConclusionError("replication support count is invalid")
        for name in ("estimator", "effect_size_method", "ci_method", "p_value_method"):
            object.__setattr__(self, name, require_text(getattr(self, name), name))
        expected = (
            self.combined_effect_supported
            and self.confidence_interval_supported
            and self.multiplicity_adjusted_supported
        )
        if self.qualified and not expected:
            raise ConclusionError("qualified outcome lacks combined scientific support")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "outcome_name": self.outcome_name,
            "mean_difference": self.mean_difference,
            "confidence_lower": self.confidence_lower,
            "confidence_upper": self.confidence_upper,
            "adjusted_p_value": self.adjusted_p_value,
            "minimum_effect": self.minimum_effect,
            "combined_effect_supported": self.combined_effect_supported,
            "confidence_interval_supported": self.confidence_interval_supported,
            "multiplicity_adjusted_supported": self.multiplicity_adjusted_supported,
            "supporting_replications": self.supporting_replications,
            "total_replications": self.total_replications,
            "qualified": self.qualified,
            "estimator": self.estimator,
            "effect_size_method": self.effect_size_method,
            "ci_method": self.ci_method,
            "p_value_method": self.p_value_method,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> OutcomeConclusion:
        return cls(
            outcome_name=str(value["outcome_name"]),
            mean_difference=require_float(value["mean_difference"], "mean_difference"),
            confidence_lower=require_float(value["confidence_lower"], "confidence_lower"),
            confidence_upper=require_float(value["confidence_upper"], "confidence_upper"),
            adjusted_p_value=require_float(value["adjusted_p_value"], "adjusted_p_value"),
            minimum_effect=require_float(value["minimum_effect"], "minimum_effect"),
            combined_effect_supported=value["combined_effect_supported"] is True,
            confidence_interval_supported=value["confidence_interval_supported"] is True,
            multiplicity_adjusted_supported=(value["multiplicity_adjusted_supported"] is True),
            supporting_replications=require_int(
                value["supporting_replications"], "supporting_replications"
            ),
            total_replications=require_int(value["total_replications"], "total_replications"),
            qualified=value["qualified"] is True,
            estimator=str(value["estimator"]),
            effect_size_method=str(value["effect_size_method"]),
            ci_method=str(value["ci_method"]),
            p_value_method=str(value["p_value_method"]),
        )


@dataclass(frozen=True, slots=True)
class ResearchConclusion:
    schema_version: str
    preregistration_sha256: str
    analysis_plan_sha256: str
    assignment_sha256: str
    replication_merge_sha256: str
    result_bundle_sha256: str
    successful_replications: int
    required_successful_replications: int
    outcomes: tuple[OutcomeConclusion, ...]
    replication_evidence: tuple[ReplicationOutcomeEvidence, ...]
    dropped_pairs: tuple[ReplicationDroppedPair, ...]
    limitations: tuple[str, ...]
    metadata: Mapping[str, JsonValue]
    qualified: bool
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.conclusion.v1":
            raise ConclusionError("unsupported conclusion schema")
        for name in (
            "preregistration_sha256",
            "analysis_plan_sha256",
            "assignment_sha256",
            "replication_merge_sha256",
            "result_bundle_sha256",
            "canonical_sha256",
        ):
            object.__setattr__(self, name, require_sha256(getattr(self, name), name))
        if not 1 <= self.required_successful_replications <= self.successful_replications:
            raise ConclusionError("successful replication requirement is not satisfied")
        outcomes = tuple(sorted(self.outcomes))
        if not outcomes or len({item.outcome_name for item in outcomes}) != len(outcomes):
            raise ConclusionError("conclusion outcomes must be non-empty and unique")
        evidence = tuple(sorted(self.replication_evidence))
        outcome_names = {item.outcome_name for item in outcomes}
        total_replications = {item.total_replications for item in outcomes}
        if total_replications != {self.successful_replications}:
            raise ConclusionError("outcome replication totals must match successful evidence")
        evidence_keys = {(item.replication_index, item.outcome_name) for item in evidence}
        expected_evidence = {
            (index, outcome_name)
            for index in range(self.successful_replications)
            for outcome_name in outcome_names
        }
        if evidence_keys != expected_evidence or len(evidence_keys) != len(evidence):
            raise ConclusionError("replication evidence is incomplete or selectively reported")
        for outcome in outcomes:
            expected_qualification = (
                outcome.combined_effect_supported
                and outcome.confidence_interval_supported
                and outcome.multiplicity_adjusted_supported
                and outcome.supporting_replications >= self.required_successful_replications
            )
            if outcome.qualified != expected_qualification:
                raise ConclusionError("outcome qualification does not match registered gates")
        drops = tuple(sorted(self.dropped_pairs))
        limitations = tuple(require_text(item, "limitation") for item in self.limitations)
        if limitations != _LIMITATIONS:
            raise ConclusionError("conclusion must disclose the registered limitations")
        if self.qualified != all(item.qualified for item in outcomes):
            raise ConclusionError("overall qualification must match every outcome conclusion")
        object.__setattr__(self, "outcomes", outcomes)
        object.__setattr__(self, "replication_evidence", evidence)
        object.__setattr__(self, "dropped_pairs", drops)
        object.__setattr__(self, "limitations", limitations)
        object.__setattr__(self, "metadata", freeze_public_metadata(self.metadata, "metadata"))

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "preregistration_sha256": self.preregistration_sha256,
            "analysis_plan_sha256": self.analysis_plan_sha256,
            "assignment_sha256": self.assignment_sha256,
            "replication_merge_sha256": self.replication_merge_sha256,
            "result_bundle_sha256": self.result_bundle_sha256,
            "successful_replications": self.successful_replications,
            "required_successful_replications": self.required_successful_replications,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "replication_evidence": [item.to_dict() for item in self.replication_evidence],
            "dropped_pairs": [item.to_dict() for item in self.dropped_pairs],
            "limitations": list(self.limitations),
            "metadata": dict(self.metadata),
            "qualified": self.qualified,
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ResearchConclusion:
        outcomes = require_sequence(value["outcomes"], "outcomes")
        evidence = require_sequence(value["replication_evidence"], "replication_evidence")
        drops = require_sequence(value["dropped_pairs"], "dropped_pairs")
        limitations = require_sequence(value["limitations"], "limitations")
        parsed_outcomes: list[OutcomeConclusion] = []
        parsed_evidence: list[ReplicationOutcomeEvidence] = []
        parsed_drops: list[ReplicationDroppedPair] = []
        for item in outcomes:
            if not isinstance(item, Mapping):
                raise TypeError("outcome conclusion must be a mapping")
            parsed_outcomes.append(OutcomeConclusion.from_dict(item))
        for item in evidence:
            if not isinstance(item, Mapping):
                raise TypeError("replication evidence must be a mapping")
            parsed_evidence.append(
                ReplicationOutcomeEvidence(
                    replication_index=require_int(item["replication_index"], "replication_index"),
                    outcome_name=str(item["outcome_name"]),
                    sample_size=require_int(item["sample_size"], "sample_size"),
                    mean_difference=require_float(item["mean_difference"], "mean_difference"),
                    meets_minimum_effect=item["meets_minimum_effect"] is True,
                    direction_supported=item["direction_supported"] is True,
                )
            )
        for item in drops:
            if not isinstance(item, Mapping):
                raise TypeError("replication dropped pair must be a mapping")
            parsed_drops.append(ReplicationDroppedPair.from_dict(item))
        return cls(
            schema_version=str(value["schema_version"]),
            preregistration_sha256=str(value["preregistration_sha256"]),
            analysis_plan_sha256=str(value["analysis_plan_sha256"]),
            assignment_sha256=str(value["assignment_sha256"]),
            replication_merge_sha256=str(value["replication_merge_sha256"]),
            result_bundle_sha256=str(value["result_bundle_sha256"]),
            successful_replications=require_int(
                value["successful_replications"], "successful_replications"
            ),
            required_successful_replications=require_int(
                value["required_successful_replications"],
                "required_successful_replications",
            ),
            outcomes=tuple(parsed_outcomes),
            replication_evidence=tuple(parsed_evidence),
            dropped_pairs=tuple(parsed_drops),
            limitations=tuple(str(item) for item in limitations),
            metadata=require_json_mapping(value["metadata"], "metadata"),
            qualified=value["qualified"] is True,
            canonical_sha256=str(value["canonical_sha256"]),
        )


def _combined_support(
    estimate_mean: float,
    confidence_lower: float,
    confidence_upper: float,
    hypothesis: Hypothesis,
) -> tuple[bool, bool]:
    if hypothesis.direction is HypothesisDirection.GREATER:
        return (
            estimate_mean >= hypothesis.minimum_effect,
            confidence_lower > hypothesis.null_value,
        )
    if hypothesis.direction is HypothesisDirection.LESS:
        return (
            estimate_mean <= -hypothesis.minimum_effect,
            confidence_upper < hypothesis.null_value,
        )
    return (
        abs(estimate_mean) >= hypothesis.minimum_effect,
        confidence_lower > hypothesis.null_value or confidence_upper < hypothesis.null_value,
    )


def create_research_conclusion(
    *,
    preregistration: Preregistration,
    assignment: AssignmentManifest,
    replication_merge: ReplicationMerge,
    result_bundle: ResultBundle,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ResearchConclusion:
    if not preregistration.verify() or not assignment.verify() or not replication_merge.verify():
        raise ConclusionError("research commitments failed digest verification")
    if replication_merge.preregistration_sha256 != preregistration.canonical_sha256:
        raise ConclusionError("replication merge preregistration mismatch")
    if replication_merge.assignment_sha256 != assignment.canonical_sha256:
        raise ConclusionError("replication merge assignment mismatch")
    if result_bundle.preregistration_sha256 != preregistration.canonical_sha256:
        raise ConclusionError("result bundle preregistration mismatch")
    if not result_bundle.publishable or result_bundle.status.value != "complete":
        raise ConclusionError("only complete publication-eligible result bundles may conclude")
    plan = preregistration.design.replication_plan
    if replication_merge.successful_replications < plan.minimum_successful:
        raise ConclusionError("minimum successful replication count was not reached")

    outcome_names = tuple(item.outcome_name for item in result_bundle.estimates)
    expected_outcomes = tuple(item.outcome_name for item in preregistration.hypotheses)
    if Counter(outcome_names) != Counter(expected_outcomes):
        raise ConclusionError("all confirmatory outcomes must be reported exactly once")
    evidence_keys = {
        (item.replication_index, item.outcome_name)
        for item in replication_merge.replication_evidence
    }
    expected_evidence = {
        (index, outcome) for index in range(plan.replications) for outcome in expected_outcomes
    }
    if evidence_keys != expected_evidence or len(evidence_keys) != len(
        replication_merge.replication_evidence
    ):
        raise ConclusionError("replication evidence is incomplete or selectively reported")

    hypotheses = {item.outcome_name: item for item in preregistration.hypotheses}
    evidence_by_outcome: dict[str, list[ReplicationOutcomeEvidence]] = {
        name: [] for name in expected_outcomes
    }
    for evidence in replication_merge.replication_evidence:
        evidence_by_outcome[evidence.outcome_name].append(evidence)
    conclusions: list[OutcomeConclusion] = []
    for estimate in result_bundle.estimates:
        hypothesis = hypotheses[estimate.outcome_name]
        effect_supported, interval_supported = _combined_support(
            estimate.mean_difference,
            estimate.confidence_lower,
            estimate.confidence_upper,
            hypothesis,
        )
        adjusted_supported = estimate.adjusted_p_value <= preregistration.design.analysis_plan.alpha
        supporting = sum(
            item.direction_supported and item.meets_minimum_effect
            for item in evidence_by_outcome[estimate.outcome_name]
        )
        quality = result_bundle.data_quality[estimate.outcome_name]
        quality_supported = (
            quality.complete_pairs >= preregistration.design.analysis_plan.planned_sample_size
        )
        qualified = (
            effect_supported
            and interval_supported
            and adjusted_supported
            and quality_supported
            and supporting >= plan.minimum_successful
        )
        conclusions.append(
            OutcomeConclusion(
                outcome_name=estimate.outcome_name,
                mean_difference=estimate.mean_difference,
                confidence_lower=estimate.confidence_lower,
                confidence_upper=estimate.confidence_upper,
                adjusted_p_value=estimate.adjusted_p_value,
                minimum_effect=hypothesis.minimum_effect,
                combined_effect_supported=effect_supported,
                confidence_interval_supported=interval_supported,
                multiplicity_adjusted_supported=adjusted_supported,
                supporting_replications=supporting,
                total_replications=plan.replications,
                qualified=qualified,
                estimator=estimate.estimator,
                effect_size_method=estimate.effect_size_method,
                ci_method=estimate.ci_method,
                p_value_method=estimate.p_value_method,
            )
        )
    public_metadata = freeze_public_metadata(metadata or {}, "metadata")
    overall = all(item.qualified for item in conclusions)
    provisional = ResearchConclusion(
        schema_version="arena.research.conclusion.v1",
        preregistration_sha256=preregistration.canonical_sha256,
        analysis_plan_sha256=preregistration.design.analysis_plan.canonical_sha256(),
        assignment_sha256=assignment.canonical_sha256,
        replication_merge_sha256=replication_merge.canonical_sha256,
        result_bundle_sha256=result_bundle.bundle_sha256(),
        successful_replications=replication_merge.successful_replications,
        required_successful_replications=plan.minimum_successful,
        outcomes=tuple(conclusions),
        replication_evidence=replication_merge.replication_evidence,
        dropped_pairs=replication_merge.dropped_pairs,
        limitations=_LIMITATIONS,
        metadata=public_metadata,
        qualified=overall,
        canonical_sha256="0" * 64,
    )
    return ResearchConclusion(
        schema_version=provisional.schema_version,
        preregistration_sha256=provisional.preregistration_sha256,
        analysis_plan_sha256=provisional.analysis_plan_sha256,
        assignment_sha256=provisional.assignment_sha256,
        replication_merge_sha256=provisional.replication_merge_sha256,
        result_bundle_sha256=provisional.result_bundle_sha256,
        successful_replications=provisional.successful_replications,
        required_successful_replications=provisional.required_successful_replications,
        outcomes=provisional.outcomes,
        replication_evidence=provisional.replication_evidence,
        dropped_pairs=provisional.dropped_pairs,
        limitations=provisional.limitations,
        metadata=provisional.metadata,
        qualified=provisional.qualified,
        canonical_sha256=content_sha256(provisional.payload()),
    )
