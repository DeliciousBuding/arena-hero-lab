"""Unified hierarchical evidence workflow and immutable-ledger integration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from arena_hero_research.hierarchical import (
    ClusterMissingPolicy,
    ClusterObservation,
    RandomInterceptFit,
    cross_validate_random_intercept,
    fit_random_intercept_with_certificate,
    verify_solver_certificate,
)
from arena_hero_research.hierarchical_artifacts import (
    CROSS_VALIDATION_REPORT_SCHEMA,
    CrossValidationReportV1,
    CrossValidationStatus,
    DesignProfile,
    SolverCertificate,
    SolverEvidenceError,
    SolverStatus,
    ValidationScope,
)
from arena_hero_research.storage import (
    FrozenResearchRecord,
    ResearchLedgerStorage,
    ResearchLedgerTransaction,
    ResearchRecordKind,
)
from arena_hero_research.validation import require_identifier
from arena_hero_sim.serialization import quantized_content_sha256


class HierarchicalEvidenceError(SolverEvidenceError):
    """The fit, certificate, report, or durable references disagree."""


@dataclass(frozen=True, slots=True)
class HierarchicalAnalysisEvidence:
    """One acyclic Fit v2 -> Certificate v1 -> Report v1 evidence chain."""

    fit: RandomInterceptFit
    certificate: SolverCertificate
    report: CrossValidationReportV1

    def __post_init__(self) -> None:
        if not self.verify():
            raise HierarchicalEvidenceError("hierarchical evidence reference chain is invalid")

    def verify(self) -> bool:
        references_match = (
            self.fit.verify()
            and self.certificate.verify()
            and self.report.verify()
            and self.certificate.fit_schema_version == self.fit.schema_version
            and self.certificate.fit_sha256 == self.fit.canonical_sha256
            and self.report.source_input_sha256 == self.certificate.source_input_sha256
            and self.report.analysis_input_sha256 == self.certificate.analysis_input_sha256
            and self.report.fit_schema_version == self.fit.schema_version
            and self.report.fit_sha256 == self.fit.canonical_sha256
            and self.report.certificate_schema_version == self.certificate.schema_version
            and self.report.certificate_sha256 == self.certificate.canonical_sha256
        )
        if not references_match:
            return False
        fit_fields_match = (
            self.report.outcome_name == self.fit.outcome_name
            and self.report.control_level == self.fit.control_level
            and self.report.treatment_level == self.fit.treatment_level
            and self.report.cluster_count == self.fit.cluster_count
            and self.report.observation_count == self.fit.observation_count
            and self.report.path_b_effect == self.fit.treatment_effect
            and self.report.path_b_between_variance == self.fit.between_variance
            and self.report.path_b_error_variance == self.fit.error_variance
        )
        if not fit_fields_match:
            return False
        if self.certificate.solver_status is SolverStatus.VERIFIED_INTERIOR and not (
            self.certificate.has_verified_root_conditions()
        ):
            return False
        if self.certificate.solver_status is not SolverStatus.VERIFIED_INTERIOR:
            return (
                self.report.validation_scope is ValidationScope.NONE
                and self.report.status is CrossValidationStatus.INDETERMINATE
                and not self.report.passed
            )
        if self.report.status is CrossValidationStatus.FULLY_VALIDATED:
            return (
                self.report.design_profile is DesignProfile.PAIRED_1X1
                and self.report.validation_scope is ValidationScope.EFFECT_AND_VARIANCE
                and self.report.passed
            )
        return not self.report.passed

    def verify_recomputed(
        self,
        *,
        outcome_name: str,
        observations: Sequence[ClusterObservation],
        control_level: str = "control",
        treatment_level: str = "treatment",
        confidence_level: float = 0.95,
        missing_policy: ClusterMissingPolicy = ClusterMissingPolicy.FAIL,
    ) -> bool:
        if not verify_solver_certificate(
            certificate=self.certificate,
            fit=self.fit,
            outcome_name=outcome_name,
            observations=observations,
            control_level=control_level,
            treatment_level=treatment_level,
            confidence_level=confidence_level,
            missing_policy=missing_policy,
        ):
            return False
        try:
            recomputed = analyze_hierarchical_evidence(
                outcome_name=outcome_name,
                observations=observations,
                control_level=control_level,
                treatment_level=treatment_level,
                confidence_level=confidence_level,
                missing_policy=missing_policy,
            )
        except ValueError:
            return False
        return recomputed.report.to_dict() == self.report.to_dict()


def _versioned_cross_validation_report(
    *,
    legacy_report,
    fit: RandomInterceptFit,
    certificate: SolverCertificate,
) -> CrossValidationReportV1:
    profile = (
        DesignProfile.PAIRED_1X1
        if legacy_report.variance_validated
        else DesignProfile.BALANCED_REPEATED
        if legacy_report.balanced
        else DesignProfile.ALLOCATION_UNBALANCED
    )
    if certificate.solver_status is not SolverStatus.VERIFIED_INTERIOR:
        scope = ValidationScope.NONE
        status = CrossValidationStatus.INDETERMINATE
    elif legacy_report.variance_validated:
        scope = ValidationScope.EFFECT_AND_VARIANCE
        status = (
            CrossValidationStatus.FULLY_VALIDATED
            if legacy_report.effect_passed and legacy_report.variance_passed
            else CrossValidationStatus.FAILED
        )
    else:
        scope = ValidationScope.EFFECT_ONLY
        status = (
            CrossValidationStatus.EFFECT_DIAGNOSTIC
            if legacy_report.effect_passed
            else CrossValidationStatus.FAILED
        )
    provisional = CrossValidationReportV1(
        schema_version=CROSS_VALIDATION_REPORT_SCHEMA,
        source_input_sha256=certificate.source_input_sha256,
        analysis_input_sha256=certificate.analysis_input_sha256,
        fit_schema_version=fit.schema_version,
        fit_sha256=fit.canonical_sha256,
        certificate_schema_version=certificate.schema_version,
        certificate_sha256=certificate.canonical_sha256,
        design_profile=profile,
        validation_scope=scope,
        status=status,
        outcome_name=legacy_report.outcome_name,
        control_level=legacy_report.control_level,
        treatment_level=legacy_report.treatment_level,
        cluster_count=legacy_report.cluster_count,
        observation_count=legacy_report.observation_count,
        balanced=legacy_report.balanced,
        path_a_effect=legacy_report.path_a_effect,
        path_b_effect=legacy_report.path_b_effect,
        path_a_between_variance=legacy_report.path_a_between_variance,
        path_a_error_variance=legacy_report.path_a_error_variance,
        path_b_between_variance=legacy_report.path_b_between_variance,
        path_b_error_variance=legacy_report.path_b_error_variance,
        effect_absolute_difference=legacy_report.effect_absolute_difference,
        variance_absolute_difference=legacy_report.variance_absolute_difference,
        effect_tolerance=legacy_report.effect_tolerance,
        variance_tolerance=legacy_report.variance_tolerance,
        effect_passed=legacy_report.effect_passed,
        variance_validated=legacy_report.variance_validated,
        variance_passed=legacy_report.variance_passed,
        passed=status is CrossValidationStatus.FULLY_VALIDATED,
        authoritative=legacy_report.authoritative,
        canonical_sha256="0" * 64,
    )
    return replace(provisional, canonical_sha256=quantized_content_sha256(provisional.payload()))


def analyze_hierarchical_evidence(
    *,
    outcome_name: str,
    observations: Sequence[ClusterObservation],
    control_level: str = "control",
    treatment_level: str = "treatment",
    confidence_level: float = 0.95,
    missing_policy: ClusterMissingPolicy = ClusterMissingPolicy.FAIL,
) -> HierarchicalAnalysisEvidence:
    """Build the complete public evidence chain without changing Fit v2."""

    fit, certificate = fit_random_intercept_with_certificate(
        outcome_name=outcome_name,
        observations=observations,
        control_level=control_level,
        treatment_level=treatment_level,
        confidence_level=confidence_level,
        missing_policy=missing_policy,
    )
    if confidence_level != 0.95:
        raise HierarchicalEvidenceError(
            "CrossValidationReport v1 is bound to the existing 0.95 fit entrypoint"
        )
    legacy_report = cross_validate_random_intercept(
        outcome_name=outcome_name,
        observations=observations,
        control_level=control_level,
        treatment_level=treatment_level,
        missing_policy=missing_policy,
    )
    report = _versioned_cross_validation_report(
        legacy_report=legacy_report, fit=fit, certificate=certificate
    )
    return HierarchicalAnalysisEvidence(fit=fit, certificate=certificate, report=report)


def _evidence_records(
    *,
    study_id: str,
    analysis_id: str,
    evidence: HierarchicalAnalysisEvidence,
) -> tuple[FrozenResearchRecord, FrozenResearchRecord, FrozenResearchRecord]:
    normalized_study = require_identifier(study_id, "study_id")
    normalized_analysis = require_identifier(analysis_id, "analysis_id")
    if not evidence.verify():
        raise HierarchicalEvidenceError("cannot persist an invalid hierarchical evidence chain")
    return (
        FrozenResearchRecord.create(
            study_id=normalized_study,
            kind=ResearchRecordKind.HIERARCHICAL_FIT,
            subject_id=normalized_analysis,
            payload=evidence.fit.to_dict(),
        ),
        FrozenResearchRecord.create(
            study_id=normalized_study,
            kind=ResearchRecordKind.SOLVER_CERTIFICATE,
            subject_id=normalized_analysis,
            payload=evidence.certificate.to_dict(),
        ),
        FrozenResearchRecord.create(
            study_id=normalized_study,
            kind=ResearchRecordKind.CROSS_VALIDATION_REPORT,
            subject_id=normalized_analysis,
            payload=evidence.report.to_dict(),
        ),
    )


def commit_hierarchical_analysis_evidence(
    storage: ResearchLedgerStorage,
    *,
    operation_id: str,
    study_id: str,
    analysis_id: str,
    evidence: HierarchicalAnalysisEvidence,
    expected_head_sha256: str | None,
) -> ResearchLedgerTransaction:
    """Atomically append fit, certificate, and report in one ledger transaction."""

    records = _evidence_records(
        study_id=study_id,
        analysis_id=analysis_id,
        evidence=evidence,
    )
    return storage.commit(
        operation_id=operation_id,
        study_id=study_id,
        records=records,
        expected_head_sha256=expected_head_sha256,
    )


def load_hierarchical_analysis_evidence(
    storage: ResearchLedgerStorage,
    *,
    study_id: str,
    analysis_id: str,
) -> HierarchicalAnalysisEvidence:
    """Restore strict artifacts and verify every forward reference in the chain."""

    normalized_study = require_identifier(study_id, "study_id")
    normalized_analysis = require_identifier(analysis_id, "analysis_id")
    expected_kinds = {
        ResearchRecordKind.HIERARCHICAL_FIT,
        ResearchRecordKind.SOLVER_CERTIFICATE,
        ResearchRecordKind.CROSS_VALIDATION_REPORT,
    }
    state = storage.load()
    records = tuple(
        record
        for record in state.records
        if record.study_id == normalized_study
        and record.subject_id == normalized_analysis
        and record.kind in expected_kinds
    )
    by_kind = {record.kind: record for record in records}
    if len(records) != 3 or frozenset(by_kind) != frozenset(expected_kinds):
        raise HierarchicalEvidenceError(
            "durable hierarchical evidence requires exactly one fit, certificate, and report"
        )
    record_digests = frozenset(record.canonical_sha256 for record in records)
    matching_transactions = tuple(
        transaction
        for transaction in state.transactions
        if frozenset(transaction.record_sha256s) == record_digests
        and len(transaction.record_sha256s) == 3
    )
    if len(matching_transactions) != 1:
        raise HierarchicalEvidenceError(
            "fit, certificate, and report must be committed in one atomic transaction"
        )
    try:
        fit = RandomInterceptFit.from_dict(by_kind[ResearchRecordKind.HIERARCHICAL_FIT].payload)
        certificate = SolverCertificate.from_dict(
            by_kind[ResearchRecordKind.SOLVER_CERTIFICATE].payload
        )
        report = CrossValidationReportV1.from_dict(
            by_kind[ResearchRecordKind.CROSS_VALIDATION_REPORT].payload
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HierarchicalEvidenceError("durable hierarchical evidence is malformed") from error
    return HierarchicalAnalysisEvidence(fit=fit, certificate=certificate, report=report)
