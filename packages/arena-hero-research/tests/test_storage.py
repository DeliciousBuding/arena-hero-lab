from __future__ import annotations

import json

import pytest

from arena_hero_research.ledger import LedgerConflictError
from arena_hero_research.storage import (
    FilesystemResearchLedgerStorage,
    FrozenResearchRecord,
    LedgerCorruptionError,
    ResearchRecordKind,
    TornLedgerTailError,
)
from arena_hero_sim.serialization import canonical_json_bytes


def _record(*, value: int = 1, subject_id: str = "analysis-plan") -> FrozenResearchRecord:
    return FrozenResearchRecord.create(
        study_id="study-1",
        kind=ResearchRecordKind.ANALYSIS_PLAN,
        subject_id=subject_id,
        payload={"schema_version": "test.analysis.v1", "value": value},
    )


def test_filesystem_storage_commits_content_addressed_hash_chain(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    record = _record()

    transaction = storage.commit(
        operation_id="freeze-plan",
        study_id="study-1",
        records=(record,),
        expected_head_sha256=None,
    )
    state = storage.load()

    assert transaction.verify()
    assert state.transactions == (transaction,)
    assert state.records == (record,)
    assert storage.object_path(record.canonical_sha256).is_file()
    assert storage.journal_path.read_bytes().endswith(b"\n")
    assert list(storage.root.rglob("*.tmp")) == []
    assert not hasattr(storage, "delete")


def test_operation_id_is_idempotent_across_adapter_restart(tmp_path) -> None:
    root = tmp_path / "ledger"
    first = FilesystemResearchLedgerStorage(root)
    record = _record()
    committed = first.commit(
        operation_id="freeze-plan", study_id="study-1", records=(record,), expected_head_sha256=None
    )

    restarted = FilesystemResearchLedgerStorage(root)
    replayed = restarted.commit(
        operation_id="freeze-plan", study_id="study-1", records=(record,), expected_head_sha256=None
    )

    assert replayed == committed
    assert len(restarted.load().transactions) == 1


def test_conflicting_operation_or_immutable_record_fails_closed(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    storage.commit(
        operation_id="freeze-plan",
        study_id="study-1",
        records=(_record(),),
        expected_head_sha256=None,
    )

    with pytest.raises(LedgerConflictError, match="operation id"):
        storage.commit(
            operation_id="freeze-plan",
            study_id="study-1",
            records=(_record(subject_id="other-plan"),),
            expected_head_sha256=None,
        )
    with pytest.raises(LedgerConflictError, match="immutable"):
        storage.commit(
            operation_id="rewrite-plan",
            study_id="study-1",
            records=(_record(value=2),),
            expected_head_sha256=storage.load().transactions[-1].canonical_sha256,
        )


def test_stale_policy_head_precondition_rejects_racing_commit(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    storage.commit(
        operation_id="first-operation",
        study_id="study-1",
        records=(_record(subject_id="first-plan"),),
        expected_head_sha256=None,
    )

    with pytest.raises(LedgerConflictError, match="head changed"):
        storage.commit(
            operation_id="stale-operation",
            study_id="study-1",
            records=(_record(subject_id="second-plan"),),
            expected_head_sha256=None,
        )


def test_torn_tail_requires_explicit_quarantined_recovery(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    record = _record()
    committed = storage.commit(
        operation_id="freeze-plan", study_id="study-1", records=(record,), expected_head_sha256=None
    )
    with storage.journal_path.open("ab") as stream:
        stream.write(b'{"schema_version":"arena.research.ledger-transaction.v1"')

    with pytest.raises(TornLedgerTailError) as caught:
        storage.load()
    assert caught.value.torn_bytes > 0

    recovery = storage.recover_torn_tail()

    assert recovery.repaired
    assert recovery.discarded_bytes == caught.value.torn_bytes
    assert recovery.quarantine_relative_path is not None
    assert (storage.root / recovery.quarantine_relative_path).is_file()
    assert storage.load().transactions == (committed,)


def test_complete_json_without_commit_newline_is_conservatively_discarded(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    record = _record()
    storage.commit(
        operation_id="freeze-plan",
        study_id="study-1",
        records=(record,),
        expected_head_sha256=None,
    )
    storage.journal_path.write_bytes(storage.journal_path.read_bytes()[:-1])

    with pytest.raises(TornLedgerTailError):
        storage.load()
    recovery = storage.recover_torn_tail()

    assert recovery.repaired
    assert storage.load().transactions == ()
    assert storage.object_path(record.canonical_sha256).is_file()


def test_recovery_refuses_canonical_or_midstream_corruption(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    storage.commit(
        operation_id="freeze-plan",
        study_id="study-1",
        records=(_record(),),
        expected_head_sha256=None,
    )
    original = storage.journal_path.read_bytes()
    decoded = json.loads(original)
    noncanonical = json.dumps(decoded, indent=2).encode() + b"\n"
    storage.journal_path.write_bytes(noncanonical)

    with pytest.raises(LedgerCorruptionError, match=r"valid UTF-8 JSON|canonically"):
        storage.load()
    with pytest.raises(LedgerCorruptionError, match=r"valid UTF-8 JSON|canonically"):
        storage.recover_torn_tail()


def test_missing_or_tampered_content_object_is_detected(tmp_path) -> None:
    storage = FilesystemResearchLedgerStorage(tmp_path / "ledger")
    record = _record()
    storage.commit(
        operation_id="freeze-plan", study_id="study-1", records=(record,), expected_head_sha256=None
    )
    path = storage.object_path(record.canonical_sha256)
    value = json.loads(path.read_bytes())
    value["payload"]["value"] = 9
    path.write_bytes(canonical_json_bytes(value))

    with pytest.raises(LedgerCorruptionError, match="digest mismatch"):
        storage.load()


def test_record_payload_recursively_rejects_sensitive_fields() -> None:
    with pytest.raises(ValueError, match="sensitive key"):
        FrozenResearchRecord.create(
            study_id="study-1",
            kind=ResearchRecordKind.RESULT_BUNDLE,
            subject_id="run-1",
            payload={"evidence": {"nested": [{"private-key": "forbidden"}]}},
        )
