from __future__ import annotations

import multiprocessing
from dataclasses import replace
from multiprocessing.queues import Queue as ProcessQueue
from multiprocessing.synchronize import Event as ProcessEvent

import pytest

from arena_hero_research.analysis import analyze_preregistered_paired_outcomes
from arena_hero_research.assignment import AssignmentUnit, generate_assignments
from arena_hero_research.contracts import Preregistration, ResearchRunStatus
from arena_hero_research.durable import DurableResearchLedger, DurableResearchLedgerError
from arena_hero_research.execution import (
    ExecutionProvenance,
    PairedObservation,
    ReplicationResult,
    ReplicationResultStatus,
    build_replication_tasks,
)
from arena_hero_research.ledger import DataUseClaim, LedgerConflictError
from arena_hero_research.lifecycle import ResearchLifecycle, ResearchPhase
from arena_hero_research.provenance import (
    EnvironmentSnapshot,
    SoftwareBillOfMaterials,
    SoftwareComponent,
)
from arena_hero_research.results import ResearchRun, ResultBundle
from arena_hero_research.storage import (
    FilesystemResearchLedgerStorage,
    FrozenResearchRecord,
    ResearchRecordKind,
)
from arena_hero_sim.serialization import content_sha256

from .research_fixtures import make_preregistration


def _freeze_successor_worker(
    root: str,
    operation_id: str,
    lifecycle: ResearchLifecycle,
    preregistration: Preregistration,
    assignment,
    start: ProcessEvent,
    results: ProcessQueue,
) -> None:
    start.wait(15)
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(root))
    try:
        ledger.freeze_design(
            operation_id=operation_id,
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
        )
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    else:
        results.put(("ok", operation_id, ""))


def _environment() -> EnvironmentSnapshot:
    return EnvironmentSnapshot.create(
        python_implementation="CPython",
        python_version="3.12.10",
        operating_system="Linux",
        operating_system_release="6.8.0",
        machine="x86_64",
        executor="local-reference",
        metadata={"isolation": "test-fixture"},
    )


def _sbom() -> SoftwareBillOfMaterials:
    return SoftwareBillOfMaterials.create(
        generator="arena-hero-research",
        components=(
            SoftwareComponent(name="arena-hero-research", version="0.2.0"),
            SoftwareComponent(name="Python", version="3.12.10", component_type="runtime"),
        ),
    )


def _context(*, input_digest: str = "c" * 64):
    preregistration = make_preregistration()
    units = tuple(AssignmentUnit("scenario-a", seat, "block-a") for seat in range(4))
    assignment = generate_assignments(preregistration, units, treatment_factor="strategy")
    lifecycle = ResearchLifecycle.create(
        study_id="study-1",
        preregistration=preregistration,
        assignment=assignment,
    )
    exploratory = lifecycle.transition(
        ResearchPhase.EXPLORATORY,
        preregistration=preregistration,
        assignment=assignment,
    )
    confirmatory = exploratory.transition(
        ResearchPhase.CONFIRMATORY,
        preregistration=preregistration,
        assignment=assignment,
    )
    environment = _environment()
    sbom = _sbom()
    assert confirmatory.confirmatory_freeze_sha256 is not None
    provenance = ExecutionProvenance(
        confirmatory.confirmatory_freeze_sha256,
        "b" * 64,
        input_digest,
        environment.canonical_sha256,
        sbom.canonical_sha256,
    )
    tasks = build_replication_tasks(
        lifecycle=confirmatory,
        preregistration=preregistration,
        assignment=assignment,
        provenance_by_environment={"local-reference": provenance},
    )
    results = tuple(
        ReplicationResult.create(
            task=task,
            status=ReplicationResultStatus.COMPLETE,
            observations=(
                PairedObservation("score", f"pair-{task.replication_index}", 1.0, 1.0),
                PairedObservation("latency", f"pair-{task.replication_index}", 10.0, 10.0),
            ),
            metadata={"evidence": "null-effect-retained"},
        )
        for task in tasks
    )
    return preregistration, assignment, confirmatory, environment, sbom, tasks, results


