"""Deterministic idempotency and data-use ledgers for offline research execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar, cast

from arena_hero_research.validation import (
    require_identifier,
    require_sequence,
    require_sha256,
)
from arena_hero_sim.serialization import JsonValue, content_sha256


class LedgerConflictError(ValueError):
    pass


_DATA_USE_ROLES = frozenset({"pilot", "exploratory", "confirmatory", "replication"})


@dataclass(frozen=True, slots=True)
class OperationRecord:
    schema_version: str
    operation_id: str
    plan_sha256: str
    result_sha256s: tuple[str, ...]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.operation-record.v1":
            raise LedgerConflictError("unsupported operation record schema")
        object.__setattr__(
            self, "operation_id", require_identifier(self.operation_id, "operation_id")
        )
        object.__setattr__(self, "plan_sha256", require_sha256(self.plan_sha256, "plan_sha256"))
        results = tuple(require_sha256(item, "result_sha256") for item in self.result_sha256s)
        if len(results) != len(set(results)):
            raise LedgerConflictError("operation record contains duplicate result digests")
        object.__setattr__(self, "result_sha256s", results)
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "plan_sha256": self.plan_sha256,
            "result_sha256s": list(self.result_sha256s),
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def create(
        cls, *, operation_id: str, plan_sha256: str, result_sha256s: tuple[str, ...]
    ) -> OperationRecord:
        payload: dict[str, JsonValue] = {
            "schema_version": "arena.research.operation-record.v1",
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "result_sha256s": list(result_sha256s),
        }
        return cls(
            schema_version="arena.research.operation-record.v1",
            operation_id=operation_id,
            plan_sha256=plan_sha256,
            result_sha256s=result_sha256s,
            canonical_sha256=content_sha256(payload),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> OperationRecord:
        results = require_sequence(value["result_sha256s"], "result_sha256s")
        return cls(
            schema_version=str(value["schema_version"]),
            operation_id=str(value["operation_id"]),
            plan_sha256=str(value["plan_sha256"]),
            result_sha256s=tuple(str(item) for item in results),
            canonical_sha256=str(value["canonical_sha256"]),
        )


_Result = TypeVar("_Result")


class OperationLedger:
    """In-memory replay ledger; storage adapters may persist the versioned records."""

    def __init__(self) -> None:
        self._records: dict[str, OperationRecord] = {}
        self._values: dict[str, object] = {}

    def execute(
        self,
        *,
        operation_id: str,
        plan_sha256: str,
        execute: Callable[[], _Result],
        result_digests: Callable[[_Result], Sequence[str]],
    ) -> _Result:
        normalized_id = require_identifier(operation_id, "operation_id")
        normalized_plan = require_sha256(plan_sha256, "plan_sha256")
        existing = self._records.get(normalized_id)
        if existing is not None:
            if existing.plan_sha256 != normalized_plan:
                raise LedgerConflictError("operation id already exists with a conflicting plan")
            return cast(_Result, self._values[normalized_id])

        value = execute()
        digests = tuple(result_digests(value))
        record = OperationRecord.create(
            operation_id=normalized_id,
            plan_sha256=normalized_plan,
            result_sha256s=digests,
        )
        self._records[normalized_id] = record
        self._values[normalized_id] = value
        return value

    def record(self, operation_id: str) -> OperationRecord | None:
        return self._records.get(operation_id)


@dataclass(frozen=True, slots=True)
class DataUseClaim:
    dataset_sha256: str
    study_id: str
    role: str
    operation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "dataset_sha256", require_sha256(self.dataset_sha256, "dataset_sha256")
        )
        object.__setattr__(self, "study_id", require_identifier(self.study_id, "study_id"))
        role = require_identifier(self.role, "role")
        if role not in _DATA_USE_ROLES:
            raise LedgerConflictError("data-use role is not part of the research lifecycle")
        object.__setattr__(self, "role", role)
        object.__setattr__(
            self, "operation_id", require_identifier(self.operation_id, "operation_id")
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "dataset_sha256": self.dataset_sha256,
            "study_id": self.study_id,
            "role": self.role,
            "operation_id": self.operation_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DataUseClaim:
        return cls(
            dataset_sha256=str(value["dataset_sha256"]),
            study_id=str(value["study_id"]),
            role=str(value["role"]),
            operation_id=str(value["operation_id"]),
        )


class DataUseLedger:
    """Reject pilot leakage and holdout reuse across confirmatory operations."""

    _PRELIMINARY = frozenset({"pilot", "exploratory"})
    _HELD_OUT = frozenset({"confirmatory", "replication"})

    def __init__(self) -> None:
        self._claims: dict[str, list[DataUseClaim]] = {}

    def claim(self, claim: DataUseClaim) -> None:
        existing = self._claims.setdefault(claim.dataset_sha256, [])
        if claim in existing:
            return
        for prior in existing:
            if prior.role in self._PRELIMINARY and claim.role in self._HELD_OUT:
                raise LedgerConflictError(
                    "pilot or exploratory data cannot be reused as confirmatory holdout"
                )
            if prior.role in self._HELD_OUT and claim.role in self._PRELIMINARY:
                raise LedgerConflictError(
                    "confirmatory holdout cannot be reused for pilot or exploratory analysis"
                )
            if (
                prior.role in self._HELD_OUT
                and claim.role in self._HELD_OUT
                and (prior.study_id != claim.study_id or prior.operation_id != claim.operation_id)
            ):
                raise LedgerConflictError(
                    "confirmatory holdout already belongs to another study or operation"
                )
        existing.append(claim)

    def claims(self, dataset_sha256: str) -> tuple[DataUseClaim, ...]:
        return tuple(self._claims.get(dataset_sha256, ()))
