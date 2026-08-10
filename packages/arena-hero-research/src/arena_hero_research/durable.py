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
from arena_hero_research.validation import require_identifier, require_sequence
from arena_hero_sim.serialization import JsonValue, content_sha256


class DurableResearchLedgerError(ValueError):
    pass


_PHASE_SEQUENCE = (
    ResearchPhase.PILOT,
    ResearchPhase.EXPLORATORY,
    ResearchPhase.CONFIRMATORY,
    ResearchPhase.REPLICATION,
    ResearchPhase.COMPLETE,
)
_PHASE_INDEX = {phase: index for index, phase in enumerate(_PHASE_SEQUENCE)}
_ROLE_BY_PHASE = {
    ResearchPhase.PILOT: DataRole.PILOT.value,
    ResearchPhase.EXPLORATORY: DataRole.EXPLORATORY.value,
    ResearchPhase.CONFIRMATORY: DataRole.CONFIRMATORY.value,
    ResearchPhase.REPLICATION: DataRole.REPLICATION.value,
}
_BASE_DESIGN_KINDS = frozenset(
    {
        ResearchRecordKind.PREREGISTRATION,
        ResearchRecordKind.ASSIGNMENT,
        ResearchRecordKind.ANALYSIS_PLAN,
    }
)
_PHASE_DEPENDENT_KINDS = frozenset(
    {
        ResearchRecordKind.DATA_USE_CLAIM,
        ResearchRecordKind.REPLICATION_TASK,
        ResearchRecordKind.REPLICATION_RESULT,
        ResearchRecordKind.RESULT_BUNDLE,
    }
)


