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
ANOVA path is provided for cross-validation; the two paths must agree within a
declared numerical tolerance on balanced fixtures (and a looser tolerance on
mildly unbalanced ones). The paired design (one control and one treatment per
cluster) is the balanced degenerate case, and the bridge adapter
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
from dataclasses import dataclass
from enum import StrEnum

from arena_hero_research.execution import PairedObservation
from arena_hero_research.statistics import golden_section_maximize, student_t_inv_cdf
from arena_hero_research.validation import (
    require_float,
    require_identifier,
    require_int,
    require_sequence,
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
_SCHEMA = "arena.research.random-intercept-fit.v2"
_LOG_LAMBDA_LOW = -30.0
_LOG_LAMBDA_HIGH = 30.0
_BOUNDARY_MARGIN = 1.0


class HierarchicalFitError(ValueError):
    """Base error for the hierarchical model surface."""


class ClusterIdentifiabilityError(HierarchicalFitError):
    """The design cannot identify a within-cluster treatment contrast."""


class SingularFitError(HierarchicalFitError):
    """The variance structure is degenerate and no estimate is produced."""


class ClusterMissingPolicy(StrEnum):
    """Declared handling of clusters with an incomplete treatment pair."""

    FAIL = "fail"
    DROP_CLUSTER = "drop-cluster"


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
        if not math.isfinite(self.value):
            raise ValueError("value must be finite")

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
        return cls(
            outcome_name=str(value["outcome_name"]),
            cluster_id=str(value["cluster_id"]),
            observation_id=str(value["observation_id"]),
            treatment=str(value["treatment"]),
            value=require_float(value["value"], "value"),
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
        if self.dropped_clusters < 0 or self.dropped_clusters >= self.cluster_count:
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
        warnings = require_sequence(value["warnings"], "warnings")
        restored = cls(
            schema_version=str(value["schema_version"]),
            outcome_name=str(value["outcome_name"]),
            control_level=str(value["control_level"]),
            treatment_level=str(value["treatment_level"]),
            estimator=str(value["estimator"]),
            effect_size_method=str(value["effect_size_method"]),
            ci_method=str(value["ci_method"]),
            confidence_level=require_float(value["confidence_level"], "confidence_level"),
            estimand=str(value["estimand"]),
            cluster_count=require_int(value["cluster_count"], "cluster_count"),
            observation_count=require_int(value["observation_count"], "observation_count"),
            dropped_clusters=require_int(value["dropped_clusters"], "dropped_clusters"),
            intercept=require_float(value["intercept"], "intercept"),
            treatment_effect=require_float(value["treatment_effect"], "treatment_effect"),
            standard_error=_optional_float(value.get("standard_error"), "standard_error"),
            between_variance=require_float(value["between_variance"], "between_variance"),
            error_variance=require_float(value["error_variance"], "error_variance"),
            icc=require_float(value["icc"], "icc"),
            hierarchical_effect=_optional_float(
                value.get("hierarchical_effect"), "hierarchical_effect"
            ),
            degrees_of_freedom=require_int(value["degrees_of_freedom"], "degrees_of_freedom"),
            confidence_lower=_optional_float(value.get("confidence_lower"), "confidence_lower"),
            confidence_upper=_optional_float(value.get("confidence_upper"), "confidence_upper"),
            singular=value["singular"] is True,
            boundary_lambda=value["boundary_lambda"] is True,
            warnings=tuple(str(item) for item in warnings),
            canonical_sha256=str(value["canonical_sha256"]),
        )
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

    normalized_outcome = require_identifier(outcome_name, "outcome_name")
    control, treatment = _normalize_contrast(control_level, treatment_level)
    policy = _normalize_missing_policy(missing_policy)
    if not 0.0 < confidence_level < 1.0:
        raise HierarchicalFitError("confidence_level must be between zero and one")

    def calculate() -> RandomInterceptFit:
        clusters, dropped = _prepare_clusters(
            normalized_outcome, observations, policy, control, treatment
        )
        cluster_ids = tuple(sorted(clusters))
        cluster_count = len(cluster_ids)
        observation_count = sum(
            len(values) for grouped in clusters.values() for values in grouped.values()
        )
        beta, intercept, sigma2_e, sigma2_u, se2, singular, boundary, warnings = _reml_fit(
            cluster_ids, clusters, control, treatment
        )
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
        if not all(
            math.isfinite(value) for value in (beta, intercept, sigma2_e, sigma2_u, se2, icc)
        ):
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
        return RandomInterceptFit(
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

    return _numeric_boundary("random-intercept fit", calculate)


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
        if balanced:
            effect_tolerance = max(1e-8, 1e-8 * abs(reml_effect))
            variance_tolerance = max(1e-7, 1e-7 * reml_between, 1e-7 * reml_error)
            enforce_variance = True
        else:
            # Within-OLS and REML use different weighting under allocation imbalance.
            effect_tolerance = max(1e-2, 1e-2 * abs(reml_effect))
            variance_tolerance = max(1e-2, 1e-2 * reml_between, 1e-2 * reml_error)
            enforce_variance = False
        effect_difference = abs(ols_effect - reml_effect)
        variance_difference = max(abs(mom_between - reml_between), abs(mom_error - reml_error))
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
            passed=effect_difference <= effect_tolerance
            and (not enforce_variance or variance_difference <= variance_tolerance),
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
    raw: dict[str, dict[str, list[float]]] = {}
    treatment_levels: set[str] = set()
    for item in observations:
        if not isinstance(item, ClusterObservation):
            raise HierarchicalFitError("observations must be ClusterObservation values")
        if item.outcome_name != outcome_name:
            raise HierarchicalFitError(
                f"observation outcome {item.outcome_name} does not match requested outcome"
            )
        treatment_levels.add(item.treatment)
        raw.setdefault(item.cluster_id, {}).setdefault(item.treatment, []).append(item.value)
    expected_levels = {control_level, treatment_level}
    if treatment_levels != expected_levels:
        raise HierarchicalFitError(
            "observations must use exactly two treatment levels (control and treatment)"
        )
    levels = (control_level, treatment_level)

    dropped = 0
    retained: dict[str, dict[str, tuple[float, ...]]] = {}
    for cluster_id, grouped in sorted(raw.items()):
        present = {level: tuple(grouped[level]) for level in levels if level in grouped}
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
) -> tuple[tuple[str, ...], tuple[int, ...], dict[str, tuple[float, float, float, float, float]]]:
    """Return sorted cluster ids, sizes, and per-cluster (n, sy, st, sty, sq)."""

    ids = tuple(sorted(clusters))
    sizes: list[int] = []
    sums: dict[str, tuple[float, float, float, float, float]] = {}
    for cluster_id in ids:
        baseline = clusters[cluster_id][control_level]
        contrast = clusters[cluster_id][treatment_level]
        size = len(baseline) + len(contrast)
        sizes.append(size)
        total = sum(baseline) + sum(contrast)
        treatment_count = len(contrast)
        treatment_total = sum(contrast)
        squares = sum(value * value for value in (*baseline, *contrast))
        sums[cluster_id] = (float(size), total, float(treatment_count), treatment_total, squares)
    return ids, tuple(sizes), sums


def _reml_fit(
    cluster_ids: tuple[str, ...],
    clusters: dict[str, dict[str, tuple[float, ...]]],
    control_level: str,
    treatment_level: str,
) -> tuple[float, float, float, float, float, bool, bool, tuple[str, ...]]:
    """Profile REML fit.

    Returns (beta, mu, sigma2_e, sigma2_u, se2, singular, boundary, warnings).
    """

    _, sizes, sums = _cluster_sums(clusters, control_level, treatment_level)
    observation_count = sum(sizes)
    degrees = observation_count - 2

    def profile(log_lambda: float) -> float:
        lam = math.exp(log_lambda)
        a00 = a01 = a11 = 0.0
        b0 = b1 = 0.0
        q = 0.0
        log_terms = 0.0
        for cluster_id in cluster_ids:
            size, total, treatment_count, treatment_total, squares = sums[cluster_id]
            coeff = lam / (1.0 + size * lam)
            a00 += size - coeff * size * size
            a01 += treatment_count - coeff * size * treatment_count
            a11 += treatment_count - coeff * treatment_count * treatment_count
            b0 += total - coeff * size * total
            b1 += treatment_total - coeff * treatment_count * total
            q += squares - coeff * total * total
            log_terms += math.log1p(size * lam)
        determinant = a00 * a11 - a01 * a01
        scale = a00 * a11
        if determinant <= 0 or scale <= 0 or determinant < 1e-14 * scale or q <= 0:
            return -1e300
        beta0 = (a11 * b0 - a01 * b1) / determinant
        beta1 = (a00 * b1 - a01 * b0) / determinant
        residual = q - (beta0 * b0 + beta1 * b1)
        if residual <= 0 or residual < 1e-12 * q:
            return -1e300
        return -0.5 * (degrees * math.log(residual) + math.log(determinant) + log_terms)

    log_lambda, _ = golden_section_maximize(profile, _LOG_LAMBDA_LOW, _LOG_LAMBDA_HIGH)
    lam = math.exp(log_lambda)
    boundary = log_lambda <= _LOG_LAMBDA_LOW + _BOUNDARY_MARGIN or (
        log_lambda >= _LOG_LAMBDA_HIGH - _BOUNDARY_MARGIN
    )
    singular = boundary or lam < 1e-28 or lam > 1e28

    a00 = a01 = a11 = 0.0
    b0 = b1 = 0.0
    q = 0.0
    for cluster_id in cluster_ids:
        size, total, treatment_count, treatment_total, squares = sums[cluster_id]
        coeff = lam / (1.0 + size * lam)
        a00 += size - coeff * size * size
        a01 += treatment_count - coeff * size * treatment_count
        a11 += treatment_count - coeff * treatment_count * treatment_count
        b0 += total - coeff * size * total
        b1 += treatment_total - coeff * treatment_count * total
        q += squares - coeff * total * total
    determinant = a00 * a11 - a01 * a01
    if determinant <= 0:
        raise SingularFitError("random-intercept fit produced a singular design matrix")
    beta0 = (a11 * b0 - a01 * b1) / determinant
    beta1 = (a00 * b1 - a01 * b0) / determinant
    residual = q - (beta0 * b0 + beta1 * b1)
    if residual <= 0:
        raise SingularFitError("random-intercept fit produced a non-positive residual")
    sigma2_e = residual / degrees
    sigma2_u = lam * sigma2_e
    se2 = sigma2_e * a00 / determinant
    warnings: tuple[str, ...] = ()
    if singular:
        warnings = (
            "between-cluster variance estimate is at the boundary; "
            "confidence interval and effect size are undefined",
        )
    return beta1, beta0, sigma2_e, sigma2_u, se2, singular, boundary, warnings


def _mom_fit(
    cluster_ids: tuple[str, ...],
    clusters: dict[str, dict[str, tuple[float, ...]]],
    control_level: str,
    treatment_level: str,
) -> tuple[float, float, float]:
    """Independent method-of-moments / ANOVA path (cross-validation only)."""

    _, sizes, sums = _cluster_sums(clusters, control_level, treatment_level)
    observation_count = sum(sizes)
    cluster_count = len(cluster_ids)
    if cluster_count < 3:
        raise HierarchicalFitError(
            "method-of-moments between-variance estimation requires at least three clusters"
        )
    cross = 0.0
    treatment_variation = 0.0
    for cluster_id in cluster_ids:
        size, total, treatment_count, treatment_total, _ = sums[cluster_id]
        cross += treatment_total - total * treatment_count / size
        treatment_variation += treatment_count - treatment_count**2 / size
    if treatment_variation <= 0:
        raise ClusterIdentifiabilityError(
            "no within-cluster treatment variation is available for the contrast"
        )
    within_beta = cross / treatment_variation
    within_residual = 0.0
    for cluster_id in cluster_ids:
        size, total, treatment_count, treatment_total, square_sum = sums[cluster_id]
        within_residual += (
            square_sum
            - total * total / size
            - 2.0 * within_beta * (treatment_total - total * treatment_count / size)
            + within_beta * within_beta * (treatment_count - treatment_count**2 / size)
        )
    within_degrees = observation_count - cluster_count - 1
    if within_degrees <= 0:
        raise HierarchicalFitError("insufficient degrees of freedom for the within estimate")
    sigma2_e = within_residual / within_degrees

    weight_sum = 0.0
    weighted_y = 0.0
    weighted_t = 0.0
    for cluster_id, size in zip(cluster_ids, sizes, strict=True):
        _, total, treatment_count, _, _ = sums[cluster_id]
        weight_sum += size
        weighted_y += total
        weighted_t += treatment_count
    overall_y = weighted_y / weight_sum
    overall_t = weighted_t / weight_sum
    stt = 0.0
    str_cross = 0.0
    for cluster_id, size in zip(cluster_ids, sizes, strict=True):
        _, total, treatment_count, _, _ = sums[cluster_id]
        y_bar = total / size
        t_bar = treatment_count / size
        stt += size * (t_bar - overall_t) ** 2
        str_cross += size * (t_bar - overall_t) * (y_bar - overall_y)
    slope = str_cross / stt if stt > 0 else 0.0
    between_residual = 0.0
    for cluster_id, size in zip(cluster_ids, sizes, strict=True):
        _, total, treatment_count, _, _ = sums[cluster_id]
        y_bar = total / size
        t_bar = treatment_count / size
        residual = (y_bar - overall_y) - slope * (t_bar - overall_t)
        between_residual += size * residual * residual
    # Balanced or proportional designs leave the cluster-mean treatment slope
    # unidentifiable (stt == 0), so only the grand mean is estimated: K - 1
    # residual degrees of freedom. Unbalanced designs estimate the slope too.
    between_degrees = cluster_count - 1 if stt <= 0 else cluster_count - 2
    if between_degrees <= 0:
        raise HierarchicalFitError("insufficient degrees of freedom for the between estimate")
    msb = between_residual / between_degrees
    m0 = (observation_count - sum(size * size for size in sizes) / observation_count) / (
        cluster_count - 1
    )
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
