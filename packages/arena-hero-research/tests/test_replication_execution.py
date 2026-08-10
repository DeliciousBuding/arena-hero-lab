from __future__ import annotations

from dataclasses import replace

import pytest

from arena_hero_research.assignment import AssignmentUnit, generate_assignments
from arena_hero_research.execution import (
    ExecutionProvenance,
    PairedObservation,
    ReplicationResult,
    ReplicationResultStatus,
    ReplicationTask,
    build_replication_tasks,
)
from arena_hero_research.ledger import (
    DataUseClaim,
    DataUseLedger,
    LedgerConflictError,
    OperationRecord,
)
from arena_hero_research.lifecycle import ResearchLifecycle, ResearchPhase
from arena_hero_research.runner import ReplicationRunner

from .research_fixtures import make_preregistration

_DIGESTS = ("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64)


def _context():
    preregistration = make_preregistration()
    units = tuple(AssignmentUnit("scenario-a", seat, "block-a") for seat in range(4))
    assignment = generate_assignments(preregistration, units, treatment_factor="strategy")
    lifecycle = ResearchLifecycle.create(
        study_id="study-1",
        preregistration=preregistration,
        assignment=assignment,
    )
    lifecycle = lifecycle.transition(
        ResearchPhase.EXPLORATORY,
        preregistration=preregistration,
        assignment=assignment,
    ).transition(
        ResearchPhase.CONFIRMATORY,
        preregistration=preregistration,
        assignment=assignment,
    )
    provenance = ExecutionProvenance(*_DIGESTS)
    tasks = build_replication_tasks(
        lifecycle=lifecycle,
        preregistration=preregistration,
        assignment=assignment,
        provenance_by_environment={"local-reference": provenance},
    )
    return preregistration, assignment, lifecycle, tasks


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, task: ReplicationTask) -> ReplicationResult:
        self.calls += 1
        index = task.replication_index
        return ReplicationResult.create(
            task=task,
            status=ReplicationResultStatus.COMPLETE,
            observations=(
                PairedObservation("score", f"pair-{index}", 0.0, 1.0),
                PairedObservation("latency", f"pair-{index}", 10.0, 9.0),
            ),
            metadata={"executor": {"kind": "synthetic-known-answer"}},
        )


def test_tasks_and_results_are_versioned_round_trippable_and_digest_bound() -> None:
    _, _, _, tasks = _context()
    assert [task.seed for task in tasks] == [11, 22, 33, 44]
    assert all(task.verify() for task in tasks)
    assert ReplicationTask.from_dict(tasks[0].to_dict()) == tasks[0]

    result = FakeExecutor().execute(tasks[0])
    assert result.verify()
    assert ReplicationResult.from_dict(result.to_dict()) == result


def test_runner_replays_same_operation_without_reexecuting() -> None:
    _, _, _, tasks = _context()
    executor = FakeExecutor()
    runner = ReplicationRunner(executor)

    first = runner.run(operation_id="operation-1", tasks=tasks)
    second = runner.run(operation_id="operation-1", tasks=tasks)

    assert first == second
    assert executor.calls == len(tasks)
    record = runner.operation_ledger.record("operation-1")
    assert record is not None and record.verify()
    assert OperationRecord.from_dict(record.to_dict()) == record


def test_idempotency_conflict_fails_closed() -> None:
    _, _, _, tasks = _context()
    runner = ReplicationRunner(FakeExecutor())
    runner.run(operation_id="operation-1", tasks=tasks)

    changed = replace(tasks[0], canonical_sha256="f" * 64)
    with pytest.raises(ValueError, match="digest verification"):
        runner.run(operation_id="operation-1", tasks=(changed, *tasks[1:]))
    with pytest.raises(LedgerConflictError, match="conflicting plan"):
        runner.run(operation_id="operation-1", tasks=tasks[:-1])


def test_pilot_data_cannot_be_reused_as_confirmatory_holdout() -> None:
    _, _, _, tasks = _context()
    data_ledger = DataUseLedger()
    data_ledger.claim(
        DataUseClaim(
            dataset_sha256=tasks[0].provenance.input_data_sha256,
            study_id="study-1",
            role="pilot",
            operation_id="pilot-operation",
        )
    )
    runner = ReplicationRunner(FakeExecutor(), data_use_ledger=data_ledger)
    with pytest.raises(LedgerConflictError, match="cannot be reused"):
        runner.run(operation_id="confirmatory-operation", tasks=tasks)


def test_holdout_cannot_be_reused_by_a_second_operation() -> None:
    _, _, _, tasks = _context()
    runner = ReplicationRunner(FakeExecutor())
    runner.run(operation_id="operation-1", tasks=tasks)
    with pytest.raises(LedgerConflictError, match="already belongs"):
        runner.run(operation_id="operation-2", tasks=tasks)


def test_nested_sensitive_result_metadata_is_rejected() -> None:
    _, _, _, tasks = _context()
    with pytest.raises(ValueError, match="sensitive key"):
        ReplicationResult.create(
            task=tasks[0],
            status=ReplicationResultStatus.COMPLETE,
            observations=(PairedObservation("score", "pair-1", 0.0, 1.0),),
            metadata={"runtime": {"api_token": "forbidden"}},
        )


def test_data_use_claim_rejects_unknown_role() -> None:
    with pytest.raises(LedgerConflictError, match="research lifecycle"):
        DataUseClaim(
            dataset_sha256="a" * 64,
            study_id="study-1",
            role="selected-after-results",
            operation_id="operation-1",
        )
