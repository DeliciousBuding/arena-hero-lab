from __future__ import annotations

from dataclasses import replace

import pytest

from arena_hero_research.assignment import AssignmentUnit, generate_assignments
from arena_hero_research.contracts import MissingDataPolicy, Preregistration
from arena_hero_research.execution import (
    DroppedPair,
    ExecutionProvenance,
    PairedObservation,
    ReplicationResult,
    ReplicationResultStatus,
    ReplicationTask,
    build_replication_tasks,
)
from arena_hero_research.lifecycle import ResearchLifecycle, ResearchPhase
from arena_hero_research.replication import (
    ReplicationMerge,
    ReplicationQualityError,
    merge_replications,
)
from arena_hero_sim.serialization import JsonValue, content_sha256

from .research_fixtures import make_preregistration


def _setup(*, missing_policy: MissingDataPolicy = MissingDataPolicy.FAIL, pairs: int = 1):
    preregistration = make_preregistration(missing_policy=missing_policy)
    if pairs != 1:
        replication_plan = replace(
            preregistration.design.replication_plan,
            observations_per_replication=pairs,
        )
        design = replace(preregistration.design, replication_plan=replication_plan)
        preregistration = Preregistration.create(
            question=preregistration.question,
            hypotheses=preregistration.hypotheses,
            design=design,
            registered_at=preregistration.registered_at,
        )
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
    return preregistration, assignment, tasks


def _result(
    task: ReplicationTask,
    *,
    status: ReplicationResultStatus = ReplicationResultStatus.COMPLETE,
    pairs: int = 1,
    missing_score: bool = False,
    explicit_drop: bool = False,
) -> ReplicationResult:
    observations = []
    drops = []
    for offset in range(pairs):
        pair_id = f"pair-{task.replication_index}-{offset}"
        observations.append(
            PairedObservation(
                "score",
                pair_id,
                None if missing_score and offset == 0 else 0.0,
                1.0,
            )
        )
        observations.append(PairedObservation("latency", pair_id, 10.0, 9.0))
        if missing_score and offset == 0 and explicit_drop:
            drops.append(DroppedPair("score", pair_id, "preregistered-drop-pair"))
    return ReplicationResult.create(
        task=task,
        status=status,
        observations=tuple(observations),
        dropped_pairs=tuple(drops),
    )


def _forge_task(task: ReplicationTask, **changes: JsonValue) -> ReplicationTask:
    payload = task.payload()
    payload.update(changes)
    payload["canonical_sha256"] = content_sha256(
        {key: value for key, value in payload.items() if key != "canonical_sha256"}
    )
    return ReplicationTask.from_dict(payload)


def test_replication_merge_is_deterministic_complete_and_round_trippable() -> None:
    preregistration, assignment, tasks = _setup()
    results = tuple(_result(task) for task in tasks)
    merge = merge_replications(
        preregistration=preregistration,
        assignment=assignment,
        expected_tasks=tasks,
        results=tuple(reversed(results)),
    )

    assert merge.verify()
    assert merge.successful_replications == 4
    assert merge.observations()["score"] == ((0.0,) * 4, (1.0,) * 4)
    assert len(merge.replication_evidence) == 8
    assert ReplicationMerge.from_dict(merge.to_dict()) == merge


@pytest.mark.parametrize("case", ["duplicate", "missing", "partial"])
def test_replication_merge_rejects_duplicate_missing_and_partial(case: str) -> None:
    preregistration, assignment, tasks = _setup()
    results = tuple(_result(task) for task in tasks)
    if case == "duplicate":
        bad_results = (*results, results[0])
    elif case == "missing":
        bad_results = results[:-1]
    else:
        bad_results = (
            _result(tasks[0], status=ReplicationResultStatus.PARTIAL),
            *results[1:],
        )
    with pytest.raises(ReplicationQualityError, match=case):
        merge_replications(
            preregistration=preregistration,
            assignment=assignment,
            expected_tasks=tasks,
            results=bad_results,
        )


def test_replication_merge_rejects_mixed_environment_or_plan_identity() -> None:
    preregistration, assignment, tasks = _setup()
    forged_environment = _forge_task(tasks[0], environment="other-environment")
    forged_plan = _forge_task(tasks[1], analysis_plan_sha256="f" * 64)
    for forged in (forged_environment, forged_plan):
        results = tuple(
            _result(forged if task.replication_index == forged.replication_index else task)
            for task in tasks
        )
        with pytest.raises(ReplicationQualityError, match="identity or provenance"):
            merge_replications(
                preregistration=preregistration,
                assignment=assignment,
                expected_tasks=tasks,
                results=results,
            )


def test_drop_pair_must_be_explicit_and_remain_above_planned_sample_size() -> None:
    preregistration, assignment, tasks = _setup(
        missing_policy=MissingDataPolicy.DROP_PAIR,
        pairs=2,
    )
    without_record = (
        _result(tasks[0], pairs=2, missing_score=True),
        *(_result(task, pairs=2) for task in tasks[1:]),
    )
    with pytest.raises(ReplicationQualityError, match="explicit"):
        merge_replications(
            preregistration=preregistration,
            assignment=assignment,
            expected_tasks=tasks,
            results=without_record,
        )

    with_record = (
        _result(tasks[0], pairs=2, missing_score=True, explicit_drop=True),
        *(_result(task, pairs=2) for task in tasks[1:]),
    )
    merge = merge_replications(
        preregistration=preregistration,
        assignment=assignment,
        expected_tasks=tasks,
        results=with_record,
    )
    assert len(merge.dropped_pairs) == 1
    assert len(merge.observations()["score"][0]) == 7


def test_fail_policy_rejects_missing_pair_even_if_drop_is_declared() -> None:
    preregistration, assignment, tasks = _setup()
    results = (
        _result(tasks[0], missing_score=True, explicit_drop=True),
        *(_result(task) for task in tasks[1:]),
    )
    with pytest.raises(ReplicationQualityError, match="FAIL policy"):
        merge_replications(
            preregistration=preregistration,
            assignment=assignment,
            expected_tasks=tasks,
            results=results,
        )