def _phase_chain(context) -> tuple[ResearchLifecycle, ...]:
    preregistration, assignment, confirmatory, _, _, _, _ = context
    pilot = ResearchLifecycle.create(
        study_id=confirmatory.study_id,
        preregistration=preregistration,
        assignment=assignment,
    )
    exploratory = pilot.transition(
        ResearchPhase.EXPLORATORY,
        preregistration=preregistration,
        assignment=assignment,
    )
    assert (
        exploratory.transition(
            ResearchPhase.CONFIRMATORY,
            preregistration=preregistration,
            assignment=assignment,
        )
        == confirmatory
    )
    return pilot, exploratory, confirmatory


def _freeze(ledger: DurableResearchLedger, context) -> None:
    preregistration, assignment, _, _, _, _, _ = context
    for lifecycle in _phase_chain(context):
        ledger.freeze_design(
            operation_id=f"freeze-{lifecycle.phase.value}",
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
        )


def test_freeze_design_persists_all_immutable_scientific_state(tmp_path) -> None:
    context = _context()
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    ledger = DurableResearchLedger(storage)
    _freeze(ledger, context)

    restarted = DurableResearchLedger(FilesystemResearchLedgerStorage(storage.root))
    kinds = {record.kind for record in restarted.state().records}

    assert kinds == {
        ResearchRecordKind.PREREGISTRATION,
        ResearchRecordKind.ASSIGNMENT,
        ResearchRecordKind.ANALYSIS_PLAN,
        ResearchRecordKind.LIFECYCLE,
    }
    assert restarted.state().operation("freeze-confirmatory") is not None
    assert len(restarted.state().records_for(kind=ResearchRecordKind.LIFECYCLE)) == 3


def test_confirmatory_design_cannot_be_modified_after_freeze(tmp_path) -> None:
    context = _context()
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    _freeze(ledger, context)
    preregistration, _, _, _, _, _, _ = context
    changed_plan = replace(preregistration.design.analysis_plan, alpha=0.04)
    changed_design = replace(preregistration.design, analysis_plan=changed_plan)
    changed_preregistration = Preregistration.create(
        question=preregistration.question,
        hypotheses=preregistration.hypotheses,
        design=changed_design,
        registered_at=preregistration.registered_at,
    )
    units = tuple(AssignmentUnit("scenario-a", seat, "block-a") for seat in range(4))
    changed_assignment = generate_assignments(
        changed_preregistration, units, treatment_factor="strategy"
    )
    changed_lifecycle = (
        ResearchLifecycle.create(
            study_id="study-1",
            preregistration=changed_preregistration,
            assignment=changed_assignment,
        )
        .transition(
            ResearchPhase.EXPLORATORY,
            preregistration=changed_preregistration,
            assignment=changed_assignment,
        )
        .transition(
            ResearchPhase.CONFIRMATORY,
            preregistration=changed_preregistration,
            assignment=changed_assignment,
        )
    )

    with pytest.raises(DurableResearchLedgerError, match="supplied frozen design"):
        ledger.freeze_design(
            operation_id="posthoc-rewrite",
            lifecycle=changed_lifecycle,
            preregistration=changed_preregistration,
            assignment=changed_assignment,
        )


def test_replication_operation_is_durable_idempotent_and_replayable(tmp_path) -> None:
    context = _context()
    preregistration, assignment, lifecycle, environment, sbom, tasks, results = context
    root = tmp_path / "ledger"
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(root))
    _freeze(ledger, context)
    committed = ledger.record_replication_results(
        operation_id="replication-operation",
        lifecycle=lifecycle,
        preregistration=preregistration,
        assignment=assignment,
        tasks=tasks,
        results=results,
        environment=environment,
        sbom=sbom,
    )

    restarted = DurableResearchLedger(FilesystemResearchLedgerStorage(root))
    replay = restarted.replay_replication_results(operation_id="replication-operation", tasks=tasks)
    repeated = restarted.record_replication_results(
        operation_id="replication-operation",
        lifecycle=lifecycle,
        preregistration=preregistration,
        assignment=assignment,
        tasks=tasks,
        results=results,
        environment=environment,
        sbom=sbom,
    )

    assert replay == results
    assert repeated == committed
    assert len(restarted.state().transactions) == 4


def test_replication_replay_rejects_changed_task_plan(tmp_path) -> None:
    context = _context()
    preregistration, assignment, lifecycle, environment, sbom, tasks, results = context
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    _freeze(ledger, context)
    ledger.record_replication_results(
        operation_id="replication-operation",
        lifecycle=lifecycle,
        preregistration=preregistration,
        assignment=assignment,
        tasks=tasks,
        results=results,
        environment=environment,
        sbom=sbom,
    )

    with pytest.raises(LedgerConflictError, match="task plan"):
        ledger.replay_replication_results(operation_id="replication-operation", tasks=tasks[:-1])


