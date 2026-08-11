from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from arena_hero_bench.manifest import ArtifactManifest, ArtifactStatus
from arena_hero_bench.storage import (
    ArtifactConflictError,
    ArtifactStoreError,
    CorruptObjectError,
    FilesystemArtifactStore,
    MissingObjectError,
    StoreLock,
    StoreLockError,
)
from arena_hero_sim.serialization import canonical_json_bytes, content_sha256, to_json_value

_SHA = "a" * 64


def artifact_for(
    content: bytes,
    *,
    status: ArtifactStatus = ArtifactStatus.COMPLETE,
    publishable: bool | None = None,
) -> ArtifactManifest:
    if publishable is None:
        publishable = status is ArtifactStatus.COMPLETE
    return ArtifactManifest.for_content(
        content=content,
        schema_version="arena.lab.artifact.v1",
        generator_version="0.1.0",
        provenance={"source": "tests/artifact.json"},
        source_build_sha256=_SHA,
        status=status,
        publishable=publishable,
    )


def test_put_get_verify_roundtrip(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = canonical_json_bytes({"run": "fixture", "scores": [1, 2, 3]})

    digest = store.put(payload)

    assert digest == content_sha256(payload)
    assert store.get(digest) == payload
    assert store.verify(digest)
    assert store.contains(digest)


def test_duplicate_put_is_idempotent(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = b"same immutable bytes"

    first = store.put(payload)
    second = store.put(payload)

    assert first == second
    files = [path for path in (tmp_path / "store" / "objects").rglob("*") if path.is_file()]
    assert len(files) == 1


def test_put_rejects_expected_digest_mismatch(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")

    with pytest.raises(ArtifactStoreError, match="expected SHA-256"):
        store.put(b"payload", expected_sha256="0" * 64)


def test_put_rejects_non_bytes_payload(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")

    with pytest.raises(ArtifactStoreError, match="payload must be bytes"):
        store.put(cast(bytes, "not bytes"))


class _FixedDigest:
    def hexdigest(self) -> str:
        return "c" * 64


def test_same_identity_different_bytes_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    fixed_digest = "c" * 64
    # Simulate a genuine SHA-256 collision: both payloads map to the same
    # digest while their stored bytes differ, so the identity is truly reused.
    monkeypatch.setattr("arena_hero_bench.storage.content_sha256", lambda _value: fixed_digest)
    monkeypatch.setattr("arena_hero_bench.storage.hashlib.sha256", lambda _data: _FixedDigest())

    store.put(b"first bytes")

    with pytest.raises(ArtifactConflictError, match="different bytes"):
        store.put(b"second bytes")


def test_put_self_heals_corrupt_existing_object(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = b"canonical bytes"
    digest = store.put(payload)

    store._object_path(digest).write_bytes(b"torn or corrupt bytes")
    assert not store.verify(digest)

    healed = store.put(payload)
    assert healed == digest
    assert store.verify(digest)
    assert store.get(digest) == payload


def test_get_missing_raises(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")

    with pytest.raises(MissingObjectError, match="not found"):
        store.get("d" * 64)


@pytest.mark.parametrize(
    "digest",
    [
        "",
        "..",
        "../escape",
        "..\\escape",
        "a\\..\\escape",
        f"C:{os.sep}escape",
        "objects/../../escape",
        "not-a-digest",
        "A" * 64,
    ],
)
def test_get_rejects_non_digest_identifiers(tmp_path: Path, digest: str) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")

    with pytest.raises(ArtifactStoreError, match="SHA-256"):
        store.get(digest)


def test_torn_or_corrupt_object_fails_closed(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = b"immutable object"
    digest = store.put(payload)

    store._object_path(digest).write_bytes(b"torn tail bytes")

    assert not store.verify(digest)
    with pytest.raises(CorruptObjectError, match="does not match digest"):
        store.get(digest)


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    store.put(b"alpha")
    store.put(b"beta")

    assert list((tmp_path / "store" / ".tmp").iterdir()) == []


def test_store_artifact_roundtrip_and_publishable(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content = canonical_json_bytes({"scores": [1, 2, 3]})
    artifact = artifact_for(content)

    store.store_artifact(artifact, content)

    assert store.load_artifact(artifact) == content
    assert store.verify(artifact.content_sha256)
    assert store.is_publishable(artifact.content_sha256)


def test_store_artifact_rejects_content_mismatch(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    artifact = artifact_for(canonical_json_bytes({"scores": [1, 2, 3]}))
    other = canonical_json_bytes({"scores": [1, 2, 4]})

    with pytest.raises(ArtifactConflictError, match="does not match manifest"):
        store.store_artifact(artifact, other)


@pytest.mark.parametrize("status", [ArtifactStatus.PARTIAL, ArtifactStatus.FAILED])
def test_partial_and_failed_artifacts_never_publishable(
    tmp_path: Path, status: ArtifactStatus
) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content = canonical_json_bytes({"run": status.value, "torn": True})
    artifact = artifact_for(content, status=status, publishable=False)

    store.store_artifact(artifact, content)

    assert store.load_artifact(artifact) == content
    assert not store.is_publishable(artifact.content_sha256)


def test_complete_but_unpublishable_artifact_never_publishable(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content = b"complete but withheld"
    artifact = artifact_for(content, publishable=False)

    store.store_artifact(artifact, content)

    assert not store.is_publishable(artifact.content_sha256)


def test_is_publishable_fails_closed_when_object_missing_or_corrupt(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content = canonical_json_bytes({"scores": [1, 2, 3]})
    artifact = artifact_for(content)
    store.store_artifact(artifact, content)
    assert store.is_publishable(artifact.content_sha256)

    store._object_path(artifact.content_sha256).write_bytes(b"torn bytes")
    assert not store.is_publishable(artifact.content_sha256)

    store._object_path(artifact.content_sha256).unlink()
    assert not store.is_publishable(artifact.content_sha256)


def test_manifest_records_skips_wrong_name_and_tampered_records(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content = canonical_json_bytes({"scores": [9, 8, 7]})
    artifact = artifact_for(content)
    store.store_artifact(artifact, content)

    valid_body = {
        "schema_version": "arena.lab.artifact.v1",
        "generator_version": "0.1.0",
        "provenance": {"source": "tests/artifact.json"},
        "source_build_sha256": "a" * 64,
        "content_sha256": "b" * 64,
        "status": "complete",
        "publishable": True,
    }
    # A manifest body under a non-digest filename must be skipped.
    (store._manifests / "not-a-digest.json").write_text(json.dumps(valid_body), encoding="utf-8")

    # A tampered manifest whose bytes no longer match its digest filename must
    # be skipped even though it still parses.
    tampered_digest = content_sha256(to_json_value(valid_body))
    (store._manifests / f"{tampered_digest}.json").write_text(
        json.dumps({**valid_body, "publishable": False}), encoding="utf-8"
    )

    records = list(store.manifest_records())
    assert len(records) == 1
    assert records[0].to_dict() == artifact.to_dict()


def test_manifest_record_roundtrip(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content = canonical_json_bytes({"scores": [5, 4, 3]})
    artifact = artifact_for(content)

    store.store_artifact(artifact, content)

    records = list(store.manifest_records())
    assert len(records) == 1
    assert records[0].to_dict() == artifact.to_dict()


def test_store_lock_serializes_and_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "writer.lock"
    first = StoreLock(lock_path)
    second = StoreLock(lock_path, timeout=0.2)

    first.acquire()
    assert lock_path.exists()
    with pytest.raises(StoreLockError, match="could not acquire"):
        second.acquire()
    first.release()
    assert not lock_path.exists()

    second.acquire()
    second.release()
    assert not lock_path.exists()


def test_store_lock_release_keeps_foreign_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "writer.lock"
    first = StoreLock(lock_path)
    second = StoreLock(lock_path, timeout=0.2)

    first.acquire()
    with pytest.raises(StoreLockError):
        second.acquire()
    second.release()
    assert lock_path.exists()

    first.release()
    assert not lock_path.exists()


def test_live_lock_is_never_taken_over_even_when_old(tmp_path: Path) -> None:
    lock_path = tmp_path / "writer.lock"
    first = StoreLock(lock_path, timeout=0.5)
    first.acquire()

    # Age the live lock far beyond any conceivable stale threshold; the mutex
    # is fail-closed and must still refuse the second writer.
    old = time.time() - 3600.0
    os.utime(lock_path, (old, old))

    second = StoreLock(lock_path, timeout=0.2)
    with pytest.raises(StoreLockError, match="could not acquire"):
        second.acquire()
    assert lock_path.exists()

    first.release()
    assert not lock_path.exists()


def test_store_lock_requires_explicit_recovery_after_crash(tmp_path: Path) -> None:
    lock_path = tmp_path / "writer.lock"
    lock_path.write_text("dead:owner\n", encoding="ascii")

    blocked = StoreLock(lock_path, timeout=0.2)
    with pytest.raises(StoreLockError, match="could not acquire"):
        blocked.acquire()
    assert lock_path.exists()

    StoreLock.recover(lock_path)
    assert not lock_path.exists()

    lock = StoreLock(lock_path, timeout=0.5)
    lock.acquire()
    lock.release()
    assert not lock_path.exists()


def test_store_lock_mutual_exclusion_across_threads(tmp_path: Path) -> None:
    path = tmp_path / "writer.lock"
    counter = 0

    def worker() -> None:
        nonlocal counter
        lock = StoreLock(path, timeout=10.0)
        with lock:
            value = counter
            time.sleep(0.002)
            counter = value + 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: worker(), range(16)))

    assert counter == 16


def test_concurrent_identical_puts_are_safe(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = canonical_json_bytes({"run": "shared", "scores": list(range(50))})

    def worker() -> str:
        return store.put(payload)

    with ThreadPoolExecutor(max_workers=8) as pool:
        digests = list(pool.map(lambda _index: worker(), range(32)))

    assert len(set(digests)) == 1
    assert store.get(digests[0]) == payload


def test_filesystem_store_drives_existing_local_executor(tmp_path: Path) -> None:
    from arena_hero_bench.orchestration import (
        ExperimentId,
        InMemoryExecutionLedger,
        LocalBatchExecutor,
        RunId,
        ShardId,
        ShardPlan,
    )
    from arena_hero_sim.contracts import RulesetRef, SimulationRequest, SimulatorConfig
    from arena_hero_sim.reference import ReferenceBackendPlaceholder
    from arena_hero_sim.registry import BackendRegistry

    registry = BackendRegistry()
    registry.register(ReferenceBackendPlaceholder())
    store = FilesystemArtifactStore(tmp_path / "store")
    ledger = InMemoryExecutionLedger()
    executor = LocalBatchExecutor(registry, store, ledger)
    request = SimulationRequest(
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
    plan = ShardPlan.create(
        operation_id="operation-1",
        experiment_id=ExperimentId("experiment-1"),
        run_id=RunId("run-1"),
        shard_id=ShardId("shard-a"),
        requests=(request,),
    )

    first = executor.execute(plan)
    second = executor.execute(plan)

    assert first is second
    assert store.get(first.content_sha256)
    assert store.verify(first.content_sha256)


def test_writer_generation_advances_after_transactions(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    assert store.generation_token() == "0\n"

    store.put(b"first")
    assert store.generation_token() == "1\n"

    content = canonical_json_bytes({"artifact": "second"})
    store.store_artifact(artifact_for(content), content)
    assert store.generation_token() == "2\n"


def test_writer_generation_corruption_fails_closed(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    (store.root / ".state" / "generation").write_bytes(b"corrupt")

    with pytest.raises(ArtifactStoreError, match="generation marker"):
        store.put(b"blocked")
