"""Durable append-only storage ports for immutable research records."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from arena_hero_research.ledger import LedgerConflictError
from arena_hero_research.validation import (
    freeze_public_metadata,
    require_identifier,
    require_json_mapping,
    require_sha256,
)
from arena_hero_sim.serialization import JsonValue, canonical_json_bytes, content_sha256


class LedgerStorageError(RuntimeError):
    pass


class LedgerCorruptionError(LedgerStorageError):
    pass


class TornLedgerTailError(LedgerCorruptionError):
    """The journal ends without a commit newline and requires explicit recovery."""

    def __init__(self, *, valid_prefix_bytes: int, torn_bytes: int) -> None:
        super().__init__(
            "research ledger has an uncommitted torn tail; explicit recovery is required"
        )
        self.valid_prefix_bytes = valid_prefix_bytes
        self.torn_bytes = torn_bytes


def _require_exact_keys(value: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise LedgerStorageError(
            f"{label} keys mismatch; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _strict_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a JSON string")
    return value


class ResearchRecordKind(StrEnum):
    PREREGISTRATION = "preregistration"
    ASSIGNMENT = "assignment"
    ANALYSIS_PLAN = "analysis-plan"
    LIFECYCLE = "lifecycle"
    ENVIRONMENT_SNAPSHOT = "environment-snapshot"
    SBOM = "sbom"
    ENVIRONMENT_PROVENANCE = "environment-provenance"
    DATA_USE_CLAIM = "data-use-claim"
    REPLICATION_TASK = "replication-task"
    REPLICATION_RESULT = "replication-result"
    RESULT_BUNDLE = "result-bundle"
    HIERARCHICAL_FIT = "hierarchical-fit"
    SOLVER_CERTIFICATE = "solver-certificate"
    CROSS_VALIDATION_REPORT = "cross-validation-report"


@dataclass(frozen=True, slots=True)
class FrozenResearchRecord:
    """One immutable public artifact stored under its canonical SHA-256 identity."""

    schema_version: str
    study_id: str
    kind: ResearchRecordKind
    subject_id: str
    payload_sha256: str
    payload: Mapping[str, JsonValue]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.frozen-record.v1":
            raise LedgerStorageError("unsupported frozen research record schema")
        object.__setattr__(self, "study_id", require_identifier(self.study_id, "study_id"))
        object.__setattr__(self, "subject_id", require_identifier(self.subject_id, "subject_id"))
        object.__setattr__(
            self, "payload_sha256", require_sha256(self.payload_sha256, "payload_sha256")
        )
        object.__setattr__(self, "payload", freeze_public_metadata(self.payload, "record payload"))
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def envelope(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "kind": self.kind.value,
            "subject_id": self.subject_id,
            "payload_sha256": self.payload_sha256,
            "payload": dict(self.payload),
        }

    def verify(self) -> bool:
        return (
            content_sha256(dict(self.payload)) == self.payload_sha256
            and content_sha256(self.envelope()) == self.canonical_sha256
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.envelope(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def create(
        cls,
        *,
        study_id: str,
        kind: ResearchRecordKind,
        subject_id: str,
        payload: Mapping[str, JsonValue],
    ) -> FrozenResearchRecord:
        normalized_study = require_identifier(study_id, "study_id")
        normalized_subject = require_identifier(subject_id, "subject_id")
        frozen_payload = freeze_public_metadata(payload, "record payload")
        payload_sha256 = content_sha256(dict(frozen_payload))
        envelope: dict[str, JsonValue] = {
            "schema_version": "arena.research.frozen-record.v1",
            "study_id": normalized_study,
            "kind": kind.value,
            "subject_id": normalized_subject,
            "payload_sha256": payload_sha256,
            "payload": dict(frozen_payload),
        }
        return cls(
            schema_version="arena.research.frozen-record.v1",
            study_id=normalized_study,
            kind=kind,
            subject_id=normalized_subject,
            payload_sha256=payload_sha256,
            payload=frozen_payload,
            canonical_sha256=content_sha256(envelope),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> FrozenResearchRecord:
        _require_exact_keys(
            value,
            frozenset(
                {
                    "schema_version",
                    "study_id",
                    "kind",
                    "subject_id",
                    "payload_sha256",
                    "payload",
                    "canonical_sha256",
                }
            ),
            "frozen research record",
        )
        restored = cls(
            schema_version=_strict_string(value["schema_version"], "schema_version"),
            study_id=_strict_string(value["study_id"], "study_id"),
            kind=ResearchRecordKind(_strict_string(value["kind"], "kind")),
            subject_id=_strict_string(value["subject_id"], "subject_id"),
            payload_sha256=_strict_string(value["payload_sha256"], "payload_sha256"),
            payload=require_json_mapping(value["payload"], "record payload"),
            canonical_sha256=_strict_string(value["canonical_sha256"], "canonical_sha256"),
        )
        if restored.to_dict() != dict(value):
            raise LedgerStorageError("frozen research record is not canonical schema v1")
        return restored


@dataclass(frozen=True, slots=True)
class ResearchLedgerTransaction:
    """One atomic operation in the append-only hash-chained journal."""

    schema_version: str
    sequence: int
    operation_id: str
    study_id: str
    record_sha256s: tuple[str, ...]
    previous_transaction_sha256: str | None
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.ledger-transaction.v1":
            raise LedgerStorageError("unsupported research ledger transaction schema")
        if self.sequence < 0:
            raise LedgerStorageError("transaction sequence must be non-negative")
        object.__setattr__(
            self, "operation_id", require_identifier(self.operation_id, "operation_id")
        )
        object.__setattr__(self, "study_id", require_identifier(self.study_id, "study_id"))
        record_sha256s = tuple(
            require_sha256(item, "record_sha256") for item in self.record_sha256s
        )
        if not record_sha256s or len(record_sha256s) != len(set(record_sha256s)):
            raise LedgerStorageError("transaction record digests must be non-empty and unique")
        object.__setattr__(self, "record_sha256s", record_sha256s)
        if self.previous_transaction_sha256 is not None:
            object.__setattr__(
                self,
                "previous_transaction_sha256",
                require_sha256(
                    self.previous_transaction_sha256,
                    "previous_transaction_sha256",
                ),
            )
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "operation_id": self.operation_id,
            "study_id": self.study_id,
            "record_sha256s": list(self.record_sha256s),
            "previous_transaction_sha256": self.previous_transaction_sha256,
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        operation_id: str,
        study_id: str,
        record_sha256s: tuple[str, ...],
        previous_transaction_sha256: str | None,
    ) -> ResearchLedgerTransaction:
        normalized_operation = require_identifier(operation_id, "operation_id")
        normalized_study = require_identifier(study_id, "study_id")
        payload: dict[str, JsonValue] = {
            "schema_version": "arena.research.ledger-transaction.v1",
            "sequence": sequence,
            "operation_id": normalized_operation,
            "study_id": normalized_study,
            "record_sha256s": list(record_sha256s),
            "previous_transaction_sha256": previous_transaction_sha256,
        }
        return cls(
            schema_version="arena.research.ledger-transaction.v1",
            sequence=sequence,
            operation_id=normalized_operation,
            study_id=normalized_study,
            record_sha256s=record_sha256s,
            previous_transaction_sha256=previous_transaction_sha256,
            canonical_sha256=content_sha256(payload),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ResearchLedgerTransaction:
        _require_exact_keys(
            value,
            frozenset(
                {
                    "schema_version",
                    "sequence",
                    "operation_id",
                    "study_id",
                    "record_sha256s",
                    "previous_transaction_sha256",
                    "canonical_sha256",
                }
            ),
            "research ledger transaction",
        )
        digests = value["record_sha256s"]
        if not isinstance(digests, list):
            raise TypeError("record_sha256s must be a JSON list")
        if any(not isinstance(item, str) for item in digests):
            raise TypeError("record_sha256s entries must be JSON strings")
        previous = value["previous_transaction_sha256"]
        if previous is not None and not isinstance(previous, str):
            raise TypeError("previous_transaction_sha256 must be a JSON string or null")
        sequence = value["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise TypeError("transaction sequence must be an integer")
        restored = cls(
            schema_version=_strict_string(value["schema_version"], "schema_version"),
            sequence=sequence,
            operation_id=_strict_string(value["operation_id"], "operation_id"),
            study_id=_strict_string(value["study_id"], "study_id"),
            record_sha256s=tuple(digests),
            previous_transaction_sha256=previous,
            canonical_sha256=_strict_string(value["canonical_sha256"], "canonical_sha256"),
        )
        if restored.to_dict() != dict(value):
            raise LedgerStorageError("research ledger transaction is not canonical schema v1")
        return restored


@dataclass(frozen=True, slots=True)
class ResearchLedgerState:
    transactions: tuple[ResearchLedgerTransaction, ...]
    records: tuple[FrozenResearchRecord, ...]

    def operation(self, operation_id: str) -> ResearchLedgerTransaction | None:
        normalized = require_identifier(operation_id, "operation_id")
        return next(
            (item for item in self.transactions if item.operation_id == normalized),
            None,
        )

    def records_for(
        self,
        *,
        study_id: str | None = None,
        kind: ResearchRecordKind | None = None,
    ) -> tuple[FrozenResearchRecord, ...]:
        return tuple(
            record
            for record in self.records
            if (study_id is None or record.study_id == study_id)
            and (kind is None or record.kind is kind)
        )


@dataclass(frozen=True, slots=True)
class TornTailRecovery:
    repaired: bool
    discarded_bytes: int
    discarded_sha256: str | None
    quarantine_relative_path: str | None


@runtime_checkable
class ResearchLedgerStorage(Protocol):
    """Port implemented by append-only research ledger storage adapters."""

    def load(self) -> ResearchLedgerState: ...

    def commit(
        self,
        *,
        operation_id: str,
        study_id: str,
        records: Sequence[FrozenResearchRecord],
        expected_head_sha256: str | None,
    ) -> ResearchLedgerTransaction: ...

    def recover_torn_tail(self) -> TornTailRecovery: ...


class FilesystemResearchLedgerStorage:
    """Reference local adapter using immutable objects plus a JSONL hash chain."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    @property
    def journal_path(self) -> Path:
        return self.root / "journal" / "transactions.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.root / "journal" / "writer.lock"

    def object_path(self, canonical_sha256: str) -> Path:
        digest = require_sha256(canonical_sha256, "canonical_sha256")
        return self.root / "objects" / "sha256" / digest[:2] / f"{digest}.json"

    def load(self) -> ResearchLedgerState:
        with self._writer_lock():
            return self._load_unlocked()

    def commit(
        self,
        *,
        operation_id: str,
        study_id: str,
        records: Sequence[FrozenResearchRecord],
        expected_head_sha256: str | None,
    ) -> ResearchLedgerTransaction:
        normalized_operation = require_identifier(operation_id, "operation_id")
        normalized_study = require_identifier(study_id, "study_id")
        proposed = tuple(
            sorted(
                records,
                key=lambda item: (item.kind.value, item.subject_id, item.canonical_sha256),
            )
        )
        if not proposed:
            raise LedgerConflictError("a durable operation must contain at least one record")
        if any(record.study_id != normalized_study for record in proposed):
            raise LedgerConflictError("all records must belong to the transaction study")
        if any(not record.verify() for record in proposed):
            raise LedgerConflictError("all records must pass content verification")
        keys = tuple((item.study_id, item.kind, item.subject_id) for item in proposed)
        if len(keys) != len(set(keys)):
            raise LedgerConflictError("operation contains duplicate immutable record keys")
        digests = tuple(item.canonical_sha256 for item in proposed)

        with self._writer_lock():
            state = self._load_unlocked()
            existing_operation = state.operation(normalized_operation)
            if existing_operation is not None:
                if (
                    existing_operation.study_id != normalized_study
                    or existing_operation.record_sha256s != digests
                ):
                    raise LedgerConflictError(
                        "operation id already exists with conflicting durable records"
                    )
                return existing_operation

            actual_head = state.transactions[-1].canonical_sha256 if state.transactions else None
            if expected_head_sha256 != actual_head:
                raise LedgerConflictError(
                    "research ledger head changed before policy-checked commit"
                )

            immutable_index = {
                (item.study_id, item.kind, item.subject_id): item.canonical_sha256
                for item in state.records
            }
            for record in proposed:
                key = (record.study_id, record.kind, record.subject_id)
                existing_digest = immutable_index.get(key)
                if existing_digest is not None and existing_digest != record.canonical_sha256:
                    raise LedgerConflictError(
                        "immutable research record already exists with different content"
                    )
                self._write_record(record)

            previous = state.transactions[-1].canonical_sha256 if state.transactions else None
            transaction = ResearchLedgerTransaction.create(
                sequence=len(state.transactions),
                operation_id=normalized_operation,
                study_id=normalized_study,
                record_sha256s=digests,
                previous_transaction_sha256=previous,
            )
            self._append_transaction(transaction)
            verified = self._load_unlocked()
            if verified.transactions[-1] != transaction:
                raise LedgerCorruptionError(
                    "committed transaction failed read-after-write verification"
                )
            return transaction

    def recover_torn_tail(self) -> TornTailRecovery:
        """Quarantine and discard only a final non-newline-terminated transaction fragment."""

        with self._writer_lock():
            raw = self.journal_path.read_bytes() if self.journal_path.exists() else b""
            if not raw or raw.endswith(b"\n"):
                self._load_raw(raw)
                return TornTailRecovery(False, 0, None, None)

            split_at = raw.rfind(b"\n") + 1
            valid_prefix = raw[:split_at]
            torn_tail = raw[split_at:]
            self._load_raw(valid_prefix)
            tail_sha256 = content_sha256(torn_tail)
            relative = Path("recovery") / f"torn-{tail_sha256}.bin"
            self._write_immutable(self.root / relative, torn_tail)
            self._atomic_replace(self.journal_path, valid_prefix)
            self._load_unlocked()
            return TornTailRecovery(
                repaired=True,
                discarded_bytes=len(torn_tail),
                discarded_sha256=tail_sha256,
                quarantine_relative_path=relative.as_posix(),
            )

    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def _load_unlocked(self) -> ResearchLedgerState:
        raw = self.journal_path.read_bytes() if self.journal_path.exists() else b""
        return self._load_raw(raw)

    def _load_raw(self, raw: bytes) -> ResearchLedgerState:
        if raw and not raw.endswith(b"\n"):
            split_at = raw.rfind(b"\n") + 1
            raise TornLedgerTailError(
                valid_prefix_bytes=split_at,
                torn_bytes=len(raw) - split_at,
            )

        transactions: list[ResearchLedgerTransaction] = []
        operations: set[str] = set()
        previous: str | None = None
        for expected_sequence, line in enumerate(raw.splitlines()):
            value = self._decode_canonical_mapping(line, "journal transaction")
            try:
                transaction = ResearchLedgerTransaction.from_dict(value)
            except (KeyError, TypeError, ValueError) as exc:
                raise LedgerCorruptionError("invalid research ledger transaction") from exc
            if not transaction.verify():
                raise LedgerCorruptionError("research ledger transaction digest mismatch")
            if transaction.sequence != expected_sequence:
                raise LedgerCorruptionError(
                    "research ledger transaction sequence is not contiguous"
                )
            if transaction.previous_transaction_sha256 != previous:
                raise LedgerCorruptionError("research ledger transaction hash chain is broken")
            if transaction.operation_id in operations:
                raise LedgerCorruptionError("research ledger contains duplicate operation ids")
            operations.add(transaction.operation_id)
            transactions.append(transaction)
            previous = transaction.canonical_sha256

        records: list[FrozenResearchRecord] = []
        records_by_sha: dict[str, FrozenResearchRecord] = {}
        immutable_index: dict[tuple[str, ResearchRecordKind, str], str] = {}
        for transaction in transactions:
            for digest in transaction.record_sha256s:
                record = records_by_sha.get(digest)
                if record is None:
                    record = self._read_record(digest)
                    records_by_sha[digest] = record
                    records.append(record)
                if record.study_id != transaction.study_id:
                    raise LedgerCorruptionError(
                        "transaction references a record from a different study"
                    )
                key = (record.study_id, record.kind, record.subject_id)
                prior = immutable_index.get(key)
                if prior is not None and prior != record.canonical_sha256:
                    raise LedgerCorruptionError("immutable research record key was rewritten")
                immutable_index[key] = record.canonical_sha256

        return ResearchLedgerState(tuple(transactions), tuple(records))

    def _read_record(self, digest: str) -> FrozenResearchRecord:
        path = self.object_path(digest)
        if not path.is_file():
            raise LedgerCorruptionError(f"research record object is missing: {digest}")
        value = self._decode_canonical_mapping(path.read_bytes(), "research record")
        try:
            record = FrozenResearchRecord.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerCorruptionError("invalid frozen research record") from exc
        if record.canonical_sha256 != digest or not record.verify():
            raise LedgerCorruptionError("frozen research record digest mismatch")
        return record

    @staticmethod
    def _decode_canonical_mapping(payload: bytes, label: str) -> Mapping[str, object]:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LedgerCorruptionError(f"{label} is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise LedgerCorruptionError(f"{label} must be a JSON object")
        if canonical_json_bytes(value) != payload:
            raise LedgerCorruptionError(f"{label} is not canonically serialized")
        return value

    def _write_record(self, record: FrozenResearchRecord) -> None:
        self._write_immutable(
            self.object_path(record.canonical_sha256),
            canonical_json_bytes(record.to_dict()),
        )

    def _append_transaction(self, transaction: ResearchLedgerTransaction) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_json_bytes(transaction.to_dict()) + b"\n"
        with self.journal_path.open("ab", buffering=0) as stream:
            stream.write(payload)
            os.fsync(stream.fileno())
        self._fsync_directory(self.journal_path.parent)

    def _write_immutable(self, path: Path, payload: bytes) -> None:
        if path.exists():
            if not path.is_file() or path.read_bytes() != payload:
                raise LedgerCorruptionError("immutable storage object has conflicting bytes")
            return
        self._atomic_replace(path, payload, fail_if_exists=True)

    def _atomic_replace(self, path: Path, payload: bytes, *, fail_if_exists: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if fail_if_exists and path.exists():
                if path.read_bytes() != payload:
                    raise LedgerCorruptionError("immutable storage object has conflicting bytes")
                return
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
