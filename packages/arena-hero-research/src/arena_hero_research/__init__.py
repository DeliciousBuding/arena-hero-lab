"""Preregistered research contracts and reproducible analysis for Arena Hero Lab."""

from arena_hero_research.analysis import (
    DataQualityReport,
    EffectEstimate,
    MissingObservationError,
    ResearchAnalysisError,
    UndeclaredOutcomeError,
    analyze_preregistered_paired_outcomes,
    benjamini_hochberg,
    normal_approx_paired_sample_size,
    paired_effect_with_bootstrap_ci,
)
from arena_hero_research.contracts import (
    AnalysisPlan,
    ExperimentDesign,
    Factor,
    Hypothesis,
    HypothesisDirection,
    MissingDataPolicy,
    MultipleComparisonPolicy,
    Outcome,
    OutcomeRole,
    Preregistration,
    ReplicationPlan,
    ResearchQuestion,
    ResearchRunStatus,
)
from arena_hero_research.results import (
    AnalysisPlanMismatchError,
    ResearchBundleError,
    ResearchRun,
    ResultBundle,
)
from arena_hero_research.statistics import arithmetic_mean

__all__ = [
    "AnalysisPlan",
    "AnalysisPlanMismatchError",
    "DataQualityReport",
    "EffectEstimate",
    "ExperimentDesign",
    "Factor",
    "Hypothesis",
    "HypothesisDirection",
    "MissingDataPolicy",
    "MissingObservationError",
    "MultipleComparisonPolicy",
    "Outcome",
    "OutcomeRole",
    "Preregistration",
    "ReplicationPlan",
    "ResearchAnalysisError",
    "ResearchBundleError",
    "ResearchQuestion",
    "ResearchRun",
    "ResearchRunStatus",
    "ResultBundle",
    "UndeclaredOutcomeError",
    "analyze_preregistered_paired_outcomes",
    "arithmetic_mean",
    "benjamini_hochberg",
    "normal_approx_paired_sample_size",
    "paired_effect_with_bootstrap_ci",
]
__version__ = "0.2.0"