def test_durable_pilot_claim_blocks_confirmatory_holdout_after_restart(tmp_path) -> None:
    context = _context()
    preregistration, assignment, lifecycle, environment, sbom, tasks, results = context
    root = tmp_path / "ledger"
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(root))
    pilot, exploratory, confirmatory = _phase_chain(context)
    ledger.freeze_design(
        operation_id="freeze-pilot",
        lifecycle=pilot,
        preregistration=preregistration,
        assignment=assignment,
    )
    ledger.claim_data_use(
        operation_id="pilot-operation",
        lifecycle=pilot,
        preregistration=preregistration,
        assignment=assignment,
        claims=(
            DataUseClaim(
                dataset_sha256=tasks[0].provenance.input_data_sha256,
                study_id="study-1",
                role="pilot",
                operation_id="pilot-operation",
            ),
        ),
    )

    ledger.freeze_design(
        operation_id="freeze-exploratory",
        lifecycle=exploratory,
        preregistration=preregistration,
        assignment=assignment,
    )
    ledger.freeze_design(
        operation_id="freeze-confirmatory",
        lifecycle=confirmatory,
        preregistration=preregistration,
        assignment=assignment,
    )

    restarted = DurableResearchLedger(FilesystemResearchLedgerStorage(root))
    with pytest.raises(LedgerConflictError, match="cannot be reused"):
        restarted.record_replication_results(
            operation_id="replication-operation",
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
            tasks=tasks,
            results=results,
            environment=environment,
            sbom=sbom,
        )


def test_second_operation_cannot_reuse_same_heldout_dataset(tmp_path) -> None:
    context = _context()
    preregistration, assignment, lifecycle, environment, sbom, tasks, results = context
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    _freeze(ledger, context)
    ledger.record_replication_results(
        operation_id="replication-operation",
        lifecycle=lifecycle,
        preregistration=preregistration,
        assignment=assignment,
        tasks=tasks,
        results=results,
        environment=environment,
        sbom=sbom,
    )

    with pytest.raises(LedgerConflictError, match="already belongs"):
        ledger.record_replication_results(
            operation_id="second-operation",
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
            tasks=tasks,
            results=results,
            environment=environment,
            sbom=sbom,
        )


def test_null_or_adverse_replication_evidence_cannot_be_replaced(tmp_path) -> None:
    context = _context()
    preregistration, assignment, lifecycle, environment, sbom, tasks, results = context
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    _freeze(ledger, context)
    ledger.record_replication_results(
        operation_id="replication-operation",
        lifecycle=lifecycle,
        preregistration=preregistration,
        assignment=assignment,
        tasks=tasks,
        results=results,
        environment=environment,
        sbom=sbom,
    )
    changed = (
        ReplicationResult.create(
            task=tasks[0],
            status=ReplicationResultStatus.COMPLETE,
            observations=(
                PairedObservation("score", "pair-0", 1.0, 99.0),
                PairedObservation("latency", "pair-0", 10.0, 1.0),
            ),
            metadata={"evidence": "replacement-attempt"},
        ),
        *results[1:],
    )

    with pytest.raises(LedgerConflictError, match="operation id"):
        ledger.record_replication_results(
            operation_id="replication-operation",
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
            tasks=tasks,
            results=changed,
            environment=environment,
            sbom=sbom,
        )
    assert not hasattr(ledger.storage, "delete")


def test_replication_rejects_coherently_rehashed_config_drift(tmp_path) -> None:
    context = _context()
    preregistration, assignment, lifecycle, environment, sbom, tasks, results = context
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    _freeze(ledger, context)
    changed_provenance = replace(tasks[0].provenance, frozen_config_sha256="f" * 64)
    provisional = replace(tasks[0], provenance=changed_provenance, canonical_sha256="0" * 64)
    changed_task = replace(provisional, canonical_sha256=content_sha256(provisional.payload()))
    changed_result = ReplicationResult.create(
        task=changed_task,
        status=ReplicationResultStatus.COMPLETE,
        observations=results[0].observations,
        metadata=results[0].metadata,
    )

    with pytest.raises(DurableResearchLedgerError, match="frozen-config mismatch"):
        ledger.record_replication_results(
            operation_id="replication-operation",
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
            tasks=(changed_task, *tasks[1:]),
            results=(changed_result, *results[1:]),
            environment=environment,
            sbom=sbom,
        )


