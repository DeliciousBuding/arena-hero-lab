"""Atomic immutable-ledger tests for hierarchical evidence."""

from __future__ import annotations

from dataclasses import replace

import pytest

from arena_hero_research.hierarchical import ClusterObservation
from arena_hero_research.hierarchical_evidence import (
    HierarchicalAnalysisEvidence,
    HierarchicalEvidenceError,
    analyze_hierarchical_evidence,
    commit_hierarchical_analysis_evidence,
    load_hierarchical_analysis_evidence,
)
from arena_hero_research.storage import (
    FilesystemResearchLedgerStorage,
    FrozenResearchRecord,
    LedgerStorageError,
    ResearchLedgerTransaction,
    ResearchRecordKind,
)
from arena_hero_sim.serialization import content_sha256


def observations() -> tuple[ClusterObservation, ...]:
    values = (
        ("c1", 1.0, 2.2),
        ("c2", 2.0, 3.6),
        ("c3", 1.5, 2.8),
        ("c4", 2.5, 4.1),
    )
    return tuple(
        item
        for cluster_id, control, treatment in values
        for item in (
            ClusterObservation("score", cluster_id, "c0", "control", control),
            ClusterObservation("score", cluster_id, "t0", "treatment", treatment),
        )
    )


def test_unified_evidence_api_is_recomputable() -> None:
    data = observations()
    evidence = analyze_hierarchical_evidence(outcome_name="score", observations=data)

    assert evidence.verify()
    assert evidence.verify_recomputed(outcome_name="score", observations=data)
    assert not evidence.verify_recomputed(
        outcome_name="score",
        observations=tuple(replace(item, value=item.value + 1.0) for item in data),
    )


