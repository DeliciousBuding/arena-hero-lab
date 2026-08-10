from __future__ import annotations

import pytest

from arena_hero_bench.orchestration import (
    DuplicateShardError,
    ExperimentId,
    IncompleteShardError,
    InMemoryArtifactStore,
    InMemoryExecutionLedger,
    LocalBatchExecutor,
    MissingShardError,
    RunId,
    RunStatus,
    ShardId,
    ShardPlan,
    ShardResult,
    merge_shards,
)
from arena_hero_sim.contracts import RulesetRef, SimulationRequest, SimulatorConfig
from arena_hero_sim.reference import ReferenceBackendPlaceholder
from arena_hero_sim.registry import BackendRegistry


def result(shard: str, *, status: RunStatus = RunStatus.COMPLETE) -> ShardResult:
    return ShardResult(
        run_id=RunId("run-1"),
        shard_id=ShardId(shard),
        status=status,
        publishable=status is RunStatus.COMPLETE,
        content_sha256=("a" if shard == "shard-a" else "b") * 64,
        artifact_ref=f"sha256:{shard}",
        request_ids=(f"request-{shard}",),
    )


def request() -> SimulationRequest:
    return SimulationRequest(
        request_id="request-1",
        episode_id="episode-1",
        config=SimulatorConfig(
            backend_id="reference-placeholder",
            engine_version="0.1.0-placeholder",
            ruleset=RulesetRef("arena-hero", "fixture-v1", "c" * 64),
            seed=1,
            max_ticks=10,
            protocol_version="arena.sim.v1",
        ),
        initial_state_sha256="d" * 64,
        contestant_ids=("alpha", "beta"),
    )


def test_merge_rejects_duplicate_shard() -> None:
    with pytest.raises(DuplicateShardError, match="duplicate shard"):
        merge_shards(
            (ShardId("shard-a"),),
            (result("shard-a"), result("shard-a")),
        )


def test_merge_rejects_missing_shard() -> None:
    with pytest.raises(MissingShardError, match="missing=shard-b"):
        merge_shards(
            (ShardId("shard-a"), ShardId("shard-b")),
            (result("shard-a"),),
        )


def test_merge_rejects_partial_shard() -> None:
    with pytest.raises(IncompleteShardError, match="shard-b"):
        merge_shards(
            (ShardId("shard-a"), ShardId("shard-b")),
            (result("shard-a"), result("shard-b", status=RunStatus.PARTIAL)),
        )


def test_merge_is_content_addressed_and_order_independent() -> None:
    expected = (ShardId("shard-a"), ShardId("shard-b"))
    left = merge_shards(expected, (result("shard-b"), result("shard-a")))
    right = merge_shards(expected, (result("shard-a"), result("shard-b")))
    assert left.content_sha256 == right.content_sha256
    assert tuple(item.value for item in left.shard_ids) == ("shard-a", "shard-b")


def test_local_executor_is_idempotent_and_placeholder_is_partial() -> None:
    registry = BackendRegistry()
    registry.register(ReferenceBackendPlaceholder())
    store = InMemoryArtifactStore()
    ledger = InMemoryExecutionLedger()
    executor = LocalBatchExecutor(registry, store, ledger)
    plan = ShardPlan.create(
        operation_id="operation-1",
        experiment_id=ExperimentId("experiment-1"),
        run_id=RunId("run-1"),
        shard_id=ShardId("shard-a"),
        requests=(request(),),
    )
    first = executor.execute(plan)
    second = executor.execute(plan)
    assert first is second
    assert first.status is RunStatus.PARTIAL
    assert first.publishable is False
    assert store.get(first.content_sha256)