def test_service_revalidates_durable_data_use_policy_on_load(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    pilot = DataUseClaim("e" * 64, "study-1", "pilot", "pilot-operation")
    heldout = DataUseClaim("e" * 64, "study-2", "confirmatory", "heldout-operation")
    pilot_payload = pilot.to_dict()
    heldout_payload = heldout.to_dict()
    pilot_record = FrozenResearchRecord.create(
        study_id=pilot.study_id,
        kind=ResearchRecordKind.DATA_USE_CLAIM,
        subject_id=content_sha256(pilot_payload),
        payload=pilot_payload,
    )
    heldout_record = FrozenResearchRecord.create(
        study_id=heldout.study_id,
        kind=ResearchRecordKind.DATA_USE_CLAIM,
        subject_id=content_sha256(heldout_payload),
        payload=heldout_payload,
    )
    first = storage.commit(
        operation_id=pilot.operation_id,
        study_id=pilot.study_id,
        records=(pilot_record,),
        expected_head_sha256=None,
    )
    storage.commit(
        operation_id=heldout.operation_id,
        study_id=heldout.study_id,
        records=(heldout_record,),
        expected_head_sha256=first.canonical_sha256,
    )

    with pytest.raises(DurableResearchLedgerError, match="before pilot lifecycle"):
        DurableResearchLedger(storage).state()


def test_replication_rejects_mismatched_environment_or_sbom(tmp_path) -> None:
    context = _context()
    preregistration, assignment, lifecycle, environment, sbom, tasks, results = context
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    _freeze(ledger, context)
    changed_environment = replace(environment, canonical_sha256="f" * 64)

    with pytest.raises(DurableResearchLedgerError, match="provenance artifacts"):
        ledger.record_replication_results(
            operation_id="replication-operation",
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
            tasks=tasks,
            results=results,
            environment=changed_environment,
            sbom=sbom,
        )


def test_analysis_bundle_is_retained_with_public_provenance(tmp_path) -> None:
    context = _context(input_digest="d" * 64)
    preregistration, assignment, lifecycle, environment, sbom, _, _ = context
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    _freeze(ledger, context)
    observations = {
        "score": ((1.0, 2.0, 3.0, 4.0), (1.0, 2.0, 3.0, 4.0)),
        "latency": ((10.0, 11.0, 12.0, 13.0), (10.0, 11.0, 12.0, 13.0)),
    }
    estimates, quality = analyze_preregistered_paired_outcomes(
        preregistration,
        observations,
        bootstrap_seed=7,
    )
    run = ResearchRun(
        run_id="run-null-evidence",
        preregistration=preregistration,
        frozen_config_sha256=lifecycle.confirmatory_freeze_sha256 or "",
        source_build_sha256="b" * 64,
        input_data_sha256="d" * 64,
        environment_sha256=environment.canonical_sha256,
        sbom_sha256=sbom.canonical_sha256,
        status=ResearchRunStatus.COMPLETE,
    )
    bundle = ResultBundle.create(
        run=run,
        estimates=estimates,
        data_quality=quality,
        provenance={"source": "content-addressed-fixture"},
        environment={"class": "local-reference"},
        publishable=True,
    )

    ledger.record_analysis_bundle(
        operation_id="analysis-operation",
        lifecycle=lifecycle,
        preregistration=preregistration,
        assignment=assignment,
        bundle=bundle,
        environment=environment,
        sbom=sbom,
    )

    records = ledger.state().records_for(kind=ResearchRecordKind.RESULT_BUNDLE)
    assert len(records) == 1
    assert records[0].payload_sha256 == bundle.bundle_sha256()

    changed_bundle = replace(bundle, provenance={"source": "selective-rewrite-attempt"})
    with pytest.raises(LedgerConflictError, match="operation id"):
        ledger.record_analysis_bundle(
            operation_id="analysis-operation",
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
            bundle=changed_bundle,
            environment=environment,
            sbom=sbom,
        )


def test_evidence_requires_design_and_current_phase_to_be_frozen(tmp_path) -> None:
    context = _context()
    preregistration, assignment, lifecycle, environment, sbom, tasks, results = context
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))

    with pytest.raises(DurableResearchLedgerError, match="predecessor chain"):
        ledger.record_replication_results(
            operation_id="replication-operation",
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
            tasks=tasks,
            results=results,
            environment=environment,
            sbom=sbom,
        )


