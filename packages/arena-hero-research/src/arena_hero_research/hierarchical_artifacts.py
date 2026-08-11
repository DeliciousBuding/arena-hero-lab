"""Strict content-addressed artifacts for hierarchical solver evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from arena_hero_research.validation import require_identifier, require_sha256, require_text
from arena_hero_sim.serialization import JsonValue, content_sha256

SOLVER_CERTIFICATE_SCHEMA = "arena.research.profile-reml-solver-certificate.v1"
CROSS_VALIDATION_REPORT_SCHEMA = "arena.research.cross-validation-report.v1"
FIT_SCHEMA = "arena.research.random-intercept-fit.v2"
ESTIMATOR = "random-intercept-reml"


class SolverEvidenceError(ValueError):
    """A strict solver-evidence artifact is malformed or internally inconsistent."""


class SolverStatus(StrEnum):
    VERIFIED_INTERIOR = "verified-interior"
    BOUNDARY = "boundary"
    INDETERMINATE = "indeterminate"


class DesignProfile(StrEnum):
    PAIRED_1X1 = "paired-1x1"
    BALANCED_REPEATED = "balanced-repeated"
    ALLOCATION_UNBALANCED = "allocation-unbalanced"


class ValidationScope(StrEnum):
    EFFECT_AND_VARIANCE = "effect-and-variance"
    EFFECT_ONLY = "effect-only"
    NONE = "none"


class CrossValidationStatus(StrEnum):
    FULLY_VALIDATED = "fully-validated"
    EFFECT_DIAGNOSTIC = "effect-diagnostic"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise SolverEvidenceError(f"{label} keys mismatch; missing={missing}, extra={extra}")


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise SolverEvidenceError(f"{field_name} must be a JSON string")
    try:
        return require_text(value, field_name)
    except ValueError as error:
        raise SolverEvidenceError(str(error)) from error


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise SolverEvidenceError(f"{field_name} must be a JSON string")
    try:
        return require_identifier(value, field_name)
    except ValueError as error:
        raise SolverEvidenceError(str(error)) from error


def _sha(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise SolverEvidenceError(f"{field_name} must be a JSON string")
    try:
        return require_sha256(value, field_name)
    except ValueError as error:
        raise SolverEvidenceError(str(error)) from error


def _float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SolverEvidenceError(f"{field_name} must be a JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise SolverEvidenceError(f"{field_name} must be finite")
    return result


def _optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _float(value, field_name)


def _int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SolverEvidenceError(f"{field_name} must be a JSON integer")
    return value


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise SolverEvidenceError(f"{field_name} must be a JSON boolean")
    return value


_EVALUATION_FIELDS = frozenset(
    {"log_lambda", "lambda_value", "valid", "objective", "invalid_reason"}
)


@dataclass(frozen=True, slots=True)
class SolverEvaluation:
    log_lambda: float
    lambda_value: float
    valid: bool
    objective: float | None
    invalid_reason: str | None

    def __post_init__(self) -> None:
        for field_name in ("log_lambda", "lambda_value"):
            value = getattr(self, field_name)
            if not math.isfinite(value):
                raise SolverEvidenceError(f"{field_name} must be finite")
        if self.lambda_value <= 0:
            raise SolverEvidenceError("lambda_value must be positive")
        try:
            expected_lambda = math.exp(self.log_lambda)
        except OverflowError as error:
            raise SolverEvidenceError("log_lambda is outside the supported finite range") from error
        if self.lambda_value != expected_lambda:
            raise SolverEvidenceError("lambda_value must equal exp(log_lambda)")
        if not isinstance(self.valid, bool):
            raise SolverEvidenceError("valid must be boolean")
        if self.valid:
            if self.objective is None or not math.isfinite(self.objective):
                raise SolverEvidenceError("valid evaluation requires a finite objective")
            if self.invalid_reason is not None:
                raise SolverEvidenceError("valid evaluation cannot carry invalid_reason")
        else:
            if self.objective is not None:
                raise SolverEvidenceError("invalid evaluation cannot carry an objective")
            if self.invalid_reason is None:
                raise SolverEvidenceError("invalid evaluation requires invalid_reason")
            reason = require_text(self.invalid_reason, "invalid_reason")
            allowed_reasons = {
                "non-finite-components",
                "singular-design",
                "non-positive-quadratic",
                "non-positive-or-precision-limited-residual",
                "non-finite-objective",
                "arithmetic-error",
            }
            if reason not in allowed_reasons:
                raise SolverEvidenceError("unsupported invalid evaluation reason")
            object.__setattr__(self, "invalid_reason", reason)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "log_lambda": self.log_lambda,
            "lambda_value": self.lambda_value,
            "valid": self.valid,
            "objective": self.objective,
            "invalid_reason": self.invalid_reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SolverEvaluation:
        _exact_keys(value, _EVALUATION_FIELDS, "solver evaluation")
        restored = cls(
            log_lambda=_float(value["log_lambda"], "log_lambda"),
            lambda_value=_float(value["lambda_value"], "lambda_value"),
            valid=_bool(value["valid"], "valid"),
            objective=_optional_float(value["objective"], "objective"),
            invalid_reason=(
                None
                if value["invalid_reason"] is None
                else _string(value["invalid_reason"], "invalid_reason")
            ),
        )
        if restored.to_dict() != dict(value):
            raise SolverEvidenceError("solver evaluation is not canonical")
        return restored


_CERTIFICATE_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "source_input_sha256",
        "analysis_input_sha256",
        "fit_schema_version",
        "fit_sha256",
        "optimizer",
        "initial_bracket",
        "final_bracket",
        "tolerance",
        "max_iterations",
        "iterations",
        "evaluation_count",
        "invalid_evaluation_count",
        "termination_reason",
        "candidate_log_lambda",
        "candidate_lambda",
        "candidate_objective",
        "profile_score",
        "profile_curvature",
        "kkt_residual",
        "kkt_tolerance",
        "backward_error",
        "newton_correction",
        "boundary",
        "precision_limited",
        "solver_status",
        "evaluations",
    }
)
_CERTIFICATE_FIELDS = _CERTIFICATE_PAYLOAD_FIELDS | {"canonical_sha256"}


@dataclass(frozen=True, slots=True)
class SolverCertificate:
    schema_version: str
    source_input_sha256: str
    analysis_input_sha256: str
    fit_schema_version: str
    fit_sha256: str
    optimizer: str
    initial_bracket: tuple[float, float]
    final_bracket: tuple[float, float]
    tolerance: float
    max_iterations: int
    iterations: int
    evaluation_count: int
    invalid_evaluation_count: int
    termination_reason: str
    candidate_log_lambda: float
    candidate_lambda: float
    candidate_objective: float
    profile_score: float
    profile_curvature: float
    kkt_residual: float
    kkt_tolerance: float
    backward_error: float
    newton_correction: float | None
    boundary: bool
    precision_limited: bool
    solver_status: SolverStatus
    evaluations: tuple[SolverEvaluation, ...]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != SOLVER_CERTIFICATE_SCHEMA:
            raise SolverEvidenceError("unsupported solver certificate schema")
        object.__setattr__(
            self,
            "source_input_sha256",
            require_sha256(self.source_input_sha256, "source_input_sha256"),
        )
        object.__setattr__(
            self,
            "analysis_input_sha256",
            require_sha256(self.analysis_input_sha256, "analysis_input_sha256"),
        )
        if self.fit_schema_version != FIT_SCHEMA:
            raise SolverEvidenceError("solver certificate must bind RandomInterceptFit v2")
        object.__setattr__(self, "fit_sha256", require_sha256(self.fit_sha256, "fit_sha256"))
        if self.optimizer != "bounded-golden-section":
            raise SolverEvidenceError("unsupported profile-REML optimizer")
        for field_name in (
            "tolerance",
            "candidate_log_lambda",
            "candidate_lambda",
            "candidate_objective",
            "profile_score",
            "profile_curvature",
            "kkt_residual",
            "kkt_tolerance",
            "backward_error",
        ):
            value = getattr(self, field_name)
            if not math.isfinite(value):
                raise SolverEvidenceError(f"{field_name} must be finite")
        if self.candidate_lambda <= 0 or self.tolerance <= 0 or self.kkt_tolerance <= 0:
            raise SolverEvidenceError("lambda and numerical tolerances must be positive")
        if self.kkt_residual < 0 or self.backward_error < 0:
            raise SolverEvidenceError("residual and backward error must be non-negative")
        if self.newton_correction is not None and not math.isfinite(self.newton_correction):
            raise SolverEvidenceError("newton_correction must be finite when present")
        for name, bracket in (
            ("initial_bracket", self.initial_bracket),
            ("final_bracket", self.final_bracket),
        ):
            if len(bracket) != 2 or not all(math.isfinite(item) for item in bracket):
                raise SolverEvidenceError(f"{name} must contain two finite bounds")
            if bracket[0] >= bracket[1]:
                raise SolverEvidenceError(f"{name} lower bound must be below upper bound")
        if not (
            self.initial_bracket[0]
            <= self.final_bracket[0]
            < self.final_bracket[1]
            <= self.initial_bracket[1]
        ):
            raise SolverEvidenceError("final bracket must be contained in initial bracket")
        if self.max_iterations < 1 or not 0 <= self.iterations <= self.max_iterations:
            raise SolverEvidenceError("invalid solver iteration counts")
        if self.evaluation_count != len(self.evaluations) or self.evaluation_count < 2:
            raise SolverEvidenceError("evaluation_count must match the retained trace")
        if self.evaluation_count != self.iterations + 2:
            raise SolverEvidenceError("golden-section trace must retain iterations + 2 evaluations")
        if self.initial_bracket != (-30.0, 30.0):
            raise SolverEvidenceError("solver certificate v1 requires the frozen [-30, 30] bracket")
        if self.termination_reason not in {"interval-tolerance", "max-iterations"}:
            raise SolverEvidenceError("unsupported solver termination reason")
        if not self.final_bracket[0] <= self.candidate_log_lambda <= self.final_bracket[1]:
            raise SolverEvidenceError("candidate must lie inside the final bracket")
        invalid_count = sum(not item.valid for item in self.evaluations)
        if self.invalid_evaluation_count != invalid_count:
            raise SolverEvidenceError("invalid_evaluation_count must match the retained trace")
        candidate_matches = sum(
            item.valid
            and item.log_lambda == self.candidate_log_lambda
            and item.lambda_value == self.candidate_lambda
            and item.objective == self.candidate_objective
            for item in self.evaluations
        )
        if candidate_matches != 1:
            raise SolverEvidenceError("candidate must identify one valid retained evaluation")
        object.__setattr__(
            self,
            "termination_reason",
            require_identifier(self.termination_reason, "termination_reason"),
        )
        if not isinstance(self.boundary, bool) or not isinstance(self.precision_limited, bool):
            raise SolverEvidenceError("boundary flags must be boolean")
        if self.solver_status is SolverStatus.VERIFIED_INTERIOR:
            if self.boundary or self.precision_limited:
                raise SolverEvidenceError(
                    "verified interior certificate cannot be boundary-limited"
                )
            if self.kkt_residual > self.kkt_tolerance or self.profile_curvature >= 0:
                raise SolverEvidenceError("verified interior certificate fails KKT conditions")
        elif self.solver_status is SolverStatus.BOUNDARY:
            if not self.boundary:
                raise SolverEvidenceError("boundary status requires boundary=true")
        elif self.solver_status is SolverStatus.INDETERMINATE and not self.precision_limited:
            raise SolverEvidenceError("indeterminate status requires an explicit precision limit")
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "source_input_sha256": self.source_input_sha256,
            "analysis_input_sha256": self.analysis_input_sha256,
            "fit_schema_version": self.fit_schema_version,
            "fit_sha256": self.fit_sha256,
            "optimizer": self.optimizer,
            "initial_bracket": list(self.initial_bracket),
            "final_bracket": list(self.final_bracket),
            "tolerance": self.tolerance,
            "max_iterations": self.max_iterations,
            "iterations": self.iterations,
            "evaluation_count": self.evaluation_count,
            "invalid_evaluation_count": self.invalid_evaluation_count,
            "termination_reason": self.termination_reason,
            "candidate_log_lambda": self.candidate_log_lambda,
            "candidate_lambda": self.candidate_lambda,
            "candidate_objective": self.candidate_objective,
            "profile_score": self.profile_score,
            "profile_curvature": self.profile_curvature,
            "kkt_residual": self.kkt_residual,
            "kkt_tolerance": self.kkt_tolerance,
            "backward_error": self.backward_error,
            "newton_correction": self.newton_correction,
            "boundary": self.boundary,
            "precision_limited": self.precision_limited,
            "solver_status": self.solver_status.value,
            "evaluations": [item.to_dict() for item in self.evaluations],
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SolverCertificate:
        _exact_keys(value, _CERTIFICATE_FIELDS, "solver certificate")
        initial = value["initial_bracket"]
        final = value["final_bracket"]
        evaluations_value = value["evaluations"]
        if not isinstance(initial, list) or len(initial) != 2:
            raise SolverEvidenceError("initial_bracket must be a two-item JSON list")
        if not isinstance(final, list) or len(final) != 2:
            raise SolverEvidenceError("final_bracket must be a two-item JSON list")
        if not isinstance(evaluations_value, list):
            raise SolverEvidenceError("evaluations must be a JSON list")
        evaluations: list[SolverEvaluation] = []
        for item in evaluations_value:
            if not isinstance(item, Mapping):
                raise SolverEvidenceError("each solver evaluation must be a JSON object")
            evaluations.append(SolverEvaluation.from_dict(item))
        restored = cls(
            schema_version=_string(value["schema_version"], "schema_version"),
            source_input_sha256=_sha(value["source_input_sha256"], "source_input_sha256"),
            analysis_input_sha256=_sha(value["analysis_input_sha256"], "analysis_input_sha256"),
            fit_schema_version=_string(value["fit_schema_version"], "fit_schema_version"),
            fit_sha256=_sha(value["fit_sha256"], "fit_sha256"),
            optimizer=_string(value["optimizer"], "optimizer"),
            initial_bracket=(
                _float(initial[0], "initial_bracket[0]"),
                _float(initial[1], "initial_bracket[1]"),
            ),
            final_bracket=(
                _float(final[0], "final_bracket[0]"),
                _float(final[1], "final_bracket[1]"),
            ),
            tolerance=_float(value["tolerance"], "tolerance"),
            max_iterations=_int(value["max_iterations"], "max_iterations"),
            iterations=_int(value["iterations"], "iterations"),
            evaluation_count=_int(value["evaluation_count"], "evaluation_count"),
            invalid_evaluation_count=_int(
                value["invalid_evaluation_count"], "invalid_evaluation_count"
            ),
            termination_reason=_string(value["termination_reason"], "termination_reason"),
            candidate_log_lambda=_float(value["candidate_log_lambda"], "candidate_log_lambda"),
            candidate_lambda=_float(value["candidate_lambda"], "candidate_lambda"),
            candidate_objective=_float(value["candidate_objective"], "candidate_objective"),
            profile_score=_float(value["profile_score"], "profile_score"),
            profile_curvature=_float(value["profile_curvature"], "profile_curvature"),
            kkt_residual=_float(value["kkt_residual"], "kkt_residual"),
            kkt_tolerance=_float(value["kkt_tolerance"], "kkt_tolerance"),
            backward_error=_float(value["backward_error"], "backward_error"),
            newton_correction=_optional_float(value["newton_correction"], "newton_correction"),
            boundary=_bool(value["boundary"], "boundary"),
            precision_limited=_bool(value["precision_limited"], "precision_limited"),
            solver_status=SolverStatus(_string(value["solver_status"], "solver_status")),
            evaluations=tuple(evaluations),
            canonical_sha256=_sha(value["canonical_sha256"], "canonical_sha256"),
        )
        if restored.to_dict() != dict(value):
            raise SolverEvidenceError("solver certificate is not canonical schema v1")
        if not restored.verify():
            raise SolverEvidenceError("solver certificate digest verification failed")
        return restored


_REPORT_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "source_input_sha256",
        "analysis_input_sha256",
        "fit_schema_version",
        "fit_sha256",
        "certificate_schema_version",
        "certificate_sha256",
        "design_profile",
        "validation_scope",
        "status",
        "outcome_name",
        "control_level",
        "treatment_level",
        "cluster_count",
        "observation_count",
        "balanced",
        "path_a_effect",
        "path_b_effect",
        "path_a_between_variance",
        "path_a_error_variance",
        "path_b_between_variance",
        "path_b_error_variance",
        "effect_absolute_difference",
        "variance_absolute_difference",
        "effect_tolerance",
        "variance_tolerance",
        "effect_passed",
        "variance_validated",
        "variance_passed",
        "passed",
        "authoritative",
    }
)
_REPORT_FIELDS = _REPORT_PAYLOAD_FIELDS | {"canonical_sha256"}


@dataclass(frozen=True, slots=True)
class CrossValidationReportV1:
    schema_version: str
    source_input_sha256: str
    analysis_input_sha256: str
    fit_schema_version: str
    fit_sha256: str
    certificate_schema_version: str
    certificate_sha256: str
    design_profile: DesignProfile
    validation_scope: ValidationScope
    status: CrossValidationStatus
    outcome_name: str
    control_level: str
    treatment_level: str
    cluster_count: int
    observation_count: int
    balanced: bool
    path_a_effect: float
    path_b_effect: float
    path_a_between_variance: float
    path_a_error_variance: float
    path_b_between_variance: float
    path_b_error_variance: float
    effect_absolute_difference: float
    variance_absolute_difference: float
    effect_tolerance: float
    variance_tolerance: float
    effect_passed: bool
    variance_validated: bool
    variance_passed: bool
    passed: bool
    authoritative: str
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != CROSS_VALIDATION_REPORT_SCHEMA:
            raise SolverEvidenceError("unsupported cross-validation report schema")
        object.__setattr__(
            self,
            "source_input_sha256",
            require_sha256(self.source_input_sha256, "source_input_sha256"),
        )
        object.__setattr__(
            self,
            "analysis_input_sha256",
            require_sha256(self.analysis_input_sha256, "analysis_input_sha256"),
        )
        if self.fit_schema_version != FIT_SCHEMA:
            raise SolverEvidenceError("cross-validation report must bind RandomInterceptFit v2")
        object.__setattr__(self, "fit_sha256", require_sha256(self.fit_sha256, "fit_sha256"))
        if self.certificate_schema_version != SOLVER_CERTIFICATE_SCHEMA:
            raise SolverEvidenceError("cross-validation report must bind SolverCertificate v1")
        object.__setattr__(
            self,
            "certificate_sha256",
            require_sha256(self.certificate_sha256, "certificate_sha256"),
        )
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        object.__setattr__(self, "control_level", require_text(self.control_level, "control_level"))
        object.__setattr__(
            self, "treatment_level", require_text(self.treatment_level, "treatment_level")
        )
        if self.control_level == self.treatment_level:
            raise SolverEvidenceError("control and treatment levels must differ")
        if self.cluster_count < 3 or self.observation_count < 3:
            raise SolverEvidenceError("cross-validation requires at least three clusters")
        for name in (
            "path_a_effect",
            "path_b_effect",
            "path_a_between_variance",
            "path_a_error_variance",
            "path_b_between_variance",
            "path_b_error_variance",
            "effect_absolute_difference",
            "variance_absolute_difference",
            "effect_tolerance",
            "variance_tolerance",
        ):
            if not math.isfinite(getattr(self, name)):
                raise SolverEvidenceError(f"{name} must be finite")
        for name in (
            "balanced",
            "effect_passed",
            "variance_validated",
            "variance_passed",
            "passed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise SolverEvidenceError(f"{name} must be boolean")
        if (
            self.effect_absolute_difference < 0
            or self.variance_absolute_difference < 0
            or self.effect_tolerance <= 0
            or self.variance_tolerance <= 0
        ):
            raise SolverEvidenceError("differences must be non-negative and tolerances positive")
        if self.effect_absolute_difference != abs(self.path_a_effect - self.path_b_effect):
            raise SolverEvidenceError("effect_absolute_difference is not derived canonically")
        expected_variance_difference = max(
            abs(self.path_a_between_variance - self.path_b_between_variance),
            abs(self.path_a_error_variance - self.path_b_error_variance),
        )
        if self.variance_absolute_difference != expected_variance_difference:
            raise SolverEvidenceError("variance_absolute_difference is not derived canonically")
        if self.effect_passed != (self.effect_absolute_difference <= self.effect_tolerance):
            raise SolverEvidenceError("effect_passed is inconsistent with its tolerance")
        if self.variance_passed != (
            self.variance_validated and self.variance_absolute_difference <= self.variance_tolerance
        ):
            raise SolverEvidenceError("variance_passed is inconsistent with its tolerance")
        expected_profile = (
            DesignProfile.PAIRED_1X1
            if self.variance_validated
            else DesignProfile.BALANCED_REPEATED
            if self.balanced
            else DesignProfile.ALLOCATION_UNBALANCED
        )
        if self.design_profile is not expected_profile:
            raise SolverEvidenceError("design_profile disagrees with the observed allocation")
        if self.validation_scope is ValidationScope.EFFECT_AND_VARIANCE:
            if not self.variance_validated or self.design_profile is not DesignProfile.PAIRED_1X1:
                raise SolverEvidenceError("effect-and-variance scope is limited to paired-1x1")
        elif self.validation_scope is ValidationScope.EFFECT_ONLY and self.variance_validated:
            raise SolverEvidenceError("paired-1x1 reports cannot downgrade to effect-only")
        elif (
            self.validation_scope is ValidationScope.NONE
            and self.status is not CrossValidationStatus.INDETERMINATE
        ):
            raise SolverEvidenceError("scope=none requires indeterminate status")
        expected_status = CrossValidationStatus.FAILED
        if self.validation_scope is ValidationScope.NONE:
            expected_status = CrossValidationStatus.INDETERMINATE
        elif self.effect_passed and self.validation_scope is ValidationScope.EFFECT_ONLY:
            expected_status = CrossValidationStatus.EFFECT_DIAGNOSTIC
        elif self.effect_passed and self.variance_passed:
            expected_status = CrossValidationStatus.FULLY_VALIDATED
        if self.status is not expected_status:
            raise SolverEvidenceError(
                "cross-validation status is inconsistent with validation results"
            )
        if self.passed != (self.status is CrossValidationStatus.FULLY_VALIDATED):
            raise SolverEvidenceError("passed=true is reserved for fully-validated reports")
        if self.authoritative != ESTIMATOR:
            raise SolverEvidenceError("cross-validation authority must be the REML estimator")
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "source_input_sha256": self.source_input_sha256,
            "analysis_input_sha256": self.analysis_input_sha256,
            "fit_schema_version": self.fit_schema_version,
            "fit_sha256": self.fit_sha256,
            "certificate_schema_version": self.certificate_schema_version,
            "certificate_sha256": self.certificate_sha256,
            "design_profile": self.design_profile.value,
            "validation_scope": self.validation_scope.value,
            "status": self.status.value,
            "outcome_name": self.outcome_name,
            "control_level": self.control_level,
            "treatment_level": self.treatment_level,
            "cluster_count": self.cluster_count,
            "observation_count": self.observation_count,
            "balanced": self.balanced,
            "path_a_effect": self.path_a_effect,
            "path_b_effect": self.path_b_effect,
            "path_a_between_variance": self.path_a_between_variance,
            "path_a_error_variance": self.path_a_error_variance,
            "path_b_between_variance": self.path_b_between_variance,
            "path_b_error_variance": self.path_b_error_variance,
            "effect_absolute_difference": self.effect_absolute_difference,
            "variance_absolute_difference": self.variance_absolute_difference,
            "effect_tolerance": self.effect_tolerance,
            "variance_tolerance": self.variance_tolerance,
            "effect_passed": self.effect_passed,
            "variance_validated": self.variance_validated,
            "variance_passed": self.variance_passed,
            "passed": self.passed,
            "authoritative": self.authoritative,
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> CrossValidationReportV1:
        _exact_keys(value, _REPORT_FIELDS, "cross-validation report")
        restored = cls(
            schema_version=_string(value["schema_version"], "schema_version"),
            source_input_sha256=_sha(value["source_input_sha256"], "source_input_sha256"),
            analysis_input_sha256=_sha(value["analysis_input_sha256"], "analysis_input_sha256"),
            fit_schema_version=_string(value["fit_schema_version"], "fit_schema_version"),
            fit_sha256=_sha(value["fit_sha256"], "fit_sha256"),
            certificate_schema_version=_string(
                value["certificate_schema_version"], "certificate_schema_version"
            ),
            certificate_sha256=_sha(value["certificate_sha256"], "certificate_sha256"),
            design_profile=DesignProfile(_string(value["design_profile"], "design_profile")),
            validation_scope=ValidationScope(
                _string(value["validation_scope"], "validation_scope")
            ),
            status=CrossValidationStatus(_string(value["status"], "status")),
            outcome_name=_identifier(value["outcome_name"], "outcome_name"),
            control_level=_string(value["control_level"], "control_level"),
            treatment_level=_string(value["treatment_level"], "treatment_level"),
            cluster_count=_int(value["cluster_count"], "cluster_count"),
            observation_count=_int(value["observation_count"], "observation_count"),
            balanced=_bool(value["balanced"], "balanced"),
            path_a_effect=_float(value["path_a_effect"], "path_a_effect"),
            path_b_effect=_float(value["path_b_effect"], "path_b_effect"),
            path_a_between_variance=_float(
                value["path_a_between_variance"], "path_a_between_variance"
            ),
            path_a_error_variance=_float(value["path_a_error_variance"], "path_a_error_variance"),
            path_b_between_variance=_float(
                value["path_b_between_variance"], "path_b_between_variance"
            ),
            path_b_error_variance=_float(value["path_b_error_variance"], "path_b_error_variance"),
            effect_absolute_difference=_float(
                value["effect_absolute_difference"], "effect_absolute_difference"
            ),
            variance_absolute_difference=_float(
                value["variance_absolute_difference"], "variance_absolute_difference"
            ),
            effect_tolerance=_float(value["effect_tolerance"], "effect_tolerance"),
            variance_tolerance=_float(value["variance_tolerance"], "variance_tolerance"),
            effect_passed=_bool(value["effect_passed"], "effect_passed"),
            variance_validated=_bool(value["variance_validated"], "variance_validated"),
            variance_passed=_bool(value["variance_passed"], "variance_passed"),
            passed=_bool(value["passed"], "passed"),
            authoritative=_string(value["authoritative"], "authoritative"),
            canonical_sha256=_sha(value["canonical_sha256"], "canonical_sha256"),
        )
        if restored.to_dict() != dict(value):
            raise SolverEvidenceError("cross-validation report is not canonical schema v1")
        if not restored.verify():
            raise SolverEvidenceError("cross-validation report digest verification failed")
        return restored
