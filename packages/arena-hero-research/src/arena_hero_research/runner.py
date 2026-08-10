"""Injected offline replication runner with deterministic replay semantics."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from arena_hero_research.execution import (
    ReplicationError,
    ReplicationResult,
    ReplicationTask,
)
from arena_hero_research.ledger import DataUseClaim, DataUseLedger, OperationLedger
from arena_hero_sim.serialization import content_sha256


class ReplicationExecutor(Protocol):
    def execute(self, task: ReplicationTask) -> ReplicationResult: ...


class ReplicationRunner:
    def __init__(
        self,
        executor: ReplicationExecutor,
        *,
        operation_ledger: OperationLedger | None = None,
        data_use_ledger: DataUseLedger | None = None,
    ) -> None:
        self._executor = executor
        self._operation_ledger = operation_ledger or OperationLedger()
        self._data_use_ledger = data_use_ledger or DataUseLedger()

    @property
    def operation_ledger(self) -> OperationLedger:
        return self._operation_ledger

    @property
    def data_use_ledger(self) -> DataUseLedger:
        return self._data_use_ledger

    def run(
        self, *, operation_id: str, tasks: Sequence[ReplicationTask]
    ) -> tuple[ReplicationResult, ...]:
        ordered = tuple(sorted(tasks, key=lambda item: item.replication_index))
        if not ordered or len({item.task_id for item in ordered}) != len(ordered):
            raise ReplicationError("replication tasks must be non-empty and uniquely identified")
        if any(not item.verify() for item in ordered):
            raise ReplicationError("replication task digest verification failed")
        plan_sha256 = content_sha256([item.to_dict() for item in ordered])

        def execute() -> tuple[ReplicationResult, ...]:
            results: list[ReplicationResult] = []
            for task in ordered:
                self._data_use_ledger.claim(
                    DataUseClaim(
                        dataset_sha256=task.provenance.input_data_sha256,
                        study_id=task.study_id,
                        role=task.data_role.value,
                        operation_id=operation_id,
                    )
                )
                result = self._executor.execute(task)
                if result.task != task:
                    raise ReplicationError("executor returned a result for a different task")
                if not result.verify():
                    raise ReplicationError("executor returned a result with an invalid digest")
                results.append(result)
            return tuple(results)

        return self._operation_ledger.execute(
            operation_id=operation_id,
            plan_sha256=plan_sha256,
            execute=execute,
            result_digests=lambda results: [item.canonical_sha256 for item in results],
        )