def test_empty_ledger_rejects_confirmatory_as_first_phase(tmp_path) -> None:
    context = _context()
    preregistration, assignment, confirmatory, _, _, _, _ = context
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))

    with pytest.raises(DurableResearchLedgerError, match="must begin at pilot"):
        ledger.freeze_design(
            operation_id="late-preregistration",
            lifecycle=confirmatory,
            preregistration=preregistration,
            assignment=assignment,
        )


def test_successor_rejects_skip_and_backfill_after_evidence(tmp_path) -> None:
    context = _context()
    preregistration, assignment, _, _, _, _, _ = context
    pilot, exploratory, confirmatory = _phase_chain(context)
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    ledger.freeze_design(
        operation_id="freeze-pilot",
        lifecycle=pilot,
        preregistration=preregistration,
        assignment=assignment,
    )

    with pytest.raises(DurableResearchLedgerError, match="exactly one phase"):
        ledger.freeze_design(
            operation_id="skip-to-confirmatory",
            lifecycle=confirmatory,
            preregistration=preregistration,
            assignment=assignment,
        )

    ledger.claim_data_use(
        operation_id="pilot-evidence",
        lifecycle=pilot,
        preregistration=preregistration,
        assignment=assignment,
        claims=(DataUseClaim("f" * 64, "study-1", "pilot", "pilot-evidence"),),
    )
    ledger.freeze_design(
        operation_id="freeze-exploratory",
        lifecycle=exploratory,
        preregistration=preregistration,
        assignment=assignment,
    )
    with pytest.raises(DurableResearchLedgerError, match="exactly one phase"):
        ledger.freeze_design(
            operation_id="backfill-pilot",
            lifecycle=pilot,
            preregistration=preregistration,
            assignment=assignment,
        )


def test_structural_state_rejects_malformed_history_record(tmp_path) -> None:
    context = _context()
    preregistration, assignment, _, _, _, _, _ = context
    pilot, _, confirmatory = _phase_chain(context)
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    ledger = DurableResearchLedger(storage)
    first = ledger.freeze_design(
        operation_id="freeze-pilot",
        lifecycle=pilot,
        preregistration=preregistration,
        assignment=assignment,
    )
    payload = confirmatory.to_dict()
    payload["history"] = ["pilot", "confirmatory"]
    malformed = FrozenResearchRecord.create(
        study_id="study-1",
        kind=ResearchRecordKind.LIFECYCLE,
        subject_id="phase-confirmatory",
        payload=payload,
    )
    storage.commit(
        operation_id="malformed-successor",
        study_id="study-1",
        records=(malformed,),
        expected_head_sha256=first.canonical_sha256,
    )

    with pytest.raises(DurableResearchLedgerError, match="invalid durable lifecycle"):
        ledger.state()


def test_structural_state_rejects_noncanonical_confirmatory_transition(tmp_path) -> None:
    context = _context()
    preregistration, assignment, confirmatory, _, _, _, _ = context
    pilot, exploratory, _ = _phase_chain(context)
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    ledger = DurableResearchLedger(storage)
    for lifecycle in (pilot, exploratory):
        ledger.freeze_design(
            operation_id=f"freeze-{lifecycle.phase.value}",
            lifecycle=lifecycle,
            preregistration=preregistration,
            assignment=assignment,
        )

    payload = confirmatory.payload()
    payload["confirmatory_freeze_sha256"] = "f" * 64
    malformed = replace(
        confirmatory,
        confirmatory_freeze_sha256="f" * 64,
        canonical_sha256=content_sha256(payload),
    )
    assert malformed.verify()
    record = FrozenResearchRecord.create(
        study_id="study-1",
        kind=ResearchRecordKind.LIFECYCLE,
        subject_id="phase-confirmatory",
        payload=malformed.to_dict(),
    )
    state = ledger.state()
    storage.commit(
        operation_id="malformed-confirmatory",
        study_id="study-1",
        records=(record,),
        expected_head_sha256=state.transactions[-1].canonical_sha256,
    )

    with pytest.raises(DurableResearchLedgerError, match="exact predecessor transition"):
        ledger.state()


