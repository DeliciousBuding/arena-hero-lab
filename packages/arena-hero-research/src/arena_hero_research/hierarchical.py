"""Random-intercept hierarchical model for clustered and repeated-measure outcomes.

This module implements a minimal but real two-level linear mixed model::

    Y_ij = mu + beta * T_ij + u_i + e_ij

with independent random intercepts ``u_i ~ N(0, sigma2_u)`` per cluster and
errors ``e_ij ~ N(0, sigma2_e)``. The estimand is the **within-cluster
(conditional) average treatment contrast**::

    beta = E[Y | T = 1, u] - E[Y | T = 0, u]   (constant over every cluster u)

Design boundaries (fail closed, never silently approximated):

- only one random intercept and a binary treatment are supported;
- fewer than two clusters, non-finite values, or designs with no within-cluster
  treatment variation (cluster-randomized) are rejected;
- a singular between-cluster variance is reported explicitly and disables
  confidence-interval and effect-size claims;
- there is no silent fallback to ordinary least squares or to the paired
  analysis surface.

The authoritative estimator is a profile restricted-maximum-likelihood fit
(``estimator = random-intercept-reml``). An independent method-of-moments /
ANOVA path is provided for cross-validation. Allocation-balanced designs use a
strict effect-agreement tolerance; variance-component conformance is currently
preregistered only for one-control/one-treatment pairs. Balanced repeated and
allocation-unbalanced designs therefore report effect diagnostics but mark variance
unvalidated, so their aggregate report cannot pass. The paired design is the
calibrated balanced degenerate case, and the bridge adapter
:func:`paired_to_cluster_observations` lets callers verify that the REML
treatment effect reproduces the existing paired mean difference.

Confidence intervals use a conservative between-cluster t approximation with
``df = cluster_count - 1``. This is explicitly **not** Satterthwaite or
Kenward-Roger, and the effect-size label ``hierarchical-d-v1`` is not Cohen's
dz or Cohen's d.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from arena_hero_research.execution import PairedObservation
from arena_hero_research.hierarchical_artifacts import (
    FIT_SCHEMA,
    SOLVER_CERTIFICATE_SCHEMA,
    SolverCertificate,
    SolverEvaluation,
    SolverStatus,
)
from arena_hero_research.statistics import student_t_inv_cdf
from arena_hero_research.validation import (
    require_float,
    require_identifier,
    require_int,
    require_sha256,
    require_text,
)
from arena_hero_sim.serialization import JsonValue, content_sha256


def _estimand(control_level: str, treatment_level: str) -> str:
    return (
        "within-cluster average treatment contrast: "
        f"E[Y|treatment={treatment_level},u]-E[Y|treatment={control_level},u]=beta "
        "for every cluster u"
    )


_ESTIMATOR = "random-intercept-reml"
_EFFECT_SIZE_METHOD = "hierarchical-d-v1"
_CI_METHOD = "between-cluster-t"
_SCHEMA = FIT_SCHEMA
_LOG_LAMBDA_LOW = -30.0
_LOG_LAMBDA_HIGH = 30.0
_BOUNDARY_MARGIN = 1.0
_FIT_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "outcome_name",
        "control_level",
        "treatment_level",
        "estimator",
        "effect_size_method",
        "ci_method",
        "confidence_level",
        "estimand",
        "cluster_count",
        "observation_count",
        "dropped_clusters",
        "intercept",
        "treatment_effect",
        "standard_error",
        "between_variance",
        "error_variance",
        "icc",
        "hierarchical_effect",
        "degrees_of_freedom",
        "confidence_lower",
        "confidence_upper",
        "singular",
        "boundary_lambda",
        "warnings",
    }
)
_FIT_FIELDS = _FIT_PAYLOAD_FIELDS | {"canonical_sha256"}


class HierarchicalFitError(ValueError):
    """Base error for the hierarchical model surface."""


class ClusterIdentifiabilityError(HierarchicalFitError):
    """The design cannot identify a within-cluster treatment contrast."""


class SingularFitError(HierarchicalFitError):
    """The variance structure is degenerate and no estimate is produced."""


@dataclass(frozen=True, slots=True)
class ProfileRemlEvaluation:
    """One explicit profile-REML objective evaluation.

    Invalid evaluations carry a reason and no objective value. They are never
    represented by a finite sentinel that could be mistaken for evidence.
    """

    log_lambda: float
    lambda_value: float
    valid: bool
    objective: float | None
    invalid_reason: str | None


@dataclass(frozen=True, slots=True)
class ProfileRemlTrace:
    """Deterministic trace of the bounded profile-REML optimization."""

    initial_lower: float
    initial_upper: float
    final_lower: float
    final_upper: float
    tolerance: float
    max_iterations: int
    iterations: int
    termination_reason: str
    candidate: ProfileRemlEvaluation
    evaluations: tuple[ProfileRemlEvaluation, ...]


class ClusterMissingPolicy(StrEnum):
    """Declared handling of clusters with an incomplete treatment pair."""

    FAIL = "fail"
    DROP_CLUSTER = "drop-cluster"


def _strict_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise HierarchicalFitError(f"{field_name} must be a string")
    if value != value.strip():
        raise HierarchicalFitError(f"{field_name} must already be canonical text")
    return value


def _strict_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise HierarchicalFitError(f"{field_name} must be a boolean")
    return value


def _strict_float(value: object, field_name: str) -> float:
    try:
        return require_float(value, field_name)
    except (TypeError, ValueError) as error:
        raise HierarchicalFitError(str(error)) from error


def _strict_optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _strict_float(value, field_name)


def _strict_int(value: object, field_name: str) -> int:
    try:
        return require_int(value, field_name)
    except (TypeError, ValueError) as error:
        raise HierarchicalFitError(str(error)) from error


def _require_exact_keys(
    value: Mapping[str, object], expected: frozenset[str], field_name: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise HierarchicalFitError(f"{field_name} schema mismatch: {'; '.join(details)}")


@dataclass(frozen=True, slots=True)
class ClusterObservation:
    """One long-format observation nested in a cluster.

    The data grain is (outcome, cluster, observation, treatment, value): one
    episode outcome per row, grouped by cluster for the random intercept.
    """

    outcome_name: str
    cluster_id: str
    observation_id: str
    treatment: str
    value: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        object.__setattr__(self, "cluster_id", require_identifier(self.cluster_id, "cluster_id"))
        object.__setattr__(
            self, "observation_id", require_identifier(self.observation_id, "observation_id")
        )
        object.__setattr__(self, "treatment", require_text(self.treatment, "treatment"))
        normalized_value = _strict_float(self.value, "value")
        if not math.isfinite(normalized_value):
            raise HierarchicalFitError("value must be finite")
        object.__setattr__(self, "value", normalized_value)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "outcome_name": self.outcome_name,
            "cluster_id": self.cluster_id,
            "observation_id": self.observation_id,
            "treatment": self.treatment,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ClusterObservation:
        _require_exact_keys(
            value,
            frozenset({"outcome_name", "cluster_id", "observation_id", "treatment", "value"}),
            "cluster observation",
        )
        return cls(
            outcome_name=_strict_string(value["outcome_name"], "outcome_name"),
            cluster_id=_strict_string(value["cluster_id"], "cluster_id"),
            observation_id=_strict_string(value["observation_id"], "observation_id"),
            treatment=_strict_string(value["treatment"], "treatment"),
            value=_strict_float(value["value"], "value"),
        )


@dataclass(frozen=True, slots=True)
class CrossValidationReport:
    """Independent method-of-moments vs REML agreement report."""

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

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        object.__setattr__(self, "control_level", require_text(self.control_level, "control_level"))
        object.__setattr__(
            self, "treatment_level", require_text(self.treatment_level, "treatment_level")
        )
        if self.control_level == self.treatment_level:
            raise HierarchicalFitError("control and treatment levels must differ")
        object.__setattr__(self, "cluster_count", require_int(self.cluster_count, "cluster_count"))
        object.__setattr__(
            self, "observation_count", require_int(self.observation_count, "observation_count")
        )
        if self.cluster_count < 3:
            raise HierarchicalFitError(
                "cross-validation requires at least three clusters for MoM variance estimation"
            )
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
                raise HierarchicalFitError(f"{name} must be finite")
        for name in (
            "balanced",
            "effect_passed",
            "variance_validated",
            "variance_passed",
            "passed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise HierarchicalFitError(f"{name} must be boolean")
        expected_passed = self.effect_passed and self.variance_validated and self.variance_passed
        if self.passed != expected_passed:
            raise HierarchicalFitError(
                "passed must require validated effect and variance agreement"
            )
        object.__setattr__(self, "authoritative", require_text(self.authoritative, "authoritative"))
        if self.authoritative != _ESTIMATOR:
            raise HierarchicalFitError("cross-validation authority must be the REML estimator")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
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


@dataclass(frozen=True, slots=True)
class RandomInterceptFit:
    """Frozen, content-addressed result of the REML random-intercept fit."""

    schema_version: str
    outcome_name: str
    control_level: str
    treatment_level: str
    estimator: str
    effect_size_method: str
    ci_method: str
    confidence_level: float
    estimand: str
    cluster_count: int
    observation_count: int
    dropped_clusters: int
    intercept: float
    treatment_effect: float
    standard_error: float | None
    between_variance: float
    error_variance: float
    icc: float
    hierarchical_effect: float | None
    degrees_of_freedom: int
    confidence_lower: float | None
    confidence_upper: float | None
    singular: bool
    boundary_lambda: bool
    warnings: tuple[str, ...]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA:
            raise HierarchicalFitError("unsupported random-intercept fit schema")
        object.__setattr__(
            self, "outcome_name", require_identifier(self.outcome_name, "outcome_name")
        )
        object.__setattr__(self, "control_level", require_text(self.control_level, "control_level"))
        object.__setattr__(
            self, "treatment_level", require_text(self.treatment_level, "treatment_level")
        )
        if self.control_level == self.treatment_level:
            raise HierarchicalFitError("control and treatment levels must differ")
        if self.estimator != _ESTIMATOR:
            raise HierarchicalFitError("only the REML estimator is authoritative")
        if self.effect_size_method != _EFFECT_SIZE_METHOD:
            raise HierarchicalFitError("unsupported hierarchical effect-size method")
        if self.ci_method != _CI_METHOD:
            raise HierarchicalFitError("unsupported hierarchical confidence-interval method")
        if self.estimand != _estimand(self.control_level, self.treatment_level):
            raise HierarchicalFitError("fit must disclose the registered directed estimand")
        if not 0.0 < self.confidence_level < 1.0:
            raise HierarchicalFitError("confidence_level must be between zero and one")
        object.__setattr__(self, "cluster_count", require_int(self.cluster_count, "cluster_count"))
        object.__setattr__(
            self, "observation_count", require_int(self.observation_count, "observation_count")
        )
        object.__setattr__(
            self, "dropped_clusters", require_int(self.dropped_clusters, "dropped_clusters")
        )
        if self.cluster_count < 2 or self.observation_count < 3:
            raise HierarchicalFitError("fit requires at least two clusters and three observations")
        if self.dropped_clusters < 0:
            raise HierarchicalFitError("dropped cluster count is invalid")
        for name in ("intercept", "treatment_effect"):
            if not math.isfinite(getattr(self, name)):
                raise HierarchicalFitError(f"{name} must be finite")
        if not math.isfinite(self.between_variance) or self.between_variance < 0:
            raise HierarchicalFitError("between_variance must be finite and non-negative")
        if not math.isfinite(self.error_variance) or self.error_variance <= 0:
            raise HierarchicalFitError("error_variance must be finite and positive")
        if not math.isfinite(self.icc) or not 0.0 <= self.icc < 1.0:
            raise HierarchicalFitError("icc must lie in [0, 1)")
        object.__setattr__(
            self,
            "degrees_of_freedom",
            require_int(self.degrees_of_freedom, "degrees_of_freedom"),
        )
        if self.degrees_of_freedom != self.cluster_count - 1:
            raise HierarchicalFitError("degrees_of_freedom must equal cluster_count - 1")
        if self.singular:
            if (
                self.standard_error is not None
                or self.hierarchical_effect is not None
                or self.confidence_lower is not None
                or self.confidence_upper is not None
            ):
                raise HierarchicalFitError(
                    "singular fits must not claim standard errors, effects, or intervals"
                )
        else:
            for name in (
                "standard_error",
                "hierarchical_effect",
                "confidence_lower",
                "confidence_upper",
            ):
                value = getattr(self, name)
                if value is None or not math.isfinite(value):
                    raise HierarchicalFitError(f"{name} must be present on a non-singular fit")
            standard_error = self.standard_error
            confidence_lower = self.confidence_lower
            confidence_upper = self.confidence_upper
            assert standard_error is not None
            assert confidence_lower is not None
            assert confidence_upper is not None
            if standard_error <= 0 or confidence_lower >= confidence_upper:
                raise HierarchicalFitError("non-singular fit must have a positive SE and valid CI")
        warnings = tuple(require_text(item, "warning") for item in self.warnings)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "outcome_name": self.outcome_name,
            "control_level": self.control_level,
            "treatment_level": self.treatment_level,
            "estimator": self.estimator,
            "effect_size_method": self.effect_size_method,
            "ci_method": self.ci_method,
            "confidence_level": self.confidence_level,
            "estimand": self.estimand,
            "cluster_count": self.cluster_count,
            "observation_count": self.observation_count,
            "dropped_clusters": self.dropped_clusters,
            "intercept": self.intercept,
            "treatment_effect": self.treatment_effect,
            "standard_error": self.standard_error,
            "between_variance": self.between_variance,
            "error_variance": self.error_variance,
            "icc": self.icc,
            "hierarchical_effect": self.hierarchical_effect,
            "degrees_of_freedom": self.degrees_of_freedom,
            "confidence_lower": self.confidence_lower,
            "confidence_upper": self.confidence_upper,
            "singular": self.singular,
            "boundary_lambda": self.boundary_lambda,
            "warnings": list(self.warnings),
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RandomInterceptFit:
        try:
            _require_exact_keys(value, _FIT_FIELDS, "random-intercept fit")
            warnings_value = value["warnings"]
            if not isinstance(warnings_value, list):
                raise HierarchicalFitError("warnings must be a JSON list")
            warnings = tuple(_strict_string(item, "warning") for item in warnings_value)
            restored = cls(
                schema_version=_strict_string(value["schema_version"], "schema_version"),
                outcome_name=_strict_string(value["outcome_name"], "outcome_name"),
                control_level=_strict_string(value["control_level"], "control_level"),
                treatment_level=_strict_string(value["treatment_level"], "treatment_level"),
                estimator=_strict_string(value["estimator"], "estimator"),
                effect_size_method=_strict_string(
                    value["effect_size_method"], "effect_size_method"
                ),
                ci_method=_strict_string(value["ci_method"], "ci_method"),
                confidence_level=_strict_float(value["confidence_level"], "confidence_level"),
                estimand=_strict_string(value["estimand"], "estimand"),
                cluster_count=_strict_int(value["cluster_count"], "cluster_count"),
                observation_count=_strict_int(value["observation_count"], "observation_count"),
                dropped_clusters=_strict_int(value["dropped_clusters"], "dropped_clusters"),
                intercept=_strict_float(value["intercept"], "intercept"),
                treatment_effect=_strict_float(value["treatment_effect"], "treatment_effect"),
                standard_error=_strict_optional_float(value["standard_error"], "standard_error"),
                between_variance=_strict_float(value["between_variance"], "between_variance"),
                error_variance=_strict_float(value["error_variance"], "error_variance"),
                icc=_strict_float(value["icc"], "icc"),
                hierarchical_effect=_strict_optional_float(
                    value["hierarchical_effect"], "hierarchical_effect"
                ),
                degrees_of_freedom=_strict_int(value["degrees_of_freedom"], "degrees_of_freedom"),
                confidence_lower=_strict_optional_float(
                    value["confidence_lower"], "confidence_lower"
                ),
                confidence_upper=_strict_optional_float(
                    value["confidence_upper"], "confidence_upper"
                ),
                singular=_strict_bool(value["singular"], "singular"),
                boundary_lambda=_strict_bool(value["boundary_lambda"], "boundary_lambda"),
                warnings=warnings,
                canonical_sha256=_strict_string(value["canonical_sha256"], "canonical_sha256"),
            )
        except HierarchicalFitError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise HierarchicalFitError(
                f"invalid random-intercept fit schema: {type(error).__name__}"
            ) from error
        if restored.to_dict() != dict(value):
            raise HierarchicalFitError("random-intercept fit payload is not canonical schema v2")
        if not restored.verify():
            raise HierarchicalFitError("random-intercept fit digest verification failed")
        return restored


def _optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return require_float(value, field_name)


def paired_to_cluster_observations(
    paired: Sequence[PairedObservation],
    *,
    control_level: str = "control",
    treatment_level: str = "treatment",
) -> tuple[ClusterObservation, ...]:
    """Bridge paired observations into the cluster grain.

    Each pair becomes one cluster with exactly two observations (one per
    treatment level). Pairs with a missing control or treatment value cannot
    be represented in the cluster grain and are rejected: missing values must
    be resolved by the caller under the declared policy before bridging.
    """

    normalized_control = require_text(control_level, "control_level")
    normalized_treatment = require_text(treatment_level, "treatment_level")
    if normalized_control == normalized_treatment:
        raise HierarchicalFitError("control and treatment levels must differ")
    converted: list[ClusterObservation] = []
    for item in paired:
        if item.control is None or item.treatment is None:
            raise HierarchicalFitError(
                f"paired bridge requires complete pairs; pair {item.pair_id} is incomplete"
            )
        converted.append(
            ClusterObservation(
                outcome_name=item.outcome_name,
                cluster_id=item.pair_id,
                observation_id=f"{item.pair_id}.control",
                treatment=normalized_control,
                value=item.control,
            )
        )
        converted.append(
            ClusterObservation(
                outcome_name=item.outcome_name,
                cluster_id=item.pair_id,
                observation_id=f"{item.pair_id}.treatment",
                treatment=normalized_treatment,
                value=item.treatment,
            )
        )
    return tuple(converted)


def _normalize_contrast(control_level: str, treatment_level: str) -> tuple[str, str]:
    control = require_text(control_level, "control_level")
    treatment = require_text(treatment_level, "treatment_level")
    if control == treatment:
        raise HierarchicalFitError("control and treatment levels must differ")
    return control, treatment


def _normalize_missing_policy(policy: ClusterMissingPolicy) -> ClusterMissingPolicy:
    if not isinstance(policy, ClusterMissingPolicy):
        raise HierarchicalFitError("missing_policy must be a ClusterMissingPolicy value")
    return policy


def _numeric_boundary(operation: str, function):
    try:
        return function()
    except HierarchicalFitError:
        raise
    except (ArithmeticError, ValueError) as error:
        raise HierarchicalFitError(
            f"{operation} failed at the finite numerical boundary: {type(error).__name__}"
        ) from error


def _source_input_sha256(
    *,
    outcome_name: str,
    observations: Sequence[ClusterObservation],
    control_level: str,
    treatment_level: str,
    confidence_level: float,
    missing_policy: ClusterMissingPolicy,
) -> str:
    ordered = sorted(
        observations,
        key=lambda item: (
            item.cluster_id,
            item.observation_id,
            item.treatment,
            item.outcome_name,
            item.value,
        ),
    )
    payload: dict[str, JsonValue] = {
        "schema_version": "arena.research.hierarchical-analysis-input.v1",
        "outcome_name": outcome_name,
        "control_level": control_level,
        "treatment_level": treatment_level,
        "confidence_level": confidence_level,
        "missing_policy": missing_policy.value,
        "observations": [item.to_dict() for item in ordered],
    }
    return content_sha256(payload)


def _retained_analysis_input_sha256(
    *,
    outcome_name: str,
    clusters: dict[str, dict[str, tuple[float, ...]]],
    control_level: str,
    treatment_level: str,
    confidence_level: float,
) -> str:
    payload: dict[str, JsonValue] = {
        "schema_version": "arena.research.hierarchical-retained-input.v1",
        "outcome_name": outcome_name,
        "control_level": control_level,
        "treatment_level": treatment_level,
        "confidence_level": confidence_level,
        "clusters": [
            {
                "cluster_id": cluster_id,
                "control": list(clusters[cluster_id][control_level]),
                "treatment": list(clusters[cluster_id][treatment_level]),
            }
            for cluster_id in sorted(clusters)
        ],
    }
    return content_sha256(payload)


def _fit_random_intercept_traced(
    *,
    outcome_name: str,
    observations: Sequence[ClusterObservation],
    control_level: str = "control",
    treatment_level: str = "treatment",
    confidence_level: float = 0.95,
    missing_policy: ClusterMissingPolicy = ClusterMissingPolicy.FAIL,
) -> tuple[
    RandomInterceptFit,
    ProfileRemlTrace,
    dict[str, dict[str, tuple[float, ...]]],
    str,
    str,
]:
    normalized_outcome = require_identifier(outcome_name, "outcome_name")
    control, treatment = _normalize_contrast(control_level, treatment_level)
    policy = _normalize_missing_policy(missing_policy)
    if not 0.0 < confidence_level < 1.0:
        raise HierarchicalFitError("confidence_level must be between zero and one")

    clusters, dropped = _prepare_clusters(
        normalized_outcome, observations, policy, control, treatment
    )
    cluster_ids = tuple(sorted(clusters))
    cluster_count = len(cluster_ids)
    observation_count = sum(
        len(values) for grouped in clusters.values() for values in grouped.values()
    )
    result = _reml_fit_traced(cluster_ids, clusters, control, treatment)
    beta, intercept, sigma2_e, sigma2_u, se2, singular, boundary, warnings, trace = result
    if singular:
        confidence_lower = confidence_upper = standard_error = effect = None
    else:
        standard_error = math.sqrt(se2)
        critical = student_t_inv_cdf(1.0 - (1.0 - confidence_level) / 2.0, cluster_count - 1)
        half_width = critical * standard_error
        confidence_lower = beta - half_width
        confidence_upper = beta + half_width
        effect = beta / math.sqrt(sigma2_u + sigma2_e)
    icc = sigma2_u / (sigma2_u + sigma2_e)
    if not all(math.isfinite(value) for value in (beta, intercept, sigma2_e, sigma2_u, se2, icc)):
        raise SingularFitError("random-intercept fit produced non-finite statistics")
    if dropped:
        warnings = (
            *warnings,
            "incomplete clusters were dropped according to the preregistered policy",
        )
    provisional = RandomInterceptFit(
        schema_version=_SCHEMA,
        outcome_name=normalized_outcome,
        control_level=control,
        treatment_level=treatment,
        estimator=_ESTIMATOR,
        effect_size_method=_EFFECT_SIZE_METHOD,
        ci_method=_CI_METHOD,
        confidence_level=confidence_level,
        estimand=_estimand(control, treatment),
        cluster_count=cluster_count,
        observation_count=observation_count,
        dropped_clusters=dropped,
        intercept=intercept,
        treatment_effect=beta,
        standard_error=standard_error,
        between_variance=sigma2_u,
        error_variance=sigma2_e,
        icc=icc,
        hierarchical_effect=effect,
        degrees_of_freedom=cluster_count - 1,
        confidence_lower=confidence_lower,
        confidence_upper=confidence_upper,
        singular=singular,
        boundary_lambda=boundary,
        warnings=warnings,
        canonical_sha256="0" * 64,
    )
    fit = RandomInterceptFit(
        schema_version=provisional.schema_version,
        outcome_name=provisional.outcome_name,
        control_level=provisional.control_level,
        treatment_level=provisional.treatment_level,
        estimator=provisional.estimator,
        effect_size_method=provisional.effect_size_method,
        ci_method=provisional.ci_method,
        confidence_level=provisional.confidence_level,
        estimand=provisional.estimand,
        cluster_count=provisional.cluster_count,
        observation_count=provisional.observation_count,
        dropped_clusters=provisional.dropped_clusters,
        intercept=provisional.intercept,
        treatment_effect=provisional.treatment_effect,
        standard_error=provisional.standard_error,
        between_variance=provisional.between_variance,
        error_variance=provisional.error_variance,
        icc=provisional.icc,
        hierarchical_effect=provisional.hierarchical_effect,
        degrees_of_freedom=provisional.degrees_of_freedom,
        confidence_lower=provisional.confidence_lower,
        confidence_upper=provisional.confidence_upper,
        singular=provisional.singular,
        boundary_lambda=provisional.boundary_lambda,
        warnings=provisional.warnings,
        canonical_sha256=content_sha256(provisional.payload()),
    )
    source_input_sha256 = _source_input_sha256(
        outcome_name=normalized_outcome,
        observations=observations,
        control_level=control,
        treatment_level=treatment,
        confidence_level=confidence_level,
        missing_policy=policy,
    )
    analysis_input_sha256 = _retained_analysis_input_sha256(
        outcome_name=normalized_outcome,
        clusters=clusters,
        control_level=control,
        treatment_level=treatment,
        confidence_level=confidence_level,
    )
    return fit, trace, clusters, source_input_sha256, analysis_input_sha256


def fit_random_intercept(
    *,
    outcome_name: str,
    observations: Sequence[ClusterObservation],
    control_level: str = "control",
    treatment_level: str = "treatment",
    confidence_level: float = 0.95,
    missing_policy: ClusterMissingPolicy = ClusterMissingPolicy.FAIL,
) -> RandomInterceptFit:
    """Fit a directed treatment-minus-control random-intercept model."""

    def calculate() -> RandomInterceptFit:
        fit, _, _, _, _ = _fit_random_intercept_traced(
            outcome_name=outcome_name,
            observations=observations,
            control_level=control_level,
            treatment_level=treatment_level,
            confidence_level=confidence_level,
            missing_policy=missing_policy,
        )
        return fit

    return _numeric_boundary("random-intercept fit", calculate)


def _build_solver_certificate(
    *,
    fit: RandomInterceptFit,
    trace: ProfileRemlTrace,
    clusters: dict[str, dict[str, tuple[float, ...]]],
    source_input_sha256: str,
    analysis_input_sha256: str,
) -> SolverCertificate:
    if not trace.candidate.valid or trace.candidate.objective is None:
        raise SingularFitError("profile-REML certificate requires a valid optimizer candidate")
    cluster_ids = tuple(sorted(clusters))
    _, sizes, sums, _ = _cluster_sums(clusters, fit.control_level, fit.treatment_level)
    degrees = sum(sizes) - 2
    score, curvature, score_scale = _profile_reml_diagnostics(
        cluster_ids, sums, degrees, trace.candidate.log_lambda
    )
    _, lower_curvature, _ = _profile_reml_diagnostics(cluster_ids, sums, degrees, trace.final_lower)
    _, upper_curvature, _ = _profile_reml_diagnostics(cluster_ids, sums, degrees, trace.final_upper)
    interval_error = (trace.final_upper - trace.final_lower) * max(
        abs(lower_curvature), abs(curvature), abs(upper_curvature)
    )
    roundoff_error = 16.0 * math.sqrt(math.ulp(1.0)) * score_scale
    kkt_tolerance = max(roundoff_error, interval_error)
    if fit.boundary_lambda and trace.candidate.log_lambda < 0:
        kkt_residual = max(score, 0.0)
    elif fit.boundary_lambda:
        kkt_residual = max(-score, 0.0)
    else:
        kkt_residual = abs(score)
    curvature_floor = 256.0 * math.ulp(max(1.0, abs(curvature)))
    newton_correction = (
        -score / curvature if not fit.boundary_lambda and curvature < -curvature_floor else None
    )
    backward_error = kkt_residual / score_scale
    precision_limited = trace.termination_reason != "interval-tolerance"
    indeterminate = (
        precision_limited
        or curvature >= 0.0
        or newton_correction is None
        or kkt_residual > kkt_tolerance
    )
    if fit.boundary_lambda:
        status = SolverStatus.BOUNDARY
    elif indeterminate:
        status = SolverStatus.INDETERMINATE
    else:
        status = SolverStatus.VERIFIED_INTERIOR
    evaluations = tuple(
        SolverEvaluation(
            log_lambda=item.log_lambda,
            lambda_value=item.lambda_value,
            valid=item.valid,
            objective=item.objective,
            invalid_reason=item.invalid_reason,
        )
        for item in trace.evaluations
    )
    provisional = SolverCertificate(
        schema_version=SOLVER_CERTIFICATE_SCHEMA,
        source_input_sha256=source_input_sha256,
        analysis_input_sha256=analysis_input_sha256,
        fit_schema_version=fit.schema_version,
        fit_sha256=fit.canonical_sha256,
        optimizer="bounded-golden-section",
        initial_bracket=(trace.initial_lower, trace.initial_upper),
        final_bracket=(trace.final_lower, trace.final_upper),
        tolerance=trace.tolerance,
        max_iterations=trace.max_iterations,
        iterations=trace.iterations,
        evaluation_count=len(evaluations),
        invalid_evaluation_count=sum(not item.valid for item in evaluations),
        termination_reason=trace.termination_reason,
        candidate_log_lambda=trace.candidate.log_lambda,
        candidate_lambda=trace.candidate.lambda_value,
        candidate_objective=trace.candidate.objective,
        profile_score=score,
        profile_curvature=curvature,
        kkt_residual=kkt_residual,
        kkt_tolerance=kkt_tolerance,
        backward_error=backward_error,
        newton_correction=newton_correction,
        boundary=fit.boundary_lambda,
        precision_limited=precision_limited,
        solver_status=status,
        evaluations=evaluations,
        canonical_sha256="0" * 64,
    )
    return replace(provisional, canonical_sha256=content_sha256(provisional.payload()))


def fit_random_intercept_with_certificate(
    *,
    outcome_name: str,
    observations: Sequence[ClusterObservation],
    control_level: str = "control",
    treatment_level: str = "treatment",
    confidence_level: float = 0.95,
    missing_policy: ClusterMissingPolicy = ClusterMissingPolicy.FAIL,
) -> tuple[RandomInterceptFit, SolverCertificate]:
    """Fit RandomInterceptFit v2 and emit its one-way SolverCertificate v1 evidence."""

    def calculate() -> tuple[RandomInterceptFit, SolverCertificate]:
        fit, trace, clusters, source_input_sha256, analysis_input_sha256 = (
            _fit_random_intercept_traced(
                outcome_name=outcome_name,
                observations=observations,
                control_level=control_level,
                treatment_level=treatment_level,
                confidence_level=confidence_level,
                missing_policy=missing_policy,
            )
        )
        return fit, _build_solver_certificate(
            fit=fit,
            trace=trace,
            clusters=clusters,
            source_input_sha256=source_input_sha256,
            analysis_input_sha256=analysis_input_sha256,
        )

    return _numeric_boundary("random-intercept fit with certificate", calculate)


def verify_solver_certificate(
    *,
    certificate: SolverCertificate,
    fit: RandomInterceptFit,
    outcome_name: str,
    observations: Sequence[ClusterObservation],
    control_level: str = "control",
    treatment_level: str = "treatment",
    confidence_level: float = 0.95,
    missing_policy: ClusterMissingPolicy = ClusterMissingPolicy.FAIL,
) -> bool:
    """Recompute solver evidence and require exact content-addressed identity."""

    if certificate.fit_schema_version != fit.schema_version:
        return False
    if certificate.fit_sha256 != fit.canonical_sha256 or not fit.verify():
        return False
    try:
        recomputed_fit, recomputed = fit_random_intercept_with_certificate(
            outcome_name=outcome_name,
            observations=observations,
            control_level=control_level,
            treatment_level=treatment_level,
            confidence_level=confidence_level,
            missing_policy=missing_policy,
        )
    except HierarchicalFitError:
        return False
    return (
        recomputed_fit.to_dict() == fit.to_dict()
        and recomputed.to_dict() == certificate.to_dict()
        and certificate.verify()
    )


def cross_validate_random_intercept(
    *,
    outcome_name: str,
    observations: Sequence[ClusterObservation],
    control_level: str = "control",
    treatment_level: str = "treatment",
    missing_policy: ClusterMissingPolicy = ClusterMissingPolicy.FAIL,
) -> CrossValidationReport:
    """Compare independent within-OLS/MoM estimates with authoritative REML."""

    normalized_outcome = require_identifier(outcome_name, "outcome_name")
    control, treatment = _normalize_contrast(control_level, treatment_level)
    policy = _normalize_missing_policy(missing_policy)

    def calculate() -> CrossValidationReport:
        clusters, _ = _prepare_clusters(
            normalized_outcome, observations, policy, control, treatment
        )
        cluster_ids = tuple(sorted(clusters))
        cluster_count = len(cluster_ids)
        observation_count = sum(
            len(values) for grouped in clusters.values() for values in grouped.values()
        )
        if cluster_count < 3:
            raise HierarchicalFitError("cross-validation requires at least three clusters")
        ols_effect, mom_between, mom_error = _mom_fit(cluster_ids, clusters, control, treatment)
        reml = _reml_fit(cluster_ids, clusters, control, treatment)
        reml_effect, reml_error, reml_between = reml[0], reml[2], reml[3]
        balanced = _is_balanced(clusters, control, treatment)
        variance_validated = _has_paired_allocation(clusters, control, treatment)
        if balanced:
            effect_tolerance = max(1e-8, 1e-8 * abs(reml_effect))
            variance_tolerance = max(1e-7, 1e-7 * reml_between, 1e-7 * reml_error)
        else:
            # The independent within-cluster effect remains a useful diagnostic
            # under allocation imbalance, but the tolerance must reflect that it
            # targets a differently weighted finite-sample contrast than REML.
            effect_tolerance = max(1e-2, 1e-2 * abs(reml_effect))
            variance_tolerance = max(1e-2, 1e-2 * reml_between, 1e-2 * reml_error)
        effect_difference = abs(ols_effect - reml_effect)
        variance_difference = max(abs(mom_between - reml_between), abs(mom_error - reml_error))
        effect_passed = effect_difference <= effect_tolerance
        variance_passed = variance_validated and variance_difference <= variance_tolerance
        return CrossValidationReport(
            outcome_name=normalized_outcome,
            control_level=control,
            treatment_level=treatment,
            cluster_count=cluster_count,
            observation_count=observation_count,
            balanced=balanced,
            path_a_effect=ols_effect,
            path_b_effect=reml_effect,
            path_a_between_variance=mom_between,
            path_a_error_variance=mom_error,
            path_b_between_variance=reml_between,
            path_b_error_variance=reml_error,
            effect_absolute_difference=effect_difference,
            variance_absolute_difference=variance_difference,
            effect_tolerance=effect_tolerance,
            variance_tolerance=variance_tolerance,
            effect_passed=effect_passed,
            variance_validated=variance_validated,
            variance_passed=variance_passed,
            passed=effect_passed and variance_validated and variance_passed,
            authoritative=_ESTIMATOR,
        )

    return _numeric_boundary("random-intercept cross-validation", calculate)


def _prepare_clusters(
    outcome_name: str,
    observations: Sequence[ClusterObservation],
    missing_policy: ClusterMissingPolicy,
    control_level: str,
    treatment_level: str,
) -> tuple[dict[str, dict[str, tuple[float, ...]]], int]:
    """Validate and group observations, enforcing identifiability gates."""

    if not observations:
        raise HierarchicalFitError("at least three observations are required")
    raw: dict[str, dict[str, list[tuple[str, float]]]] = {}
    treatment_levels: set[str] = set()
    observation_identities: set[tuple[str, str]] = set()
    for item in observations:
        if not isinstance(item, ClusterObservation):
            raise HierarchicalFitError("observations must be ClusterObservation values")
        if item.outcome_name != outcome_name:
            raise HierarchicalFitError(
                f"observation outcome {item.outcome_name} does not match requested outcome"
            )
        identity = (item.cluster_id, item.observation_id)
        if identity in observation_identities:
            raise HierarchicalFitError("observation identities must be unique within a cluster")
        observation_identities.add(identity)
        treatment_levels.add(item.treatment)
        raw.setdefault(item.cluster_id, {}).setdefault(item.treatment, []).append(
            (item.observation_id, item.value)
        )
    expected_levels = {control_level, treatment_level}
    if treatment_levels != expected_levels:
        raise HierarchicalFitError(
            "observations must use exactly two treatment levels (control and treatment)"
        )
    levels = (control_level, treatment_level)

    dropped = 0
    retained: dict[str, dict[str, tuple[float, ...]]] = {}
    for cluster_id, grouped in sorted(raw.items()):
        present = {
            level: tuple(value for _, value in sorted(grouped[level]))
            for level in levels
            if level in grouped
        }
        if len(present) != 2:
            if missing_policy == ClusterMissingPolicy.FAIL:
                missing = ", ".join(sorted(set(levels) - set(present)))
                raise ClusterIdentifiabilityError(
                    f"cluster {cluster_id} is missing treatment level(s): {missing}"
                )
            dropped += 1
            continue
        retained[cluster_id] = present
    if len(retained) < 2:
        raise ClusterIdentifiabilityError(
            "at least two complete clusters are required for a within-cluster contrast"
        )
    return retained, dropped


def _cluster_sums(
    clusters: dict[str, dict[str, tuple[float, ...]]],
    control_level: str,
    treatment_level: str,
) -> tuple[
    tuple[str, ...],
    tuple[int, ...],
    dict[str, tuple[float, float, float, float, float]],
    float,
]:
    """Return stable centered sufficient statistics for every cluster.

    A deterministic observed value is subtracted from every outcome before any
    squares or cross-products are formed. The model includes an intercept, so
    this translation leaves treatment and variance estimates unchanged while
    avoiding catastrophic cancellation for large common offsets.
    """

    ids = tuple(sorted(clusters))
    first = clusters[ids[0]]
    anchor_values = first[control_level] or first[treatment_level]
    anchor = anchor_values[0]
    sizes: list[int] = []
    sums: dict[str, tuple[float, float, float, float, float]] = {}
    for cluster_id in ids:
        baseline = tuple(value - anchor for value in clusters[cluster_id][control_level])
        contrast = tuple(value - anchor for value in clusters[cluster_id][treatment_level])
        centered = (*baseline, *contrast)
        size = len(centered)
        sizes.append(size)
        total = math.fsum(centered)
        treatment_count = len(contrast)
        treatment_total = math.fsum(contrast)
        squares = math.fsum(value * value for value in centered)
        sums[cluster_id] = (
            float(size),
            total,
            float(treatment_count),
            treatment_total,
            squares,
        )
    return ids, tuple(sizes), sums, anchor


def _reml_components(
    cluster_ids: tuple[str, ...],
    sums: dict[str, tuple[float, float, float, float, float]],
    lam: float,
) -> tuple[float, float, float, float, float, float, float]:
    coefficients = {
        cluster_id: lam / (1.0 + sums[cluster_id][0] * lam) for cluster_id in cluster_ids
    }
    a00 = math.fsum(
        sums[cluster_id][0] - coefficients[cluster_id] * sums[cluster_id][0] * sums[cluster_id][0]
        for cluster_id in cluster_ids
    )
    a01 = math.fsum(
        sums[cluster_id][2] - coefficients[cluster_id] * sums[cluster_id][0] * sums[cluster_id][2]
        for cluster_id in cluster_ids
    )
    a11 = math.fsum(
        sums[cluster_id][2] - coefficients[cluster_id] * sums[cluster_id][2] * sums[cluster_id][2]
        for cluster_id in cluster_ids
    )
    b0 = math.fsum(
        sums[cluster_id][1] - coefficients[cluster_id] * sums[cluster_id][0] * sums[cluster_id][1]
        for cluster_id in cluster_ids
    )
    b1 = math.fsum(
        sums[cluster_id][3] - coefficients[cluster_id] * sums[cluster_id][2] * sums[cluster_id][1]
        for cluster_id in cluster_ids
    )
    q = math.fsum(
        sums[cluster_id][4] - coefficients[cluster_id] * sums[cluster_id][1] * sums[cluster_id][1]
        for cluster_id in cluster_ids
    )
    log_terms = math.fsum(math.log1p(sums[cluster_id][0] * lam) for cluster_id in cluster_ids)
    return a00, a01, a11, b0, b1, q, log_terms


def _profile_reml_diagnostics(
    cluster_ids: tuple[str, ...],
    sums: dict[str, tuple[float, float, float, float, float]],
    degrees: int,
    log_lambda: float,
) -> tuple[float, float, float]:
    """Return analytic score and curvature with respect to log(lambda)."""

    lam = math.exp(log_lambda)
    component_rows: list[tuple[float, float, float, float, float, float]] = []
    first_rows: list[tuple[float, float, float, float, float, float]] = []
    second_rows: list[tuple[float, float, float, float, float, float]] = []
    log_first: list[float] = []
    log_second: list[float] = []
    for cluster_id in cluster_ids:
        n, sy, nt, sty, syy = sums[cluster_id]
        denominator = 1.0 + n * lam
        coefficient = lam / denominator
        first = lam / (denominator * denominator)
        second = lam * (1.0 - n * lam) / (denominator * denominator * denominator)
        products = (n * n, n * nt, nt * nt, n * sy, nt * sy, sy * sy)
        component_rows.append(
            (
                n - coefficient * products[0],
                nt - coefficient * products[1],
                nt - coefficient * products[2],
                sy - coefficient * products[3],
                sty - coefficient * products[4],
                syy - coefficient * products[5],
            )
        )
        first_rows.append(
            (
                -first * products[0],
                -first * products[1],
                -first * products[2],
                -first * products[3],
                -first * products[4],
                -first * products[5],
            )
        )
        second_rows.append(
            (
                -second * products[0],
                -second * products[1],
                -second * products[2],
                -second * products[3],
                -second * products[4],
                -second * products[5],
            )
        )
        log_first.append(n * lam / denominator)
        log_second.append(n * lam / (denominator * denominator))

    def totals(rows):
        return tuple(math.fsum(row[index] for row in rows) for index in range(6))

    a00, a01, a11, b0, b1, q = totals(component_rows)
    a00d, a01d, a11d, b0d, b1d, qd = totals(first_rows)
    a00dd, a01dd, a11dd, b0dd, b1dd, qdd = totals(second_rows)
    determinant = a00 * a11 - a01 * a01
    determinant_first = a00d * a11 + a00 * a11d - 2.0 * a01 * a01d
    determinant_second = (
        a00dd * a11 + 2.0 * a00d * a11d + a00 * a11dd - 2.0 * (a01d * a01d + a01 * a01dd)
    )
    if determinant <= 0:
        raise SingularFitError("analytic profile diagnostics found a singular design matrix")
    beta0 = (a11 * b0 - a01 * b1) / determinant
    beta1 = (a00 * b1 - a01 * b0) / determinant
    residual = q - beta0 * b0 - beta1 * b1
    residual_first = (
        qd
        - 2.0 * (beta0 * b0d + beta1 * b1d)
        + a00d * beta0 * beta0
        + 2.0 * a01d * beta0 * beta1
        + a11d * beta1 * beta1
    )
    g0 = b0d - (a00d * beta0 + a01d * beta1)
    g1 = b1d - (a01d * beta0 + a11d * beta1)
    g_inverse_g = (a11 * g0 * g0 - 2.0 * a01 * g0 * g1 + a00 * g1 * g1) / determinant
    residual_second = (
        qdd
        - 2.0 * (beta0 * b0dd + beta1 * b1dd)
        + a00dd * beta0 * beta0
        + 2.0 * a01dd * beta0 * beta1
        + a11dd * beta1 * beta1
        - 2.0 * g_inverse_g
    )
    if residual <= 0 or not all(
        math.isfinite(item)
        for item in (
            residual,
            residual_first,
            residual_second,
            determinant_first,
            determinant_second,
        )
    ):
        raise SingularFitError("analytic profile diagnostics found a non-positive residual")
    score = -0.5 * (
        degrees * residual_first / residual + determinant_first / determinant + math.fsum(log_first)
    )
    curvature = -0.5 * (
        degrees
        * (residual_second / residual - (residual_first / residual) * (residual_first / residual))
        + determinant_second / determinant
        - (determinant_first / determinant) * (determinant_first / determinant)
        + math.fsum(log_second)
    )
    if not math.isfinite(score) or not math.isfinite(curvature):
        raise SingularFitError("analytic profile diagnostics produced non-finite values")
    score_scale = 1.0 + 0.5 * (
        abs(degrees * residual_first / residual)
        + abs(determinant_first / determinant)
        + abs(math.fsum(log_first))
    )
    return score, curvature, score_scale


def _profile_reml_score_curvature(
    cluster_ids: tuple[str, ...],
    sums: dict[str, tuple[float, float, float, float, float]],
    degrees: int,
    log_lambda: float,
) -> tuple[float, float]:
    score, curvature, _ = _profile_reml_diagnostics(cluster_ids, sums, degrees, log_lambda)
    return score, curvature


def _profile_reml_evaluation(
    cluster_ids: tuple[str, ...],
    sums: dict[str, tuple[float, float, float, float, float]],
    degrees: int,
    log_lambda: float,
) -> ProfileRemlEvaluation:
    """Evaluate the profile objective without converting invalid states to sentinels."""

    try:
        lam = math.exp(log_lambda)
        a00, a01, a11, b0, b1, q, log_terms = _reml_components(cluster_ids, sums, lam)
        determinant = a00 * a11 - a01 * a01
        scale = a00 * a11
        if not all(
            math.isfinite(value) for value in (a00, a01, a11, b0, b1, q, log_terms, determinant)
        ):
            reason = "non-finite-components"
        elif determinant <= 0 or scale <= 0 or determinant < 1e-14 * scale:
            reason = "singular-design"
        elif q <= 0:
            reason = "non-positive-quadratic"
        else:
            beta0 = (a11 * b0 - a01 * b1) / determinant
            beta1 = (a00 * b1 - a01 * b0) / determinant
            residual = q - (beta0 * b0 + beta1 * b1)
            if not math.isfinite(residual) or residual <= 0 or residual < 1e-12 * q:
                reason = "non-positive-or-precision-limited-residual"
            else:
                objective = -0.5 * (
                    degrees * math.log(residual) + math.log(determinant) + log_terms
                )
                if math.isfinite(objective):
                    return ProfileRemlEvaluation(
                        log_lambda=log_lambda,
                        lambda_value=lam,
                        valid=True,
                        objective=objective,
                        invalid_reason=None,
                    )
                reason = "non-finite-objective"
    except (ArithmeticError, ValueError):
        reason = "arithmetic-error"
        lam = math.inf if log_lambda > 0 else 0.0
    return ProfileRemlEvaluation(
        log_lambda=log_lambda,
        lambda_value=lam,
        valid=False,
        objective=None,
        invalid_reason=reason,
    )


def _golden_section_maximize_traced(
    cluster_ids: tuple[str, ...],
    sums: dict[str, tuple[float, float, float, float, float]],
    degrees: int,
    lower: float,
    upper: float,
    *,
    tolerance: float = 1e-12,
    max_iterations: int = 200,
) -> ProfileRemlTrace:
    """Mirror the established golden-section search while retaining every evaluation."""

    if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
        raise HierarchicalFitError("profile-REML bounds must be finite with lower < upper")
    if tolerance <= 0 or max_iterations < 1:
        raise HierarchicalFitError("profile-REML tolerance and iteration count must be positive")
    inv_phi = (math.sqrt(5.0) - 1.0) / 2.0
    left = lower
    right = upper
    mid_left = right - inv_phi * (right - left)
    mid_right = left + inv_phi * (right - left)
    evaluations: list[ProfileRemlEvaluation] = []

    def evaluate(value: float) -> ProfileRemlEvaluation:
        result = _profile_reml_evaluation(cluster_ids, sums, degrees, value)
        evaluations.append(result)
        return result

    left_evaluation = evaluate(mid_left)
    right_evaluation = evaluate(mid_right)

    def comparison_value(evaluation: ProfileRemlEvaluation) -> float:
        if evaluation.valid and evaluation.objective is not None:
            return evaluation.objective
        return -math.inf

    iterations = 0
    termination_reason = "max-iterations"
    for _ in range(max_iterations):
        if right - left <= tolerance * max(1.0, abs(left), abs(right)):
            termination_reason = "interval-tolerance"
            break
        left_value = comparison_value(left_evaluation)
        right_value = comparison_value(right_evaluation)
        if left_value > right_value:
            right = mid_right
            mid_right = mid_left
            right_evaluation = left_evaluation
            mid_left = right - inv_phi * (right - left)
            left_evaluation = evaluate(mid_left)
        else:
            left = mid_left
            mid_left = mid_right
            left_evaluation = right_evaluation
            mid_right = left + inv_phi * (right - left)
            right_evaluation = evaluate(mid_right)
        iterations += 1

    left_value = comparison_value(left_evaluation)
    right_value = comparison_value(right_evaluation)
    candidate = left_evaluation if left_value >= right_value else right_evaluation
    return ProfileRemlTrace(
        initial_lower=lower,
        initial_upper=upper,
        final_lower=left,
        final_upper=right,
        tolerance=tolerance,
        max_iterations=max_iterations,
        iterations=iterations,
        termination_reason=termination_reason,
        candidate=candidate,
        evaluations=tuple(evaluations),
    )


def _reml_fit_traced(
    cluster_ids: tuple[str, ...],
    clusters: dict[str, dict[str, tuple[float, ...]]],
    control_level: str,
    treatment_level: str,
) -> tuple[
    float,
    float,
    float,
    float,
    float,
    bool,
    bool,
    tuple[str, ...],
    ProfileRemlTrace,
]:
    """Profile REML fit plus an explicit bounded-optimizer trace."""

    _, sizes, sums, anchor = _cluster_sums(clusters, control_level, treatment_level)
    observation_count = sum(sizes)
    degrees = observation_count - 2
    trace = _golden_section_maximize_traced(
        cluster_ids, sums, degrees, _LOG_LAMBDA_LOW, _LOG_LAMBDA_HIGH
    )
    log_lambda = trace.candidate.log_lambda
    lam = trace.candidate.lambda_value
    boundary = log_lambda <= _LOG_LAMBDA_LOW + _BOUNDARY_MARGIN or (
        log_lambda >= _LOG_LAMBDA_HIGH - _BOUNDARY_MARGIN
    )
    singular = boundary or lam < 1e-28 or lam > 1e28

    a00, a01, a11, b0, b1, q, _ = _reml_components(cluster_ids, sums, lam)
    determinant = a00 * a11 - a01 * a01
    if not math.isfinite(determinant) or determinant <= 0:
        raise SingularFitError("random-intercept fit produced a singular design matrix")
    beta0_centered = (a11 * b0 - a01 * b1) / determinant
    beta1 = (a00 * b1 - a01 * b0) / determinant
    residual = q - (beta0_centered * b0 + beta1 * b1)
    if not math.isfinite(residual) or residual <= 0:
        raise SingularFitError("random-intercept fit produced a non-positive residual")
    sigma2_e = residual / degrees
    sigma2_u = lam * sigma2_e
    se2 = sigma2_e * a00 / determinant
    intercept = beta0_centered + anchor
    if not all(math.isfinite(value) for value in (beta1, intercept, sigma2_e, sigma2_u, se2)):
        raise SingularFitError("random-intercept fit produced non-finite statistics")
    warnings: tuple[str, ...] = ()
    if singular:
        warnings = (
            "between-cluster variance estimate is at the boundary; "
            "confidence interval and effect size are undefined",
        )
    return beta1, intercept, sigma2_e, sigma2_u, se2, singular, boundary, warnings, trace


def _reml_fit(
    cluster_ids: tuple[str, ...],
    clusters: dict[str, dict[str, tuple[float, ...]]],
    control_level: str,
    treatment_level: str,
) -> tuple[float, float, float, float, float, bool, bool, tuple[str, ...]]:
    """Compatibility wrapper preserving the established estimator return contract."""

    result = _reml_fit_traced(cluster_ids, clusters, control_level, treatment_level)
    return result[:8]


def _mom_fit(
    cluster_ids: tuple[str, ...],
    clusters: dict[str, dict[str, tuple[float, ...]]],
    control_level: str,
    treatment_level: str,
) -> tuple[float, float, float]:
    """Independent centered method-of-moments / ANOVA path."""

    _, sizes, sums, _ = _cluster_sums(clusters, control_level, treatment_level)
    observation_count = sum(sizes)
    cluster_count = len(cluster_ids)
    if cluster_count < 3:
        raise HierarchicalFitError(
            "method-of-moments between-variance estimation requires at least three clusters"
        )
    cross = math.fsum(
        sums[cluster_id][3] - sums[cluster_id][1] * sums[cluster_id][2] / sums[cluster_id][0]
        for cluster_id in cluster_ids
    )
    treatment_variation = math.fsum(
        sums[cluster_id][2] - sums[cluster_id][2] * sums[cluster_id][2] / sums[cluster_id][0]
        for cluster_id in cluster_ids
    )
    if treatment_variation <= 0:
        raise ClusterIdentifiabilityError(
            "no within-cluster treatment variation is available for the contrast"
        )
    within_beta = cross / treatment_variation
    within_residual = math.fsum(
        sums[cluster_id][4]
        - sums[cluster_id][1] * sums[cluster_id][1] / sums[cluster_id][0]
        - 2.0
        * within_beta
        * (sums[cluster_id][3] - sums[cluster_id][1] * sums[cluster_id][2] / sums[cluster_id][0])
        + within_beta
        * within_beta
        * (sums[cluster_id][2] - sums[cluster_id][2] * sums[cluster_id][2] / sums[cluster_id][0])
        for cluster_id in cluster_ids
    )
    within_degrees = observation_count - cluster_count - 1
    if within_degrees <= 0:
        raise HierarchicalFitError("insufficient degrees of freedom for the within estimate")
    sigma2_e = within_residual / within_degrees
    if not math.isfinite(sigma2_e) or sigma2_e <= 0:
        raise SingularFitError("MoM/ANOVA path produced non-positive residual variance")

    weight_sum = float(observation_count)
    weighted_y = math.fsum(sums[cluster_id][1] for cluster_id in cluster_ids)
    weighted_t = math.fsum(sums[cluster_id][2] for cluster_id in cluster_ids)
    overall_y = weighted_y / weight_sum
    overall_t = weighted_t / weight_sum
    stt = math.fsum(
        sums[cluster_id][0] * (sums[cluster_id][2] / sums[cluster_id][0] - overall_t) ** 2
        for cluster_id in cluster_ids
    )
    str_cross = math.fsum(
        sums[cluster_id][0]
        * (sums[cluster_id][2] / sums[cluster_id][0] - overall_t)
        * (sums[cluster_id][1] / sums[cluster_id][0] - overall_y)
        for cluster_id in cluster_ids
    )
    slope = str_cross / stt if stt > 0 else 0.0
    between_residual = math.fsum(
        sums[cluster_id][0]
        * (
            (sums[cluster_id][1] / sums[cluster_id][0] - overall_y)
            - slope * (sums[cluster_id][2] / sums[cluster_id][0] - overall_t)
        )
        ** 2
        for cluster_id in cluster_ids
    )
    between_degrees = cluster_count - 1 if stt <= 0 else cluster_count - 2
    if between_degrees <= 0:
        raise HierarchicalFitError("insufficient degrees of freedom for the between estimate")
    msb = between_residual / between_degrees
    m0 = (observation_count - math.fsum(size * size for size in sizes) / observation_count) / (
        cluster_count - 1
    )
    if not math.isfinite(m0) or m0 <= 0:
        raise SingularFitError("MoM/ANOVA path produced an invalid cluster-size factor")
    sigma2_u = max(0.0, (msb - sigma2_e) / m0)

    if not all(math.isfinite(value) for value in (within_beta, sigma2_u, sigma2_e)):
        raise SingularFitError("MoM/ANOVA path produced non-finite statistics")
    return within_beta, sigma2_u, sigma2_e


def _is_balanced(
    clusters: dict[str, dict[str, tuple[float, ...]]],
    control_level: str,
    treatment_level: str,
) -> bool:
    """Return true only when every cluster has the same treatment allocation/information."""

    allocations = {
        (len(grouped[control_level]), len(grouped[treatment_level]))
        for grouped in clusters.values()
    }
    return len(allocations) == 1


def _has_paired_allocation(
    clusters: dict[str, dict[str, tuple[float, ...]]],
    control_level: str,
    treatment_level: str,
) -> bool:
    """Return whether the independent variance oracle is calibrated for this design.

    The first hierarchical slice preregisters variance-component conformance only
    for one-control/one-treatment pairs. Balanced repeated-observation designs still
    receive the independent effect check, but remain effect-only until a separate
    finite-sample variance calibration suite is registered.
    """

    return all(
        len(grouped[control_level]) == 1 and len(grouped[treatment_level]) == 1
        for grouped in clusters.values()
    )
