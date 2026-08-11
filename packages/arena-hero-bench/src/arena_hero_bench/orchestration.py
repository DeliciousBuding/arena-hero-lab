"""Run/shard orchestration seams, local execution, resume, and deterministic merge."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from arena_hero_sim.contracts import SimulationRequest, SimulationResult, SimulationStatus
from arena_hero_sim.registry import BackendRegistry
from arena_hero_sim.serialization import canonical_json_bytes, content_sha256

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OrchestrationError(ValueError):
    pass


class DuplicateShardError(OrchestrationError):
    pass


class MissingShardError(OrchestrationError):
    pass


class IncompleteShardError(OrchestrationError):
    pass


class IdempotencyConflictError(OrchestrationError):
    pass


def _id(value: str, field_name: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase portable identifier")
    return value


def _sha(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


class RunStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExperimentId:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _id(self.value, "experiment_id"))


@dataclass(frozen=True, slots=True)
class RunId:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _id(self.value, "run_id"))


@dataclass(frozen=True, slots=True)
class ShardId:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _id(self.value, "shard_id"))


@dataclass(frozen=True, slots=True)
class ShardPlan:
    operation_id: str
    experiment_id: ExperimentId
    run_id: RunId
    shard_id: ShardId
    requests: tuple[SimulationRequest, ...]
    plan_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _id(self.operation_id, "operation_id"))
        object.__setattr__(self, "plan_sha256", _sha(self.plan_sha256, "plan_sha256"))
        if not self.requests:
            raise ValueError("shard requests must not be empty")

    @classmethod
    def create(
        cls,
        *,
        operation_id: str,
        experiment_id: ExperimentId,
        run_id: RunId,
        shard_id: ShardId,
        requests: Sequence[SimulationRequest],
    ) -> ShardPlan:
        request_tuple = tuple(requests)
        identity = [
            {
                "request_id": item.request_id,
                "episode_id": item.episode_id,
                "config": {
                    "backend_id": item.config.backend_id,
                    "engine_version": item.config.engine_version,
                    "ruleset": {
                        "name": item.config.ruleset.name,
                        "version": item.config.ruleset.version,
                        "rules_sha256": item.config.ruleset.rules_sha256,
                    },
                    "seed": item.config.seed,
                    "max_ticks": item.config.max_ticks,
                    "protocol_version": item.config.protocol_version,
                    "deterministic": item.config.deterministic,
                    "requested_features": sorted(item.config.requested_features),
                    "parameters": dict(item.config.parameters),
                },
                "initial_state_sha256": item.initial_state_sha256,
                "input_artifact_sha256": item.input_artifact_sha256,
                "contestant_ids": list(item.contestant_ids),
                "labels": dict(item.labels),
            }
            for item in request_tuple
        ]
        payload = {
            "operation_id": operation_id,
            "experiment_id": experiment_id.value,
            "run_id": run_id.value,
            "shard_id": shard_id.value,
            "requests": identity,
        }
        return cls(
            operation_id=operation_id,
            experiment_id=experiment_id,
            run_id=run_id,
            shard_id=shard_id,
            requests=request_tuple,
            plan_sha256=content_sha256(payload),
        )


@dataclass(frozen=True, slots=True)
class ShardResult:
    run_id: RunId
    shard_id: ShardId
    status: RunStatus
    publishable: bool
    content_sha256: str
    artifact_ref: str
    request_ids: tuple[str, ...]
    errors: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_sha256", _sha(self.content_sha256, "content_sha256"))
        if self.status is not RunStatus.COMPLETE and self.publishable:
            raise ValueError(f"{self.status.value} shards must set publishable=false")
        object.__setattr__(self, "request_ids", tuple(self.request_ids))
        object.__setattr__(self, "errors", tuple(self.errors))


@dataclass(frozen=True, slots=True)
class MergedRun:
    run_id: RunId
    status: RunStatus
    publishable: bool
    shard_ids: tuple[ShardId, ...]
    shard_content_sha256: tuple[str, ...]
    content_sha256: str


class ArtifactStore(Protocol):
    def put(self, payload: bytes, *, expected_sha256: str | None = None) -> str: ...

    def get(self, digest: str) -> bytes: ...


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, payload: bytes, *, expected_sha256: str | None = None) -> str:
        digest = content_sha256(payload)
        if expected_sha256 is not None and expected_sha256 != digest:
            raise OrchestrationError("artifact payload does not match expected SHA-256")
        self._objects.setdefault(digest, payload)
        return digest

    def get(self, digest: str) -> bytes:
        return self._objects[digest]


class ExecutionLedger(Protocol):
    def resume(self, operation_id: str, plan_sha256: str) -> ShardResult | None: ...

    def record(self, operation_id: str, plan_sha256: str, result: ShardResult) -> None: ...


class InMemoryExecutionLedger:
    def __init__(self) -> None:
        self._results: dict[str, tuple[str, ShardResult]] = {}

    def resume(self, operation_id: str, plan_sha256: str) -> ShardResult | None:
        existing = self._results.get(operation_id)
        if existing is None:
            return None
        existing_plan, result = existing
        if existing_plan != plan_sha256:
            raise IdempotencyConflictError("operation id was reused with a different plan")
        return result

    def record(self, operation_id: str, plan_sha256: str, result: ShardResult) -> None:
        existing = self.resume(operation_id, plan_sha256)
        if existing is not None and existing != result:
            raise IdempotencyConflictError("operation id produced a different result")
        self._results[operation_id] = (plan_sha256, result)


class LocalExecutor(Protocol):
    def execute(self, plan: ShardPlan) -> ShardResult: ...


@dataclass(frozen=True, slots=True)
class DistributedExecutionHandle:
    operation_id: str
    executor_ref: str


class DistributedExecutor(Protocol):
    """Future seam; no network or scheduler implementation is implied."""

    def submit(self, plan: ShardPlan) -> DistributedExecutionHandle: ...

    def poll(self, handle: DistributedExecutionHandle) -> ShardResult | None: ...

    def cancel(self, handle: DistributedExecutionHandle) -> None: ...


def build_shard_result(
    plan: ShardPlan,
    simulation_results: Sequence[SimulationResult],
    artifact_store: ArtifactStore,
) -> ShardResult:
    """Materialize one content-addressed shard result from engine results.

    The shard payload schema is owned here so in-process and process-executed
    shards produce byte-identical artifacts for identical engine results.
    Results must arrive in plan request order.
    """
    results = tuple(simulation_results)
    if len(results) != len(plan.requests):
        raise OrchestrationError("simulation result count must match the shard plan")
    if tuple(item.request_id for item in results) != tuple(
        request.request_id for request in plan.requests
    ):
        raise OrchestrationError("simulation results must be in plan request order")
    if any(item.status is SimulationStatus.FAILED for item in results):
        status = RunStatus.FAILED
    elif all(item.status is SimulationStatus.COMPLETE for item in results):
        status = RunStatus.COMPLETE
    else:
        status = RunStatus.PARTIAL
    publishable = status is RunStatus.COMPLETE and all(item.publishable for item in results)
    payload = canonical_json_bytes(
        {
            "schema_version": "arena.bench.shard-result.v1",
            "run_id": plan.run_id.value,
            "shard_id": plan.shard_id.value,
            "plan_sha256": plan.plan_sha256,
            "results": [
                {
                    "request_id": item.request_id,
                    "status": item.status.value,
                    "publishable": item.publishable,
                    "final_world_sha256": item.final_world_sha256,
                    "errors": list(item.errors),
                }
                for item in results
            ],
        }
    )
    digest = artifact_store.put(payload)
    return ShardResult(
        run_id=plan.run_id,
        shard_id=plan.shard_id,
        status=status,
        publishable=publishable,
        content_sha256=digest,
        artifact_ref=f"sha256:{digest}",
        request_ids=tuple(item.request_id for item in results),
        errors=tuple(error for item in results for error in item.errors),
    )


class LocalBatchExecutor:
    """Deterministic local executor with resume/idempotency support."""

    def __init__(
        self,
        backend_registry: BackendRegistry,
        artifact_store: ArtifactStore,
        ledger: ExecutionLedger,
    ) -> None:
        self.backend_registry = backend_registry
        self.artifact_store = artifact_store
        self.ledger = ledger

    def execute(self, plan: ShardPlan) -> ShardResult:
        resumed = self.ledger.resume(plan.operation_id, plan.plan_sha256)
        if resumed is not None:
            return resumed
        simulation_results = self.backend_registry.simulate_batch(plan.requests)
        result = build_shard_result(plan, simulation_results, self.artifact_store)
        self.ledger.record(plan.operation_id, plan.plan_sha256, result)
        return result


def merge_shards(expected_shards: Sequence[ShardId], results: Sequence[ShardResult]) -> MergedRun:
    expected = tuple(expected_shards)
    if len(expected) != len(set(expected)):
        raise DuplicateShardError("expected shard ids contain duplicates")
    by_id: dict[ShardId, ShardResult] = {}
    for result in results:
        if result.shard_id in by_id:
            raise DuplicateShardError(f"duplicate shard result: {result.shard_id.value}")
        by_id[result.shard_id] = result
    missing = set(expected) - set(by_id)
    unexpected = set(by_id) - set(expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing=" + ",".join(sorted(item.value for item in missing)))
        if unexpected:
            details.append("unexpected=" + ",".join(sorted(item.value for item in unexpected)))
        raise MissingShardError("shard coverage mismatch: " + " ".join(details))
    ordered = tuple(by_id[item] for item in sorted(expected, key=lambda item: item.value))
    run_ids = {item.run_id for item in ordered}
    if len(run_ids) != 1:
        raise OrchestrationError("all shards must belong to one run")
    incomplete = [item for item in ordered if item.status is not RunStatus.COMPLETE]
    if incomplete:
        raise IncompleteShardError(
            "cannot publish incomplete shards: "
            + ",".join(item.shard_id.value for item in incomplete)
        )
    if any(not item.publishable for item in ordered):
        raise IncompleteShardError("complete shard set contains an unpublishable shard")
    digest_payload = {
        "schema_version": "arena.bench.merged-run.v1",
        "run_id": ordered[0].run_id.value,
        "shards": [
            {"shard_id": item.shard_id.value, "content_sha256": item.content_sha256}
            for item in ordered
        ],
    }
    return MergedRun(
        run_id=ordered[0].run_id,
        status=RunStatus.COMPLETE,
        publishable=True,
        shard_ids=tuple(item.shard_id for item in ordered),
        shard_content_sha256=tuple(item.content_sha256 for item in ordered),
        content_sha256=content_sha256(digest_payload),
    )
