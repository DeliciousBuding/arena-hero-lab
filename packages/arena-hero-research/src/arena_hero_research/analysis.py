"""Preregistered paired analysis, effect sizes, confidence intervals, and multiplicity."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from statistics import NormalDist

from arena_hero_research.contracts import (
    AnalysisPlan,
    Hypothesis,
    HypothesisDirection,
    MissingDataPolicy,
    MultipleComparisonPolicy,
    Outcome,
    OutcomeRole,
    Preregistration,
)


class ResearchAnalysisError(ValueError):
    pass


class MissingObservationError(ResearchAnalysisError):
    pass


class UndeclaredOutcomeError(ResearchAnalysisError):
    pass


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    outcome_name: str
    total_pairs: int
    complete_pairs: int
    missing_pairs: int
    dropped_pairs: int
    policy: MissingDataPolicy
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EffectEstimate:
    outcome_name: str
    hypothesis_id: str
    sample_size: int
    mean_difference: float
    standardized_effect: float | None
    confidence_lower: float
    confidence_upper: float
    confidence_level: float
    raw_p_value: float
    adjusted_p_value: float
    meets_minimum_effect: bool
    estimator: str
    effect_size_method: str
    ci_method: str
    p_value_method: str
    warnings: tuple[str, ...] = ()


def _clean_pairs(
    outcome: Outcome,
    control: Sequence[float | None],
    treatment: Sequence[float | None],
) -> tuple[tuple[float, ...], tuple[float, ...], DataQualityReport]:
    if len(control) != len(treatment):
        raise MissingObservationError("paired samples must have equal length")
    clean_control: list[float] = []
    clean_treatment: list[float] = []
    missing = 0
    for left, right in zip(control, treatment, strict=True):
        if left is None or right is None:
            missing += 1
            if outcome.missing_data_policy is MissingDataPolicy.FAIL:
                raise MissingObservationError(f"missing pair for outcome {outcome.name}")
            continue
        baseline = float(left)
        treated = float(right)
        if not math.isfinite(baseline) or not math.isfinite(treated):
            raise MissingObservationError(f"non-finite observation for outcome {outcome.name}")
        clean_control.append(baseline)
        clean_treatment.append(treated)
    if len(clean_control) < 2:
        raise MissingObservationError("at least two complete pairs are required")
    warnings = (
        ("paired observations were dropped according to the preregistered policy",)
        if missing
        else ()
    )
    report = DataQualityReport(
        outcome_name=outcome.name,
        total_pairs=len(control),
        complete_pairs=len(clean_control),
        missing_pairs=missing,
        dropped_pairs=missing,
        policy=outcome.missing_data_policy,
        warnings=warnings,
    )
    return tuple(clean_control), tuple(clean_treatment), report


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _p_value(
    mean_difference: float,
    standard_error: float,
    null_value: float,
    direction: HypothesisDirection,
) -> float:
    delta = mean_difference - null_value
    if standard_error == 0:
        if delta == 0:
            return 1.0
        if direction is HypothesisDirection.TWO_SIDED:
            return 0.0
        if direction is HypothesisDirection.GREATER:
            return 0.0 if delta > 0 else 1.0
        return 0.0 if delta < 0 else 1.0
    z_score = delta / standard_error
    distribution = NormalDist()
    if direction is HypothesisDirection.GREATER:
        return 1 - distribution.cdf(z_score)
    if direction is HypothesisDirection.LESS:
        return distribution.cdf(z_score)
    return min(1.0, 2 * (1 - distribution.cdf(abs(z_score))))


def _meets_minimum_effect(hypothesis: Hypothesis, mean_difference: float) -> bool:
    delta = mean_difference - hypothesis.null_value
    if hypothesis.direction is HypothesisDirection.GREATER:
        return delta >= hypothesis.minimum_effect
    if hypothesis.direction is HypothesisDirection.LESS:
        return -delta >= hypothesis.minimum_effect
    return abs(delta) >= hypothesis.minimum_effect


def paired_effect_with_bootstrap_ci(
    *,
    outcome: Outcome,
    hypothesis: Hypothesis,
    control: Sequence[float | None],
    treatment: Sequence[float | None],
    plan: AnalysisPlan,
    bootstrap_seed: int,
) -> tuple[EffectEstimate, DataQualityReport]:
    """Estimate treatment-control effect using paired bootstrap and Cohen's dz."""
    clean_control, clean_treatment, quality = _clean_pairs(outcome, control, treatment)
    if quality.complete_pairs < plan.planned_sample_size:
        raise MissingObservationError(
            f"outcome {outcome.name} has {quality.complete_pairs} complete pairs; "
            f"the preregistered plan requires {plan.planned_sample_size}"
        )
    differences = tuple(
        treated - baseline for baseline, treated in zip(clean_control, clean_treatment, strict=True)
    )
    mean_difference = statistics.fmean(differences)
    standard_deviation = statistics.stdev(differences)
    standardized_effect = None if standard_deviation == 0 else mean_difference / standard_deviation
    standard_error = standard_deviation / math.sqrt(len(differences))
    rng = random.Random(bootstrap_seed)
    bootstrap_means = []
    for _ in range(plan.bootstrap_samples):
        sample = [differences[rng.randrange(len(differences))] for _ in differences]
        bootstrap_means.append(statistics.fmean(sample))
    tail = (1 - plan.confidence_level) / 2
    raw_p_value = _p_value(
        mean_difference,
        standard_error,
        hypothesis.null_value,
        hypothesis.direction,
    )
    warnings = (
        ("paired differences have zero variance; standardized effect is undefined",)
        if standard_deviation == 0
        else ()
    )
    return EffectEstimate(
        outcome_name=outcome.name,
        hypothesis_id=hypothesis.hypothesis_id,
        sample_size=len(differences),
        mean_difference=mean_difference,
        standardized_effect=standardized_effect,
        confidence_lower=_percentile(bootstrap_means, tail),
        confidence_upper=_percentile(bootstrap_means, 1 - tail),
        confidence_level=plan.confidence_level,
        raw_p_value=raw_p_value,
        adjusted_p_value=raw_p_value,
        meets_minimum_effect=_meets_minimum_effect(hypothesis, mean_difference),
        estimator=plan.estimator,
        effect_size_method=plan.effect_size,
        ci_method=plan.ci_method,
        p_value_method="paired-normal-approximation",
        warnings=warnings,
    ), quality