def test_fit_certificate_report_commit_atomically_and_restore(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    evidence = analyze_hierarchical_evidence(outcome_name="score", observations=observations())

    transaction = commit_hierarchical_analysis_evidence(
        storage,
        operation_id="hierarchical.score.v1",
        study_id="study.alpha",
        analysis_id="score.primary",
        evidence=evidence,
        expected_head_sha256=None,
    )
    state = storage.load()
    restored = load_hierarchical_analysis_evidence(
        storage,
        study_id="study.alpha",
        analysis_id="score.primary",
    )

    assert len(state.transactions) == 1
    assert state.transactions[0] == transaction
    assert len(transaction.record_sha256s) == 3
    assert {record.kind for record in state.records} == {
        ResearchRecordKind.HIERARCHICAL_FIT,
        ResearchRecordKind.SOLVER_CERTIFICATE,
        ResearchRecordKind.CROSS_VALIDATION_REPORT,
    }
    assert restored == evidence
    assert restored.verify_recomputed(outcome_name="score", observations=observations())


def test_atomic_commit_is_idempotent_for_same_operation(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    evidence = analyze_hierarchical_evidence(outcome_name="score", observations=observations())
    first = commit_hierarchical_analysis_evidence(
        storage,
        operation_id="hierarchical.score.v1",
        study_id="study.alpha",
        analysis_id="score.primary",
        evidence=evidence,
        expected_head_sha256=None,
    )
    second = commit_hierarchical_analysis_evidence(
        storage,
        operation_id="hierarchical.score.v1",
        study_id="study.alpha",
        analysis_id="score.primary",
        evidence=evidence,
        expected_head_sha256=first.canonical_sha256,
    )

    assert second == first
    assert len(storage.load().transactions) == 1


def test_restore_rejects_resigned_but_cross_linked_report_tamper(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    evidence = analyze_hierarchical_evidence(outcome_name="score", observations=observations())
    tampered = replace(
        evidence.report,
        certificate_sha256="0" * 64,
        canonical_sha256="0" * 64,
    )
    tampered = replace(
        tampered,
        canonical_sha256=content_sha256(tampered.payload()),
    )
    invalid_chain = (
        FrozenResearchRecord.create(
            study_id="study.alpha",
            kind=ResearchRecordKind.HIERARCHICAL_FIT,
            subject_id="score.primary",
            payload=evidence.fit.to_dict(),
        ),
        FrozenResearchRecord.create(
            study_id="study.alpha",
            kind=ResearchRecordKind.SOLVER_CERTIFICATE,
            subject_id="score.primary",
            payload=evidence.certificate.to_dict(),
        ),
        FrozenResearchRecord.create(
            study_id="study.alpha",
            kind=ResearchRecordKind.CROSS_VALIDATION_REPORT,
            subject_id="score.primary",
            payload=tampered.to_dict(),
        ),
    )
    storage.commit(
        operation_id="hierarchical.score.tampered",
        study_id="study.alpha",
        records=invalid_chain,
        expected_head_sha256=None,
    )

    with pytest.raises(HierarchicalEvidenceError, match="reference chain"):
        load_hierarchical_analysis_evidence(
            storage,
            study_id="study.alpha",
            analysis_id="score.primary",
        )


def test_unified_evidence_rejects_cross_link_mismatch() -> None:
    evidence = analyze_hierarchical_evidence(outcome_name="score", observations=observations())
    tampered = replace(
        evidence.report,
        fit_sha256="0" * 64,
        canonical_sha256="0" * 64,
    )
    tampered = replace(tampered, canonical_sha256=content_sha256(tampered.payload()))

    with pytest.raises(HierarchicalEvidenceError, match="reference chain"):
        HierarchicalAnalysisEvidence(
            fit=evidence.fit,
            certificate=evidence.certificate,
            report=tampered,
        )


def test_restore_rejects_cross_transaction_backfill(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    evidence = analyze_hierarchical_evidence(outcome_name="score", observations=observations())
    records = (
        FrozenResearchRecord.create(
            study_id="study.alpha",
            kind=ResearchRecordKind.HIERARCHICAL_FIT,
            subject_id="score.primary",
            payload=evidence.fit.to_dict(),
        ),
        FrozenResearchRecord.create(
            study_id="study.alpha",
            kind=ResearchRecordKind.SOLVER_CERTIFICATE,
            subject_id="score.primary",
            payload=evidence.certificate.to_dict(),
        ),
        FrozenResearchRecord.create(
            study_id="study.alpha",
            kind=ResearchRecordKind.CROSS_VALIDATION_REPORT,
            subject_id="score.primary",
            payload=evidence.report.to_dict(),
        ),
    )
    head = None
    for index, record in enumerate(records):
        transaction = storage.commit(
            operation_id=f"hierarchical.backfill.{index}",
            study_id="study.alpha",
            records=(record,),
            expected_head_sha256=head,
        )
        head = transaction.canonical_sha256

    with pytest.raises(HierarchicalEvidenceError, match="one atomic transaction"):
        load_hierarchical_analysis_evidence(
            storage, study_id="study.alpha", analysis_id="score.primary"
        )


def test_outer_ledger_loaders_reject_unknown_fields_and_type_coercion() -> None:
    evidence = analyze_hierarchical_evidence(outcome_name="score", observations=observations())
    record = FrozenResearchRecord.create(
        study_id="study.alpha",
        kind=ResearchRecordKind.HIERARCHICAL_FIT,
        subject_id="score.primary",
        payload=evidence.fit.to_dict(),
    )
    record_payload = record.to_dict()
    record_payload["extra"] = True
    with pytest.raises(LedgerStorageError, match="keys mismatch"):
        FrozenResearchRecord.from_dict(record_payload)
    record_payload = record.to_dict()
    record_payload["study_id"] = 7
    with pytest.raises(TypeError, match="JSON string"):
        FrozenResearchRecord.from_dict(record_payload)

    transaction = ResearchLedgerTransaction.create(
        sequence=0,
        operation_id="hierarchical.strict.v1",
        study_id="study.alpha",
        record_sha256s=(record.canonical_sha256,),
        previous_transaction_sha256=None,
    )
    transaction_payload = transaction.to_dict()
    transaction_payload["extra"] = True
    with pytest.raises(LedgerStorageError, match="keys mismatch"):
        ResearchLedgerTransaction.from_dict(transaction_payload)
    transaction_payload = transaction.to_dict()
    transaction_payload["sequence"] = True
    with pytest.raises(TypeError, match="integer"):
        ResearchLedgerTransaction.from_dict(transaction_payload)
