"""Domain service binding immutable research state to a durable ledger port."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from arena_hero_research.assignment import AssignmentManifest
from arena_hero_research.contracts import Preregistration, preregistration_payload
from arena_hero_research.execution import DataRole, ReplicationResult, ReplicationTask
from arena_hero_research.ledger import DataUseClaim, DataUseLedger, LedgerConflictError
from arena_hero_research.lifecycle import ResearchLifecycle, ResearchPhase
from arena_hero_research.provenance import (
    EnvironmentProvenance,
    EnvironmentSnapshot,
    SoftwareBillOfMaterials,
)
from arena_hero_research.results import ResultBundle
from arena_hero_research.storage import (
    FrozenResearchRecord,
    ResearchLedgerState,
    ResearchLedgerStorage,
    ResearchLedgerTransaction,
    ResearchRecordKind,
    TornTailRecovery,
)
from arena_hero_sim.serialization import JsonValue, content_sha256


class DurableResearchLedgerError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DurableResearchLedger:
    """Application service; persistence remains behind ``ResearchLedgerStorage``."""

    storage: ResearchLedgerStorage

    def state(self) -> ResearchLedgerState:
        state = self.storage.load()
        self._data_use_ledger(state)
        return state

    def recover_torn_tail(self) -> TornTailRecovery:
        return self.storage.recover_torn_tail()

    def freeze_design(
        self,
        *,
        operation_id: str,
        lifecycle: ResearchLifecycle,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
    ) -> ResearchLedgerTransaction:
        """Freeze preregistration, assignment, analysis plan, and one lifecycle phase."""

        self._validate_design(lifecycle, preregistration, assignment)
        state = self.state()
        return self.storage.commit(
            operation_id=operation_id,
            study_id=lifecycle.study_id,
            records=self._design_records(lifecycle, preregistration, assignment),
            expected_head_sha256=self._head(state),
        )

    def claim_data_use(
        self,
        *,
        operation_id: str,
        study_id: str,
        claims: Sequence[DataUseClaim],
    ) -> ResearchLedgerTransaction:
        """Persist pilot, exploratory, confirmatory, or replication data-use claims."""

        normalized = tuple(dict.fromkeys(claims))
        if not normalized:
            raise DurableResearchLedgerError("data-use operation requires at least one claim")
        if any(
            claim.operation_id != operation_id or claim.study_id != study_id for claim in normalized
        ):
            raise DurableResearchLedgerError(
                "data-use claims must match the transaction study and operation"
            )
        state = self.state()
        self._validate_data_use_claims(state, normalized)
        records = tuple(self._data_use_record(claim) for claim in normalized)
        return self.storage.commit(
            operation_id=operation_id,
            study_id=study_id,
            records=records,
            expected_head_sha256=self._head(state),
        )

    def record_replication_results(
        self,
        *,
        operation_id: str,
        lifecycle: ResearchLifecycle,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
        tasks: Sequence[ReplicationTask],
        results: Sequence[ReplicationResult],
        environment: EnvironmentSnapshot,
        sbom: SoftwareBillOfMaterials,
    ) -> ResearchLedgerTransaction:
        """Atomically retain held-out claims, tasks, results, and public provenance."""

        self._validate_design(lifecycle, preregistration, assignment)
        if lifecycle.phase not in {ResearchPhase.CONFIRMATORY, ResearchPhase.REPLICATION}:
            raise DurableResearchLedgerError(
                "replication evidence requires confirmatory or replication lifecycle phase"
            )
        normalized_tasks = tuple(tasks)
        normalized_results = tuple(results)
        self._validate_replication_bindings(
            lifecycle=lifecycle,
            tasks=normalized_tasks,
            results=normalized_results,
            environment=environment,
            sbom=sbom,
        )
        provenance = EnvironmentProvenance.create(
            environment=environment,
            sbom=sbom,
            metadata={"scope": "replication-execution"},
        )

        state = self.state()
        self._require_frozen_design(
            state=state,
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
        )
        claims = self._replication_claims(operation_id, normalized_tasks)
        self._validate_data_use_claims(state, claims)
        records = [
            *self._provenance_records(lifecycle.study_id, environment, sbom, provenance),
            *(self._data_use_record(claim) for claim in claims),
        ]
        for task, result in zip(normalized_tasks, normalized_results, strict=True):
            records.append(
                self._record(
                    study_id=lifecycle.study_id,
                    kind=ResearchRecordKind.REPLICATION_TASK,
                    subject_id=task.task_id,
                    payload=task.to_dict(),
                )
            )
            records.append(
                self._record(
                    study_id=lifecycle.study_id,
                    kind=ResearchRecordKind.REPLICATION_RESULT,
                    subject_id=task.task_id,
                    payload=result.to_dict(),
                )
            )
        return self.storage.commit(
            operation_id=operation_id,
            study_id=lifecycle.study_id,
            records=tuple(records),
            expected_head_sha256=self._head(state),
        )

    def replay_replication_results(
        self,
        *,
        operation_id: str,
        tasks: Sequence[ReplicationTask],
    ) -> tuple[ReplicationResult, ...] | None:
        """Return a verified durable replay before an executor repeats completed work."""

        state = self.state()
        transaction = state.operation(operation_id)
        if transaction is None:
            return None
        records_by_sha = {record.canonical_sha256: record for record in state.records}
        records = tuple(records_by_sha[digest] for digest in transaction.record_sha256s)
        task_records = tuple(
            record for record in records if record.kind is ResearchRecordKind.REPLICATION_TASK
        )
        expected_task_records = tuple(
            sorted(
                (
                    self._record(
                        study_id=task.study_id,
                        kind=ResearchRecordKind.REPLICATION_TASK,
                        subject_id=task.task_id,
                        payload=task.to_dict(),
                    )
                    for task in tasks
                ),
                key=lambda item: (item.kind.value, item.subject_id, item.canonical_sha256),
            )
        )
        if tuple(sorted(item.canonical_sha256 for item in task_records)) != tuple(
            sorted(item.canonical_sha256 for item in expected_task_records)
        ):
            raise LedgerConflictError(
                "operation id already exists with a different replication task plan"
            )

        results: list[ReplicationResult] = []
        for record in records:
            if record.kind is not ResearchRecordKind.REPLICATION_RESULT:
                continue
            result = ReplicationResult.from_dict(record.payload)
            if not result.verify():
                raise DurableResearchLedgerError("durable replication result failed verification")
            results.append(result)
        if len(results) != len(expected_task_records):
            raise DurableResearchLedgerError(
                "durable replication operation does not contain one result per task"
            )
        return tuple(sorted(results, key=lambda item: item.task.replication_index))

    def record_analysis_bundle(
        self,
        *,
        operation_id: str,
        lifecycle: ResearchLifecycle,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
        bundle: ResultBundle,
        environment: EnvironmentSnapshot,
        sbom: SoftwareBillOfMaterials,
    ) -> ResearchLedgerTransaction:
        """Retain complete, partial, failed, null, and adverse analysis evidence immutably."""

        self._validate_design(lifecycle, preregistration, assignment)
        if lifecycle.phase not in {
            ResearchPhase.CONFIRMATORY,
            ResearchPhase.REPLICATION,
            ResearchPhase.COMPLETE,
        }:
            raise DurableResearchLedgerError("analysis evidence requires a frozen lifecycle")
        if bundle.run.preregistration.canonical_sha256 != preregistration.canonical_sha256:
            raise DurableResearchLedgerError("analysis bundle preregistration mismatch")
        if bundle.run.frozen_config_sha256 != lifecycle.confirmatory_freeze_sha256:
            raise DurableResearchLedgerError("analysis bundle frozen-config mismatch")
        if (
            bundle.run.environment_sha256 != environment.canonical_sha256
            or bundle.run.sbom_sha256 != sbom.canonical_sha256
        ):
            raise DurableResearchLedgerError("analysis bundle environment or SBOM mismatch")
        if not environment.verify() or not sbom.verify():
            raise DurableResearchLedgerError("analysis provenance artifacts failed verification")

        state = self.state()
        self._require_frozen_design(
            state=state,
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
        )
        claim = DataUseClaim(
            dataset_sha256=bundle.run.input_data_sha256,
            study_id=lifecycle.study_id,
            role=DataRole.CONFIRMATORY.value,
            operation_id=operation_id,
        )
        self._validate_data_use_claims(state, (claim,))
        provenance = EnvironmentProvenance.create(
            environment=environment,
            sbom=sbom,
            metadata={"scope": "analysis-bundle"},
        )
        run_subject = f"run-{content_sha256(bundle.run.run_id)[:32]}"
        records = (
            *self._provenance_records(lifecycle.study_id, environment, sbom, provenance),
            self._data_use_record(claim),
            self._record(
                study_id=lifecycle.study_id,
                kind=ResearchRecordKind.RESULT_BUNDLE,
                subject_id=run_subject,
                payload=bundle.to_dict(),
            ),
        )
        return self.storage.commit(
            operation_id=operation_id,
            study_id=lifecycle.study_id,
            records=records,
            expected_head_sha256=self._head(state),
        )

    @staticmethod
    def _head(state: ResearchLedgerState) -> str | None:
        return state.transactions[-1].canonical_sha256 if state.transactions else None

    @staticmethod
    def _validate_design(
        lifecycle: ResearchLifecycle,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
    ) -> None:
        if not lifecycle.verify_against(
            preregistration=preregistration,
            assignment=assignment,
        ):
            raise DurableResearchLedgerError(
                "research lifecycle does not verify against frozen design artifacts"
            )

    @classmethod
    def _design_records(
        cls,
        lifecycle: ResearchLifecycle,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
    ) -> tuple[FrozenResearchRecord, ...]:
        preregistration_body = preregistration_payload(
            preregistration.question,
            preregistration.hypotheses,
            preregistration.design,
            preregistration.registered_at,
        )
        preregistration_body["canonical_sha256"] = preregistration.canonical_sha256
        analysis_plan: dict[str, JsonValue] = {
            **preregistration.design.analysis_plan.to_dict(),
            "canonical_sha256": preregistration.design.analysis_plan.canonical_sha256(),
        }
        return (
            cls._record(
                study_id=lifecycle.study_id,
                kind=ResearchRecordKind.PREREGISTRATION,
                subject_id="preregistration",
                payload=preregistration_body,
            ),
            cls._record(
                study_id=lifecycle.study_id,
                kind=ResearchRecordKind.ASSIGNMENT,
                subject_id="assignment",
                payload=assignment.to_dict(),
            ),
            cls._record(
                study_id=lifecycle.study_id,
                kind=ResearchRecordKind.ANALYSIS_PLAN,
                subject_id="analysis-plan",
                payload=analysis_plan,
            ),
            cls._record(
                study_id=lifecycle.study_id,
                kind=ResearchRecordKind.LIFECYCLE,
                subject_id=f"phase-{lifecycle.phase.value}",
                payload=lifecycle.to_dict(),
            ),
        )

    @classmethod
    def _require_frozen_design(
        cls,
        *,
        state: ResearchLedgerState,
        lifecycle: ResearchLifecycle,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
    ) -> None:
        expected = cls._design_records(lifecycle, preregistration, assignment)
        actual = {
            (record.kind, record.subject_id): record.canonical_sha256
            for record in state.records_for(study_id=lifecycle.study_id)
        }
        for record in expected:
            digest = actual.get((record.kind, record.subject_id))
            if digest is None:
                raise DurableResearchLedgerError(
                    "design and current lifecycle phase must be frozen before evidence"
                )
            if digest != record.canonical_sha256:
                raise DurableResearchLedgerError("durable frozen design does not match evidence")

    @classmethod
    def _provenance_records(
        cls,
        study_id: str,
        environment: EnvironmentSnapshot,
        sbom: SoftwareBillOfMaterials,
        provenance: EnvironmentProvenance,
    ) -> tuple[FrozenResearchRecord, ...]:
        if not provenance.verify(environment, sbom):
            raise DurableResearchLedgerError("environment provenance binding failed verification")
        return (
            cls._record(
                study_id=study_id,
                kind=ResearchRecordKind.ENVIRONMENT_SNAPSHOT,
                subject_id=environment.canonical_sha256,
                payload=environment.to_dict(),
            ),
            cls._record(
                study_id=study_id,
                kind=ResearchRecordKind.SBOM,
                subject_id=sbom.canonical_sha256,
                payload=sbom.to_dict(),
            ),
            cls._record(
                study_id=study_id,
                kind=ResearchRecordKind.ENVIRONMENT_PROVENANCE,
                subject_id=provenance.canonical_sha256,
                payload=provenance.to_dict(),
            ),
        )

    @staticmethod
    def _validate_replication_bindings(
        *,
        lifecycle: ResearchLifecycle,
        tasks: tuple[ReplicationTask, ...],
        results: tuple[ReplicationResult, ...],
        environment: EnvironmentSnapshot,
        sbom: SoftwareBillOfMaterials,
    ) -> None:
        if not tasks or len(tasks) != len(results):
            raise DurableResearchLedgerError(
                "replication operation requires one result for every non-empty task"
            )
        if len({task.task_id for task in tasks}) != len(tasks):
            raise DurableResearchLedgerError("replication task ids must be unique")
        if not environment.verify() or not sbom.verify():
            raise DurableResearchLedgerError("replication provenance artifacts failed verification")
        for task, result in zip(tasks, results, strict=True):
            if not task.verify() or not result.verify():
                raise DurableResearchLedgerError(
                    "replication task or result digest verification failed"
                )
            if result.task != task:
                raise DurableResearchLedgerError("replication result is not bound to its task")
            if task.study_id != lifecycle.study_id:
                raise DurableResearchLedgerError("replication task study mismatch")
            if (
                task.preregistration_sha256 != lifecycle.preregistration_sha256
                or task.analysis_plan_sha256 != lifecycle.analysis_plan_sha256
                or task.assignment_sha256 != lifecycle.assignment_sha256
            ):
                raise DurableResearchLedgerError("replication task frozen-design mismatch")
            if task.provenance.frozen_config_sha256 != lifecycle.confirmatory_freeze_sha256:
                raise DurableResearchLedgerError("replication task frozen-config mismatch")
            if task.environment != environment.executor:
                raise DurableResearchLedgerError("replication task environment class mismatch")
            if (
                task.provenance.environment_sha256 != environment.canonical_sha256
                or task.provenance.sbom_sha256 != sbom.canonical_sha256
            ):
                raise DurableResearchLedgerError("replication task environment or SBOM mismatch")

    @staticmethod
    def _replication_claims(
        operation_id: str,
        tasks: tuple[ReplicationTask, ...],
    ) -> tuple[DataUseClaim, ...]:
        claims = {
            (
                task.provenance.input_data_sha256,
                task.study_id,
                task.data_role.value,
                operation_id,
            ): DataUseClaim(
                dataset_sha256=task.provenance.input_data_sha256,
                study_id=task.study_id,
                role=task.data_role.value,
                operation_id=operation_id,
            )
            for task in tasks
        }
        return tuple(claims[key] for key in sorted(claims))

    @staticmethod
    def _data_use_ledger(state: ResearchLedgerState) -> DataUseLedger:
        ledger = DataUseLedger()
        for record in state.records_for(kind=ResearchRecordKind.DATA_USE_CLAIM):
            ledger.claim(DataUseClaim.from_dict(record.payload))
        return ledger

    @classmethod
    def _validate_data_use_claims(
        cls,
        state: ResearchLedgerState,
        claims: Sequence[DataUseClaim],
    ) -> None:
        ledger = cls._data_use_ledger(state)
        for claim in claims:
            ledger.claim(claim)

    @classmethod
    def _data_use_record(cls, claim: DataUseClaim) -> FrozenResearchRecord:
        payload = claim.to_dict()
        return cls._record(
            study_id=claim.study_id,
            kind=ResearchRecordKind.DATA_USE_CLAIM,
            subject_id=content_sha256(payload),
            payload=payload,
        )

    @staticmethod
    def _record(
        *,
        study_id: str,
        kind: ResearchRecordKind,
        subject_id: str,
        payload: Mapping[str, JsonValue],
    ) -> FrozenResearchRecord:
        return FrozenResearchRecord.create(
            study_id=study_id,
            kind=kind,
            subject_id=subject_id,
            payload=payload,
        )