@dataclass(frozen=True, slots=True)
class DurableResearchLedger:
    """Application service; persistence remains behind ``ResearchLedgerStorage``."""

    storage: ResearchLedgerStorage

    def state(self) -> ResearchLedgerState:
        state = self.storage.load()
        self._validate_structural_chronology(state)
        self._data_use_ledger(state)
        return state

    def recover_torn_tail(self) -> TornTailRecovery:
        recovery = self.storage.recover_torn_tail()
        self.state()
        return recovery

    def freeze_design(
        self,
        *,
        operation_id: str,
        lifecycle: ResearchLifecycle,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
    ) -> ResearchLedgerTransaction:
        """Append exactly one legal lifecycle successor with its frozen design."""

        operation_id = require_identifier(operation_id, "operation_id")
        self._validate_design(lifecycle, preregistration, assignment)
        state = self.state()
        records = self._design_records(lifecycle, preregistration, assignment)
        if state.operation(operation_id) is not None:
            return self._commit_records(
                state=state,
                operation_id=operation_id,
                study_id=lifecycle.study_id,
                records=records,
            )

        chain = self._lifecycle_chain(
            state,
            lifecycle.study_id,
            preregistration=preregistration,
            assignment=assignment,
        )
        if not chain:
            if state.records_for(study_id=lifecycle.study_id):
                raise DurableResearchLedgerError(
                    "new study cannot backfill lifecycle after durable records exist"
                )
            if lifecycle.phase is not ResearchPhase.PILOT or lifecycle.history != (
                ResearchPhase.PILOT,
            ):
                raise DurableResearchLedgerError("new durable study must begin at pilot")
        else:
            predecessor = chain[-1]
            predecessor_index = _PHASE_INDEX[predecessor.phase]
            if predecessor_index + 1 >= len(_PHASE_SEQUENCE):
                raise DurableResearchLedgerError("complete lifecycle has no successor")
            expected_phase = _PHASE_SEQUENCE[predecessor_index + 1]
            if lifecycle.phase is not expected_phase:
                raise DurableResearchLedgerError(
                    "durable lifecycle successor must advance exactly one phase"
                )
            expected = predecessor.transition(
                expected_phase,
                preregistration=preregistration,
                assignment=assignment,
            )
            if lifecycle != expected or lifecycle.history != (*predecessor.history, expected_phase):
                raise DurableResearchLedgerError(
                    "durable lifecycle history must equal predecessor plus successor"
                )

        return self._commit_records(
            state=state,
            operation_id=operation_id,
            study_id=lifecycle.study_id,
            records=records,
        )

    def claim_data_use(
        self,
        *,
        operation_id: str,
        lifecycle: ResearchLifecycle,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
        claims: Sequence[DataUseClaim],
    ) -> ResearchLedgerTransaction:
        """Persist claims only for the exact current durable lifecycle phase."""

        operation_id = require_identifier(operation_id, "operation_id")
        self._validate_design(lifecycle, preregistration, assignment)
        normalized = tuple(dict.fromkeys(claims))
        if not normalized:
            raise DurableResearchLedgerError("data-use operation requires at least one claim")
        if lifecycle.phase not in _ROLE_BY_PHASE:
            raise DurableResearchLedgerError("complete lifecycle cannot accept new data-use claims")
        expected_role = _ROLE_BY_PHASE[lifecycle.phase]
        if any(
            claim.operation_id != operation_id
            or claim.study_id != lifecycle.study_id
            or claim.role != expected_role
            for claim in normalized
        ):
            raise DurableResearchLedgerError(
                "data-use claim must match operation, study, and durable lifecycle phase"
            )
        if (
            lifecycle.phase in {ResearchPhase.CONFIRMATORY, ResearchPhase.REPLICATION}
            and lifecycle.confirmatory_freeze_sha256 is None
        ):
            raise DurableResearchLedgerError("held-out claim requires confirmatory freeze")

        state = self.state()
        self._require_complete_chain(
            state=state,
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
        )
        self._validate_data_use_claims(state, normalized)
        records = tuple(self._data_use_record(claim) for claim in normalized)
        return self._commit_records(
            state=state,
            operation_id=operation_id,
            study_id=lifecycle.study_id,
            records=records,
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

        operation_id = require_identifier(operation_id, "operation_id")
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
        self._require_complete_chain(
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
        return self._commit_records(
            state=state,
            operation_id=operation_id,
            study_id=lifecycle.study_id,
            records=tuple(records),
        )

    def replay_replication_results(
        self,
        *,
        operation_id: str,
        tasks: Sequence[ReplicationTask],
    ) -> tuple[ReplicationResult, ...] | None:
        """Return a verified durable replay before an executor repeats completed work."""

        operation_id = require_identifier(operation_id, "operation_id")
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

        operation_id = require_identifier(operation_id, "operation_id")
        self._validate_design(lifecycle, preregistration, assignment)
        if lifecycle.phase not in {
            ResearchPhase.CONFIRMATORY,
            ResearchPhase.REPLICATION,
        }:
            raise DurableResearchLedgerError(
                "analysis evidence requires confirmatory or replication lifecycle phase"
            )
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
        self._require_complete_chain(
            state=state,
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
        )
        claim = DataUseClaim(
            dataset_sha256=bundle.run.input_data_sha256,
            study_id=lifecycle.study_id,
            role=_ROLE_BY_PHASE[lifecycle.phase],
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
        return self._commit_records(
            state=state,
            operation_id=operation_id,
            study_id=lifecycle.study_id,
            records=records,
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

    @staticmethod
    def _record_transaction_index(
        state: ResearchLedgerState,
    ) -> dict[str, tuple[ResearchLedgerTransaction, ...]]:
        references: dict[str, list[ResearchLedgerTransaction]] = {}
        for transaction in state.transactions:
            for digest in transaction.record_sha256s:
                references.setdefault(digest, []).append(transaction)
        return {digest: tuple(items) for digest, items in references.items()}

    @classmethod
    def _record_sequence_index(cls, state: ResearchLedgerState) -> dict[str, int]:
        return {
            digest: references[0].sequence
            for digest, references in cls._record_transaction_index(state).items()
        }

    @classmethod
    def _lifecycle_entries(
        cls,
        state: ResearchLedgerState,
        study_id: str,
        *,
        preregistration: Preregistration | None = None,
        assignment: AssignmentManifest | None = None,
    ) -> tuple[tuple[int, ResearchLifecycle], ...]:
        transaction_index = cls._record_transaction_index(state)
        sequence_index = {
            digest: references[0].sequence for digest, references in transaction_index.items()
        }
        lifecycle_records = state.records_for(study_id=study_id, kind=ResearchRecordKind.LIFECYCLE)
        ordered_records = tuple(
            sorted(
                lifecycle_records,
                key=lambda record: sequence_index[record.canonical_sha256],
            )
        )
        if len(ordered_records) > len(_PHASE_SEQUENCE):
            raise DurableResearchLedgerError("durable lifecycle has too many phase records")

        entries: list[tuple[int, ResearchLifecycle]] = []
        for position, record in enumerate(ordered_records):
            references = transaction_index[record.canonical_sha256]
            if len(references) != 1:
                raise DurableResearchLedgerError(
                    "each durable lifecycle phase must belong to exactly one transaction"
                )
            lifecycle_sequence = references[0].sequence
            if entries and lifecycle_sequence <= entries[-1][0]:
                raise DurableResearchLedgerError(
                    "durable lifecycle successor must follow its predecessor transaction"
                )
            try:
                lifecycle = ResearchLifecycle.from_dict(record.payload)
            except (KeyError, TypeError, ValueError) as exc:
                raise DurableResearchLedgerError("invalid durable lifecycle record") from exc
            if not lifecycle.verify():
                raise DurableResearchLedgerError("durable lifecycle record digest mismatch")
            if lifecycle.study_id != study_id:
                raise DurableResearchLedgerError("durable lifecycle study mismatch")
            if record.subject_id != f"phase-{lifecycle.phase.value}":
                raise DurableResearchLedgerError("durable lifecycle subject mismatch")
            expected_phase = _PHASE_SEQUENCE[position]
            expected_history = _PHASE_SEQUENCE[: position + 1]
            if lifecycle.phase is not expected_phase or lifecycle.history != expected_history:
                raise DurableResearchLedgerError(
                    "durable lifecycle must be an exact prefix beginning at pilot"
                )
            if entries:
                predecessor = entries[-1][1]
                if (
                    lifecycle.preregistration_sha256 != predecessor.preregistration_sha256
                    or lifecycle.analysis_plan_sha256 != predecessor.analysis_plan_sha256
                    or lifecycle.assignment_sha256 != predecessor.assignment_sha256
                ):
                    raise DurableResearchLedgerError(
                        "durable lifecycle successor changed frozen design"
                    )
                if (
                    position > _PHASE_INDEX[ResearchPhase.CONFIRMATORY]
                    and lifecycle.confirmatory_freeze_sha256
                    != predecessor.confirmatory_freeze_sha256
                ):
                    raise DurableResearchLedgerError(
                        "durable lifecycle successor changed confirmatory freeze"
                    )
            if (
                preregistration is not None
                and assignment is not None
                and not lifecycle.verify_against(
                    preregistration=preregistration, assignment=assignment
                )
            ):
                raise DurableResearchLedgerError(
                    "durable lifecycle does not match supplied frozen design"
                )
            entries.append((lifecycle_sequence, lifecycle))
        return tuple(entries)

    @classmethod
    def _lifecycle_chain(
        cls,
        state: ResearchLedgerState,
        study_id: str,
        *,
        preregistration: Preregistration | None = None,
        assignment: AssignmentManifest | None = None,
    ) -> tuple[ResearchLifecycle, ...]:
        return tuple(
            lifecycle
            for _, lifecycle in cls._lifecycle_entries(
                state,
                study_id,
                preregistration=preregistration,
                assignment=assignment,
            )
        )

    @staticmethod
    def _declared_payload_sha256(record: FrozenResearchRecord, label: str) -> str:
        payload = dict(record.payload)
        declared = payload.pop("canonical_sha256", None)
        if not isinstance(declared, str) or content_sha256(payload) != declared:
            raise DurableResearchLedgerError(
                f"durable {label} declared identity does not match its content"
            )
        return declared

    @staticmethod
    def _expected_confirmatory_freeze_sha256(
        base_records: Mapping[ResearchRecordKind, FrozenResearchRecord],
        *,
        preregistration_sha256: str,
        analysis_plan_sha256: str,
        assignment_sha256: str,
    ) -> str:
        preregistration = base_records[ResearchRecordKind.PREREGISTRATION].payload
        try:
            hypotheses = require_sequence(preregistration["hypotheses"], "hypotheses")
            design = preregistration["design"]
            if not isinstance(design, Mapping):
                raise TypeError("design must be a mapping")
            outcomes = require_sequence(design["outcomes"], "outcomes")
            replication_plan = design["replication_plan"]
            if not isinstance(replication_plan, Mapping):
                raise TypeError("replication_plan must be a mapping")
            seeds = require_sequence(replication_plan["seeds"], "seeds")
            hypothesis_ids: list[JsonValue] = []
            for item in hypotheses:
                if not isinstance(item, Mapping) or not isinstance(item.get("hypothesis_id"), str):
                    raise TypeError("hypothesis must contain a string hypothesis_id")
                hypothesis_ids.append(item["hypothesis_id"])
            outcome_names: list[JsonValue] = []
            for item in outcomes:
                if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
                    raise TypeError("outcome must contain a string name")
                outcome_names.append(item["name"])
            seed_values: list[JsonValue] = []
            for item in seeds:
                if isinstance(item, bool) or not isinstance(item, int):
                    raise TypeError("replication seeds must be integers")
                seed_values.append(item)
        except (KeyError, TypeError, ValueError) as exc:
            raise DurableResearchLedgerError(
                "durable preregistration cannot reconstruct confirmatory freeze"
            ) from exc
        payload: dict[str, JsonValue] = {
            "schema_version": "arena.research.confirmatory-freeze.v1",
            "preregistration_sha256": preregistration_sha256,
            "analysis_plan_sha256": analysis_plan_sha256,
            "assignment_sha256": assignment_sha256,
            "hypothesis_ids": hypothesis_ids,
            "outcomes": outcome_names,
            "seeds": seed_values,
        }
        return content_sha256(payload)

    @classmethod
    def _validate_structural_chronology(cls, state: ResearchLedgerState) -> None:
        transaction_index = cls._record_transaction_index(state)
        sequence_index = {
            digest: references[0].sequence for digest, references in transaction_index.items()
        }
        study_ids = sorted({record.study_id for record in state.records})
        expected_base_subjects = {
            ResearchRecordKind.PREREGISTRATION: "preregistration",
            ResearchRecordKind.ASSIGNMENT: "assignment",
            ResearchRecordKind.ANALYSIS_PLAN: "analysis-plan",
        }
        for study_id in study_ids:
            study_records = state.records_for(study_id=study_id)
            entries = cls._lifecycle_entries(state, study_id)
            if not entries:
                raise DurableResearchLedgerError(
                    "durable study records cannot exist before pilot lifecycle"
                )
            pilot_sequence, pilot = entries[0]
            base_records = tuple(
                record for record in study_records if record.kind in _BASE_DESIGN_KINDS
            )
            if len(base_records) != len(expected_base_subjects):
                raise DurableResearchLedgerError(
                    "pilot transaction must contain exactly one frozen design record per kind"
                )
            for record in base_records:
                if expected_base_subjects.get(record.kind) != record.subject_id:
                    raise DurableResearchLedgerError("frozen design record subject mismatch")
                if sequence_index[record.canonical_sha256] != pilot_sequence:
                    raise DurableResearchLedgerError(
                        "frozen design cannot be backfilled after pilot"
                    )
            base_by_kind = {record.kind: record for record in base_records}
            canonical_by_kind = {
                kind: cls._declared_payload_sha256(record, kind.value)
                for kind, record in base_by_kind.items()
            }
            preregistration_payload = base_by_kind[ResearchRecordKind.PREREGISTRATION].payload
            assignment_payload = base_by_kind[ResearchRecordKind.ASSIGNMENT].payload
            design = preregistration_payload.get("design")
            if not isinstance(design, Mapping):
                raise DurableResearchLedgerError("durable preregistration design must be a mapping")
            analysis_plan = design.get("analysis_plan")
            if not isinstance(analysis_plan, Mapping):
                raise DurableResearchLedgerError(
                    "durable preregistration analysis plan must be a mapping"
                )
            if (
                canonical_by_kind[ResearchRecordKind.PREREGISTRATION]
                != pilot.preregistration_sha256
                or canonical_by_kind[ResearchRecordKind.ASSIGNMENT] != pilot.assignment_sha256
                or canonical_by_kind[ResearchRecordKind.ANALYSIS_PLAN] != pilot.analysis_plan_sha256
                or content_sha256(dict(analysis_plan)) != pilot.analysis_plan_sha256
                or assignment_payload.get("preregistration_sha256") != pilot.preregistration_sha256
                or assignment_payload.get("analysis_plan_sha256") != pilot.analysis_plan_sha256
            ):
                raise DurableResearchLedgerError(
                    "pilot lifecycle does not match durable frozen design records"
                )
            expected_freeze = cls._expected_confirmatory_freeze_sha256(
                base_by_kind,
                preregistration_sha256=pilot.preregistration_sha256,
                analysis_plan_sha256=pilot.analysis_plan_sha256,
                assignment_sha256=pilot.assignment_sha256,
            )
            for _, lifecycle in entries:
                expected = (
                    expected_freeze
                    if _PHASE_INDEX[lifecycle.phase] >= _PHASE_INDEX[ResearchPhase.CONFIRMATORY]
                    else None
                )
                if lifecycle.confirmatory_freeze_sha256 != expected:
                    raise DurableResearchLedgerError(
                        "durable lifecycle is not the exact predecessor transition"
                    )

            for record in study_records:
                if record.kind in _BASE_DESIGN_KINDS or record.kind is ResearchRecordKind.LIFECYCLE:
                    continue
                references = transaction_index[record.canonical_sha256]
                record_sequence = references[0].sequence
                active = cls._active_lifecycle(entries, record_sequence)
                if active is None:
                    raise DurableResearchLedgerError(
                        "evidence or provenance cannot precede durable pilot"
                    )
                if record.kind in _PHASE_DEPENDENT_KINDS:
                    if len(references) != 1:
                        raise DurableResearchLedgerError(
                            "phase-dependent record must belong to exactly one durable operation"
                        )
                    cls._validate_phase_dependent_record(record, active, references[0])

    @staticmethod
    def _active_lifecycle(
        entries: tuple[tuple[int, ResearchLifecycle], ...],
        record_sequence: int,
    ) -> ResearchLifecycle | None:
        active: ResearchLifecycle | None = None
        for lifecycle_sequence, lifecycle in entries:
            if lifecycle_sequence >= record_sequence:
                break
            active = lifecycle
        return active

    @staticmethod
    def _validate_phase_dependent_record(
        record: FrozenResearchRecord,
        lifecycle: ResearchLifecycle,
        transaction: ResearchLedgerTransaction,
    ) -> None:
        if record.kind is ResearchRecordKind.DATA_USE_CLAIM:
            claim = DataUseClaim.from_dict(record.payload)
            expected_role = _ROLE_BY_PHASE.get(lifecycle.phase)
            if expected_role is None or claim.role != expected_role:
                raise DurableResearchLedgerError(
                    "durable data-use claim is incompatible with active lifecycle phase"
                )
            if (
                claim.study_id != lifecycle.study_id
                or claim.study_id != record.study_id
                or claim.study_id != transaction.study_id
                or claim.operation_id != transaction.operation_id
            ):
                raise DurableResearchLedgerError(
                    "durable data-use claim must match its lifecycle and transaction"
                )
            if record.subject_id != content_sha256(claim.to_dict()):
                raise DurableResearchLedgerError(
                    "durable data-use claim subject is not content-addressed"
                )
            if (
                lifecycle.phase in {ResearchPhase.CONFIRMATORY, ResearchPhase.REPLICATION}
                and lifecycle.confirmatory_freeze_sha256 is None
            ):
                raise DurableResearchLedgerError(
                    "held-out durable data-use claim requires confirmatory freeze"
                )
            return

        if record.kind in {
            ResearchRecordKind.REPLICATION_TASK,
            ResearchRecordKind.REPLICATION_RESULT,
        }:
            if record.kind is ResearchRecordKind.REPLICATION_TASK:
                task = ReplicationTask.from_dict(record.payload)
            else:
                result = ReplicationResult.from_dict(record.payload)
                if not result.verify():
                    raise DurableResearchLedgerError(
                        "durable replication result failed verification"
                    )
                task = result.task
            expected_role = _ROLE_BY_PHASE.get(lifecycle.phase)
            if (
                lifecycle.phase
                not in {
                    ResearchPhase.CONFIRMATORY,
                    ResearchPhase.REPLICATION,
                }
                or task.data_role.value != expected_role
                or task.study_id != lifecycle.study_id
                or task.preregistration_sha256 != lifecycle.preregistration_sha256
                or task.analysis_plan_sha256 != lifecycle.analysis_plan_sha256
                or task.assignment_sha256 != lifecycle.assignment_sha256
                or task.provenance.frozen_config_sha256 != lifecycle.confirmatory_freeze_sha256
            ):
                raise DurableResearchLedgerError(
                    "durable replication evidence is incompatible with lifecycle chain"
                )
            return

        if record.kind is ResearchRecordKind.RESULT_BUNDLE:
            if lifecycle.phase not in {
                ResearchPhase.CONFIRMATORY,
                ResearchPhase.REPLICATION,
                ResearchPhase.COMPLETE,
            }:
                raise DurableResearchLedgerError(
                    "analysis evidence is incompatible with active lifecycle phase"
                )
            run = record.payload.get("run")
            if not isinstance(run, Mapping):
                raise DurableResearchLedgerError("durable result bundle run is invalid")
            if (
                str(run.get("preregistration_sha256", "")) != lifecycle.preregistration_sha256
                or str(run.get("frozen_config_sha256", "")) != lifecycle.confirmatory_freeze_sha256
            ):
                raise DurableResearchLedgerError(
                    "durable analysis evidence is incompatible with lifecycle chain"
                )

    @classmethod
    def _require_complete_chain(
        cls,
        *,
        state: ResearchLedgerState,
        lifecycle: ResearchLifecycle,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
    ) -> None:
        chain = cls._lifecycle_chain(
            state,
            lifecycle.study_id,
            preregistration=preregistration,
            assignment=assignment,
        )
        if not chain or chain[-1] != lifecycle:
            raise DurableResearchLedgerError(
                "evidence requires the complete durable lifecycle predecessor chain"
            )

    def _commit_records(
        self,
        *,
        state: ResearchLedgerState,
        operation_id: str,
        study_id: str,
        records: Sequence[FrozenResearchRecord],
    ) -> ResearchLedgerTransaction:
        transaction = self.storage.commit(
            operation_id=operation_id,
            study_id=study_id,
            records=records,
            expected_head_sha256=self._head(state),
        )
        verified = self.state()
        if verified.operation(operation_id) != transaction:
            raise DurableResearchLedgerError(
                "durable operation failed chronology read-after-write verification"
            )
        return transaction

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