def benjamini_hochberg(p_values: Mapping[str, float]) -> dict[str, float]:
    """Return monotone Benjamini-Hochberg adjusted p-values by comparison id."""
    if not p_values:
        return {}
    for value in p_values.values():
        if not 0 <= value <= 1:
            raise ValueError("p-values must be between zero and one")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 1.0
    count = len(ordered)
    for rank, (name, value) in reversed(list(enumerate(ordered, start=1))):
        running = min(running, value * count / rank)
        adjusted[name] = min(1.0, running)
    return adjusted


def normal_approx_paired_sample_size(
    *,
    effect_size: float,
    alpha: float = 0.05,
    power: float = 0.8,
    two_sided: bool = True,
) -> int:
    """Approximate paired sample size; final studies should validate assumptions."""
    if effect_size <= 0 or not 0 < alpha < 1 or not 0 < power < 1:
        raise ValueError("effect_size, alpha, and power must be valid positive probabilities")
    normal = NormalDist()
    alpha_quantile = 1 - alpha / (2 if two_sided else 1)
    return math.ceil(((normal.inv_cdf(alpha_quantile) + normal.inv_cdf(power)) / effect_size) ** 2)


def analyze_preregistered_paired_outcomes(
    preregistration: Preregistration,
    observations: Mapping[str, tuple[Sequence[float | None], Sequence[float | None]]],
    *,
    bootstrap_seed: int,
) -> tuple[tuple[EffectEstimate, ...], Mapping[str, DataQualityReport]]:
    """Analyze every preregistered confirmatory outcome; never select the best result."""
    if not preregistration.verify():
        raise ResearchAnalysisError("preregistration digest verification failed")
    declared = {item.name: item for item in preregistration.design.outcomes}
    unknown = set(observations) - set(declared)
    if unknown:
        raise UndeclaredOutcomeError(f"undeclared outcomes: {', '.join(sorted(unknown))}")
    confirmatory = tuple(
        item for item in preregistration.design.outcomes if item.role is not OutcomeRole.EXPLORATORY
    )
    missing = {item.name for item in confirmatory} - set(observations)
    if missing:
        raise UndeclaredOutcomeError(
            "all preregistered confirmatory outcomes are required: " + ", ".join(sorted(missing))
        )
    hypotheses = {item.outcome_name: item for item in preregistration.hypotheses}
    estimates: list[EffectEstimate] = []
    quality: dict[str, DataQualityReport] = {}
    for index, outcome in enumerate(confirmatory):
        control, treatment = observations[outcome.name]
        estimate, report = paired_effect_with_bootstrap_ci(
            outcome=outcome,
            hypothesis=hypotheses[outcome.name],
            control=control,
            treatment=treatment,
            plan=preregistration.design.analysis_plan,
            bootstrap_seed=bootstrap_seed + index,
        )
        estimates.append(estimate)
        quality[outcome.name] = report
    plan = preregistration.design.analysis_plan
    if plan.multiple_comparison_policy is MultipleComparisonPolicy.BENJAMINI_HOCHBERG:
        adjusted = benjamini_hochberg({item.hypothesis_id: item.raw_p_value for item in estimates})
        estimates = [
            replace(item, adjusted_p_value=adjusted[item.hypothesis_id]) for item in estimates
        ]
    return tuple(estimates), quality