def test_claim_requires_chain_and_role_phase_compatibility(tmp_path) -> None:
    context = _context()
    preregistration, assignment, confirmatory, _, _, _, _ = context
    pilot = _phase_chain(context)[0]
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    confirmatory_claim = DataUseClaim("f" * 64, "study-1", "confirmatory", "confirmatory-claim")
    with pytest.raises(DurableResearchLedgerError, match="predecessor chain"):
        ledger.claim_data_use(
            operation_id="confirmatory-claim",
            lifecycle=confirmatory,
            preregistration=preregistration,
            assignment=assignment,
            claims=(confirmatory_claim,),
        )

    ledger.freeze_design(
        operation_id="freeze-pilot",
        lifecycle=pilot,
        preregistration=preregistration,
        assignment=assignment,
    )
    with pytest.raises(DurableResearchLedgerError, match="durable lifecycle phase"):
        ledger.claim_data_use(
            operation_id="confirmatory-claim",
            lifecycle=pilot,
            preregistration=preregistration,
            assignment=assignment,
            claims=(confirmatory_claim,),
        )


@pytest.mark.parametrize(
    ("claim_study_id", "claim_operation_id"),
    (
        ("study-2", "injected-claim"),
        ("study-1", "different-operation"),
    ),
)
def test_structural_state_rejects_data_use_claim_binding_drift(
    tmp_path,
    claim_study_id: str,
    claim_operation_id: str,
) -> None:
    context = _context()
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    ledger = DurableResearchLedger(storage)
    _freeze(ledger, context)
    claim = DataUseClaim(
        dataset_sha256="f" * 64,
        study_id=claim_study_id,
        role="confirmatory",
        operation_id=claim_operation_id,
    )
    payload = claim.to_dict()
    record = FrozenResearchRecord.create(
        study_id="study-1",
        kind=ResearchRecordKind.DATA_USE_CLAIM,
        subject_id=content_sha256(payload),
        payload=payload,
    )
    state = ledger.state()
    storage.commit(
        operation_id="injected-claim",
        study_id="study-1",
        records=(record,),
        expected_head_sha256=state.transactions[-1].canonical_sha256,
    )

    with pytest.raises(DurableResearchLedgerError, match="lifecycle and transaction"):
        ledger.state()


def test_replication_evidence_rejects_incomplete_predecessor_chain(tmp_path) -> None:
    context = _context()
    preregistration, assignment, confirmatory, environment, sbom, tasks, results = context
    pilot = _phase_chain(context)[0]
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(tmp_path / "ledger"))
    ledger.freeze_design(
        operation_id="freeze-pilot",
        lifecycle=pilot,
        preregistration=preregistration,
        assignment=assignment,
    )

    with pytest.raises(DurableResearchLedgerError, match="predecessor chain"):
        ledger.record_replication_results(
            operation_id="premature-evidence",
            lifecycle=confirmatory,
            preregistration=preregistration,
            assignment=assignment,
            tasks=tasks,
            results=results,
            environment=environment,
            sbom=sbom,
        )


def test_two_processes_racing_same_successor_allow_only_one_commit(tmp_path) -> None:
    context = _context()
    preregistration, assignment, _, _, _, _, _ = context
    pilot, exploratory, _ = _phase_chain(context)
    root = tmp_path / "ledger"
    ledger = DurableResearchLedger(FilesystemResearchLedgerStorage(root))
    ledger.freeze_design(
        operation_id="freeze-pilot",
        lifecycle=pilot,
        preregistration=preregistration,
        assignment=assignment,
    )

    process_context = multiprocessing.get_context("spawn")
    start = process_context.Event()
    results = process_context.Queue()
    processes = [
        process_context.Process(
            target=_freeze_successor_worker,
            args=(
                str(root),
                f"successor-{index}",
                exploratory,
                preregistration,
                assignment,
                start,
                results,
            ),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("successor worker did not terminate")
        assert process.exitcode == 0

    outcomes = [results.get(timeout=5) for _ in processes]
    assert [item[0] for item in outcomes].count("ok") == 1
    assert [item[0] for item in outcomes].count("error") == 1
    assert len(ledger.state().records_for(kind=ResearchRecordKind.LIFECYCLE)) == 2
    assert ledger.state().records_for(kind=ResearchRecordKind.LIFECYCLE)[-1].subject_id == (
        "phase-exploratory"
    )
