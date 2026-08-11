"""Fail-closed merge tests: missing, duplicate, and corrupt shards.

Each failure class is injected at the merge seam and must surface as its
dedicated ``OrchestrationError`` subtype. Corruption is judged by content
verification: the shard artifact bytes are fetched and re-hashed against the
claimed digest, never by mere presence of a ``ShardResult``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arena_hero_bench.orchestration import (
    CorruptShardError,
    DuplicateShardError,
    InMemoryArtifactStore,
    MissingShardError,
    RunId,
    RunStatus,
    ShardId,
    ShardResult,
    merge_shards,
)
from arena_hero_bench.storage import FilesystemArtifactStore
from arena_hero_sim.serialization import canonical_json_bytes


def _result(shard: str, content_sha256: str) -> ShardResult:
    return ShardResult(
        run_id=RunId("run-1"),
        shard_id=ShardId(shard),
        status=RunStatus.COMPLETE,
        publishable=True,
        content_sha256=content_sha256,
        artifact_ref=f"sha256:{content_sha256}",
        request_ids=(f"request-{shard}",),
    )


def _stored_result(shard: str) -> ShardResult:
    """Build a result whose digest matches the canonical payload in a store."""
    store = InMemoryArtifactStore()
    digest = store.put(canonical_json_bytes({"shard": shard}))
    return _result(shard, digest)


class _TamperingArtifactStore(InMemoryArtifactStore):
    """In-memory store that can serve tampered or missing artifact bytes."""

    def __init__(
        self,
        *,
        tamper: frozenset[str] = frozenset(),
        missing: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self._tamper = tamper
        self._missing = missing

    def get(self, digest: str) -> bytes:
        if digest in self._missing:
            raise KeyError(f"artifact not found: {digest}")
        payload = super().get(digest)
        if digest in self._tamper:
            return b"tampered shard artifact bytes"
        return payload


def test_merge_rejects_corrupt_shard_content() -> None:
    result_a = _stored_result("shard-a")
    result_b = _stored_result("shard-b")
    store = _TamperingArtifactStore(tamper=frozenset({result_b.content_sha256}))
    store.put(canonical_json_bytes({"shard": "shard-a"}))
    store.put(canonical_json_bytes({"shard": "shard-b"}))

    with pytest.raises(CorruptShardError, match="content does not match"):
        merge_shards(
            (ShardId("shard-a"), ShardId("shard-b")),
            (result_a, result_b),
            artifact_store=store,
        )


def test_merge_rejects_shard_artifact_missing_from_store() -> None:
    result_a = _stored_result("shard-a")
    result_b = _stored_result("shard-b")
    store = _TamperingArtifactStore(missing=frozenset({result_b.content_sha256}))
    store.put(canonical_json_bytes({"shard": "shard-a"}))
    store.put(canonical_json_bytes({"shard": "shard-b"}))

    with pytest.raises(CorruptShardError, match="cannot be verified"):
        merge_shards(
            (ShardId("shard-a"), ShardId("shard-b")),
            (result_a, result_b),
            artifact_store=store,
        )


def test_merge_rejects_filesystem_artifact_corrupted_after_put(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    digest = store.put(canonical_json_bytes({"shard": "shard-a"}))
    store._object_path(digest).write_bytes(b"torn or tampered bytes")
    result = _result("shard-a", digest)

    with pytest.raises(CorruptShardError, match="cannot be verified"):
        merge_shards((ShardId("shard-a"),), (result,), artifact_store=store)


def test_merge_rejects_duplicate_expected_shard_ids() -> None:
    with pytest.raises(DuplicateShardError, match="expected shard ids contain duplicates"):
        merge_shards(
            (ShardId("shard-a"), ShardId("shard-a")),
            (_result("shard-a", "a" * 64),),
        )


def test_merge_rejects_unexpected_shard() -> None:
    with pytest.raises(MissingShardError, match="unexpected=shard-b"):
        merge_shards(
            (ShardId("shard-a"),),
            (_result("shard-a", "a" * 64), _result("shard-b", "b" * 64)),
        )


def test_merge_with_store_verifies_content_and_preserves_digest() -> None:
    result_a = _stored_result("shard-a")
    result_b = _stored_result("shard-b")
    store = InMemoryArtifactStore()
    store.put(canonical_json_bytes({"shard": "shard-a"}))
    store.put(canonical_json_bytes({"shard": "shard-b"}))

    with_store = merge_shards(
        (ShardId("shard-a"), ShardId("shard-b")),
        (result_a, result_b),
        artifact_store=store,
    )
    without_store = merge_shards((ShardId("shard-a"), ShardId("shard-b")), (result_a, result_b))

    assert with_store.content_sha256 == without_store.content_sha256
    assert with_store.shard_content_sha256 == (result_a.content_sha256, result_b.content_sha256)
    assert with_store.status is RunStatus.COMPLETE
    assert with_store.publishable is True
