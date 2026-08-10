from __future__ import annotations

from dataclasses import replace

import pytest

from arena_hero_research.analysis import analyze_preregistered_paired_outcomes
from arena_hero_research.assignment import AssignmentUnit, generate_assignments
from arena_hero_research.conclusion import (
    ConclusionError,
    ResearchConclusion,
    create_research_conclusion,
)
from arena_hero_research.contracts import ResearchRunStatus
from arena_hero_research.execution import (
    ExecutionProvenance,
    PairedObservation,
    ReplicationResult,
    ReplicationResultStatus,
    build_replication_tasks,
)
from arena_hero_research.lifecycle import ResearchLifecycle, ResearchPhase
from arena_hero_research.replication import ReplicationQualityError, merge_replications
from arena_hero_research.results import ResearchRun, ResultBundle

from .research_fixtures import make_preregistration


def _evidence(score_effects=(0.8, 1.0, 1.2, 1.4)):
    preregistration = make_preregistration()
    assignment = generate_assignments(
        preregistration,
        tuple(AssignmentUnit("scenario-a", seat, "block-a") for seat in range(4)),
        treatment_factor="strategy",
    )
    lifecycle = (
        ResearchLifecycle.create(
            study_id="study-1", preregistration=preregistration, assignment=assignment
        )
        .transition(
            ResearchPhase.EXPLORATORY,
            preregistration=preregistration,
            assignment=assignment,
        )
        .transition(
            ResearchPhase.CONFIRMATORY,
            preregistration=preregistration,
            assignment=assignment,
        )
    )
    provenance = ExecutionProvenance(*("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64))
    tasks = build_replication_tasks(
        lifecycle=lifecycle,
        preregistration=preregistration,
        assignment=assignment,
        provenance_by_environment={"local-reference": provenance},
    )
    results = tuple(
        ReplicationResult.create(
            task=task,
            status=ReplicationResultStatus.COMPLETE,
            observations=(
                PairedObservation(
                    "score",
                    f"pair-{task.replication_index}",
                    0.0,
                    score_effects[task.replication_index],
                ),
                PairedObservation(
                    "latency",
                    f"pair-{task.replication_index}",
                    10.0,
                    10.0 - (0.8 + task.replication_index * 0.2),
                ),
            ),
        )
        for task in tasks
    )
    merge = merge_replications(
        preregistration=preregistration,
        assignment=assignment,
        expected_tasks=tasks,
        results=results,
    )
    estimates, quality = analyze_preregistered_paired_outcomes(
        preregistration,
        merge.observations(),
        bootstrap_seed=20260810,
    )
    run = ResearchRun(
        run_id="research-run-1",
        preregistration=preregistration,
        frozen_config_sha256=provenance.frozen_config_sha256,
        source_build_sha256=provenance.source_build_sha256,
        input_data_sha256=provenance.input_data_sha256,
        environment_sha256=provenance.environment_sha256,
        sbom_sha256=provenance.sbom_sha256,
        status=ResearchRunStatus.COMPLETE,
    )
    bundle = ResultBundle.create(
        run=run,
        estimates=estimates,
        data_quality=quality,
        provenance={"source": "synthetic-known-answer"},
        environment={"class": "local-reference"},
        publishable=True,
    )
    return preregistration, assignment, merge, bundle


def test_qualified_conclusion_requires_effect_ci_quality_and_replication() -> None:
    preregistration, assignment, merge, bundle = _evidence()
    conclusion = create_research_conclusion(
        preregistration=preregistration,
        assignment=assignment,
        replication_merge=merge,
        result_bundle=bundle,
        metadata={"study": {"kind": "synthetic-known-answer"}},
    )

    assert conclusion.qualified
    assert all(item.qualified for item in conclusion.outcomes)
    assert all(item.supporting_replications == 4 for item in conclusion.outcomes)
    assert len(conclusion.replication_evidence) == 8
    assert conclusion.verify()
    assert ResearchConclusion.from_dict(conclusion.to_dict()) == conclusion


def test_null_or_adverse_replication_is_retained_and_prevents_qualification() -> None:
    preregistration, assignment, merge, bundle = _evidence((0.0, 1.0, 1.2, 1.4))
    conclusion = create_research_conclusion(
        preregistration=preregistration,
        assignment=assignment,
        replication_merge=merge,
        result_bundle=bundle,
    )

    score = next(item for item in conclusion.outcomes if item.outcome_name == "score")
    assert not conclusion.qualified
    assert not score.qualified
    assert score.supporting_replications == 3
    assert any(
        item.outcome_name == "score" and item.mean_difference == 0.0
        for item in conclusion.replication_evidence
    )


def test_selective_replication_reporting_is_rejected_by_artifact_invariants() -> None:
    _, _, merge, _ = _evidence()
    with pytest.raises(ReplicationQualityError, match="selectively reported"):
        replace(
            merge,
            replication_evidence=merge.replication_evidence[:-1],
            canonical_sha256=merge.canonical_sha256,
        )


def test_nonpublishable_bundle_and_sensitive_metadata_are_rejected() -> None:
    preregistration, assignment, merge, bundle = _evidence()
    with pytest.raises(ConclusionError, match="publication-eligible"):
        create_research_conclusion(
            preregistration=preregistration,
            assignment=assignment,
            replication_merge=merge,
            result_bundle=replace(bundle, publishable=False),
        )
    with pytest.raises(ValueError, match="sensitive key"):
        create_research_conclusion(
            preregistration=preregistration,
            assignment=assignment,
            replication_merge=merge,
            result_bundle=bundle,
            metadata={"audit": {"secret_token": "forbidden"}},
        )
