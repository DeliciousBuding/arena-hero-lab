"""Tests for the derived artifact index and read-only GC plan."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import arena_hero_bench.artifact_index as artifact_index_module
from arena_hero_bench.artifact_index import (
    ArtifactIndexError,
    GcCandidate,
    GcPlan,
    StaleScanError,
    StoreScan,
    build_gc_plan,
)
from arena_hero_bench.manifest import ArtifactManifest, ArtifactStatus, RunManifest
from arena_hero_bench.storage import FilesystemArtifactStore
from arena_hero_sim.serialization import (
    JsonValue,
    canonical_json_bytes,
    content_sha256,
    to_json_value,
)

_SHA = "a" * 64


def artifact_for(content: bytes, *, tag: str = "tests/index.json") -> ArtifactManifest:
    return ArtifactManifest.for_content(
        content=content,
        schema_version="arena.lab.artifact.v1",
        generator_version="0.1.0",
        provenance={"source": tag},
        source_build_sha256=_SHA,
    )


def put_artifact(
    store: FilesystemArtifactStore, content: bytes, *, tag: str = "tests/index.json"
) -> ArtifactManifest:
    manifest = artifact_for(content, tag=tag)
    store.store_artifact(manifest, content)
    return manifest


def manifest_digest(manifest: ArtifactManifest) -> str:
    return content_sha256(to_json_value(manifest.to_dict()))


def run_for(content: bytes, artifacts: tuple[ArtifactManifest, ...]) -> RunManifest:
    return RunManifest(
        schema_version="arena.lab.run.v1",
        generator_version="0.1.0",
        provenance={"source": "tests/run.json"},
        source_build_sha256=_SHA,
        content_sha256=content_sha256(content),
        status=ArtifactStatus.COMPLETE,
        publishable=True,
        artifacts=artifacts,
    )


def put_run_record(store: FilesystemArtifactStore, run: RunManifest) -> str:
    digest = run.canonical_digest()
    (store.root / "manifests" / f"{digest}.json").write_bytes(canonical_json_bytes(run.to_dict()))
    return digest


def candidate_digests(plan: GcPlan) -> set[str]:
    return {candidate.digest for candidate in plan.candidates}


def test_reachable_and_unreferenced_objects(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root_content = canonical_json_bytes({"run": "root"})
    root = put_artifact(store, root_content)
    orphan = store.put(canonical_json_bytes({"orphan": True}))

    plan = build_gc_plan(store, [manifest_digest(root)])

    assert plan.reachable_objects == frozenset({content_sha256(root_content)})
    assert plan.unreferenced_objects == frozenset({orphan})
    assert plan.candidates == (GcCandidate("object", orphan),)
    assert plan.dry_run


def test_shared_object_is_reachable_once(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content = canonical_json_bytes({"shared": True})
    first = put_artifact(store, content, tag="tests/shared-a.json")
    second = put_artifact(store, content, tag="tests/shared-b.json")
    assert manifest_digest(first) != manifest_digest(second)

    plan = build_gc_plan(store, [manifest_digest(first), manifest_digest(second)])

    assert plan.reachable_objects == frozenset({content_sha256(content)})
    assert plan.unreferenced_objects == frozenset()
    assert plan.candidates == ()


def test_multiple_roots_union_reachability(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content_a = canonical_json_bytes({"run": "a"})
    content_b = canonical_json_bytes({"run": "b"})
    root_a = put_artifact(store, content_a, tag="tests/a.json")
    root_b = put_artifact(store, content_b, tag="tests/b.json")

    plan = build_gc_plan(store, [manifest_digest(root_a), manifest_digest(root_b)])

    assert plan.reachable_objects == frozenset(
        {content_sha256(content_a), content_sha256(content_b)}
    )
    assert plan.unreferenced_objects == frozenset()


def test_empty_roots_fail_closed(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    put_artifact(store, canonical_json_bytes({"run": "root"}))

    with pytest.raises(ArtifactIndexError, match="root set is empty"):
        build_gc_plan(store, [])
    scan = StoreScan.scan(store)
    with pytest.raises(ArtifactIndexError, match="root set is empty"):
        scan.mark_roots([])


def test_mark_roots_rejects_unknown_and_invalid_digests(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    scan = StoreScan.scan(store)

    with pytest.raises(ArtifactIndexError, match="not a verified manifest"):
        scan.mark_roots(["0" * 64])
    with pytest.raises(ArtifactIndexError, match="lowercase SHA-256"):
        scan.mark_roots(["not-a-digest"])


def test_corrupt_object_is_never_unreferenced_or_candidate(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root_content = canonical_json_bytes({"run": "root"})
    root = put_artifact(store, root_content)
    store._object_path(root.content_sha256).write_bytes(b"torn bytes")

    plan = build_gc_plan(store, [manifest_digest(root)])

    assert plan.corrupt_objects == frozenset({root.content_sha256})
    assert root.content_sha256 not in plan.reachable_objects
    assert root.content_sha256 not in plan.unreferenced_objects
    assert root.content_sha256 not in candidate_digests(plan)
    assert plan.missing_objects == frozenset()


def test_missing_object_is_reported_but_never_deletable(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root_content = canonical_json_bytes({"run": "root"})
    root = put_artifact(store, root_content)
    store._object_path(root.content_sha256).unlink()

    plan = build_gc_plan(store, [manifest_digest(root)])

    assert plan.missing_objects == frozenset({root.content_sha256})
    assert root.content_sha256 not in plan.unreferenced_objects
    assert root.content_sha256 not in candidate_digests(plan)


def test_invalid_manifests_block_the_plan(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    manifests = store.root / "manifests"

    body = {
        "schema_version": "arena.lab.artifact.v1",
        "generator_version": "0.1.0",
        "provenance": {"source": "tests/tampered.json"},
        "source_build_sha256": _SHA,
        "content_sha256": "b" * 64,
        "status": "complete",
        "publishable": True,
    }
    (manifests / "not-a-digest.json").write_text(json.dumps(body), encoding="utf-8")
    (manifests / f"{'c' * 64}.json").write_text("{not valid json", encoding="utf-8")
    tampered_digest = content_sha256(to_json_value(body))
    (manifests / f"{tampered_digest}.json").write_text(
        json.dumps({**body, "publishable": False}), encoding="utf-8"
    )
    structurally_invalid = {"schema_version": "arena.lab.artifact.v1", "status": "complete"}
    structural_digest = content_sha256(to_json_value(structurally_invalid))
    (manifests / f"{structural_digest}.json").write_bytes(
        canonical_json_bytes(structurally_invalid)
    )

    scan = StoreScan.scan(store)
    reasons = {issue.reason for issue in scan.invalid_manifests}
    assert reasons == {"wrong-name", "raw-hash-mismatch", "invalid-record"}

    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()
    assert plan.unreferenced_objects == frozenset()
    assert plan.unreferenced_manifests == frozenset()
    assert plan.reachable_objects == frozenset()
    for issue in scan.invalid_manifests:
        assert issue.digest not in plan.unreferenced_manifests
        assert issue.digest not in candidate_digests(plan)


def test_live_writer_lock_blocks_plan_without_takeover(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    store.put(b"orphan bytes")
    lock = store.root / ".locks" / "writer.lock"
    lock.write_text("live:owner\n", encoding="ascii")

    scan = StoreScan.scan(store)
    assert scan.blocked

    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()
    assert plan.unreferenced_objects == frozenset()
    assert lock.exists()

    direct = build_gc_plan(store, [manifest_digest(root)])
    assert direct.blocked
    assert direct.candidates == ()


def test_non_digest_and_mislocated_object_entries_fail_closed(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    objects = store.root / "objects"

    (objects / "README.txt").write_text("hello", encoding="utf-8")
    bad_prefix = objects / "zz"
    bad_prefix.mkdir()
    (bad_prefix / "whatever").write_text("x", encoding="utf-8")
    prefix = objects / "aa"
    prefix.mkdir(exist_ok=True)
    (prefix / ("c" * 63)).write_text("short", encoding="utf-8")
    deep = prefix / ("d" * 62)
    deep.mkdir()
    (deep / "inner.txt").write_text("nested", encoding="utf-8")

    scan = StoreScan.scan(store)
    reasons = {issue.reason for issue in scan.object_issues}
    assert {"non-digest-name", "unexpected-entry"} <= reasons

    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.object_issues
    assert root.content_sha256 in plan.reachable_objects
    assert candidate_digests(plan) == set()


def test_symlink_escape_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "symlink-target"
    link = tmp_path / "symlink-link"
    target.write_bytes(b"x")
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    prefix = store.root / "objects" / "ab"
    prefix.mkdir(exist_ok=True)
    escaped_name = "e" * 62
    os.symlink(target, prefix / escaped_name)

    scan = StoreScan.scan(store)
    assert any(issue.reason == "symlink-entry" for issue in scan.object_issues)

    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert "ab" + escaped_name not in candidate_digests(plan)
    assert (prefix / escaped_name).is_symlink()


def test_snapshot_and_plan_digests_are_stable(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    store.put(b"orphan bytes")

    first = StoreScan.scan(store)
    second = StoreScan.scan(store)
    assert first.snapshot_digest == second.snapshot_digest
    assert first.objects_digest == second.objects_digest
    assert first.manifests_digest == second.manifests_digest

    plan_first = first.mark_roots([manifest_digest(root)]).build_plan(store)
    plan_second = second.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan_first.plan_digest == plan_second.plan_digest
    assert plan_first.to_value() == plan_second.to_value()


def test_root_order_and_duplicates_are_irrelevant(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content_a = canonical_json_bytes({"run": "a"})
    content_b = canonical_json_bytes({"run": "b"})
    root_a = put_artifact(store, content_a, tag="tests/a.json")
    root_b = put_artifact(store, content_b, tag="tests/b.json")
    roots = [manifest_digest(root_a), manifest_digest(root_b)]

    forward = build_gc_plan(store, roots)
    backward = build_gc_plan(store, list(reversed(roots)))
    duplicated = build_gc_plan(store, [roots[0], roots[1], roots[0]])

    assert forward.plan_digest == backward.plan_digest == duplicated.plan_digest
    assert forward.unreferenced_objects == backward.unreferenced_objects
    assert forward.unreferenced_objects == duplicated.unreferenced_objects
    assert forward.roots == backward.roots == duplicated.roots


def test_stale_snapshot_is_detected(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    snapshot = StoreScan.scan(store)
    indexed = snapshot.mark_roots([manifest_digest(root)])

    put_artifact(store, canonical_json_bytes({"run": "later"}), tag="tests/later.json")

    assert not indexed.is_fresh(store)
    with pytest.raises(StaleScanError, match="re-scan"):
        indexed.build_plan(store)

    fresh = StoreScan.scan(store)
    assert fresh.snapshot_digest != snapshot.snapshot_digest
    plan = fresh.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.plan_digest


def test_fake_digest_object_is_corrupt_not_unreferenced(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    fake_digest = "f" * 64
    fake_path = store.root / "objects" / fake_digest[:2] / fake_digest[2:]
    fake_path.parent.mkdir(parents=True, exist_ok=True)
    fake_path.write_bytes(b"fake bytes that do not hash to the filename")

    scan = StoreScan.scan(store)
    assert fake_digest in scan.corrupt_objects

    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert fake_digest not in plan.unreferenced_objects
    assert fake_digest not in candidate_digests(plan)


def test_tmp_orphans_are_ignored(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    tmp = store.root / ".tmp"
    (tmp / "tmp-orphan-1").write_bytes(b"leftover")
    (tmp / "tmp-orphan-2").write_bytes(b"leftover")

    scan = StoreScan.scan(store)
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)

    assert plan.candidates == ()
    assert plan.unreferenced_objects == frozenset()
    assert scan.snapshot_digest


def test_run_manifest_artifact_reachability(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content_a = canonical_json_bytes({"artifact": "a"})
    content_b = canonical_json_bytes({"artifact": "b"})
    artifact_a = put_artifact(store, content_a, tag="tests/a.json")
    artifact_b = put_artifact(store, content_b, tag="tests/b.json")
    run_content = canonical_json_bytes({"run": "aggregate"})
    store.put(run_content)
    run = run_for(run_content, (artifact_a, artifact_b))
    root = put_run_record(store, run)
    orphan = store.put(canonical_json_bytes({"orphan": True}))

    plan = build_gc_plan(store, [root])

    assert plan.reachable_objects == frozenset(
        {
            content_sha256(run_content),
            artifact_a.content_sha256,
            artifact_b.content_sha256,
        }
    )
    assert manifest_digest(artifact_a) not in plan.unreferenced_manifests
    assert manifest_digest(artifact_b) not in plan.unreferenced_manifests
    assert plan.unreferenced_manifests == frozenset()
    assert plan.unreferenced_objects == frozenset({orphan})
    assert plan.candidates == (GcCandidate("object", orphan),)


def test_unreferenced_manifest_record_is_candidate(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    other = put_artifact(store, canonical_json_bytes({"run": "other"}), tag="tests/other.json")

    plan = build_gc_plan(store, [manifest_digest(root)])

    assert manifest_digest(other) in plan.unreferenced_manifests
    assert GcCandidate("manifest", manifest_digest(other)) in plan.candidates
    assert plan.unreferenced_objects == frozenset({other.content_sha256})
    assert GcCandidate("object", other.content_sha256) in plan.candidates


def test_scan_is_read_only(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")

    def snapshot() -> list[str]:
        return sorted(path.relative_to(store.root).as_posix() for path in store.root.rglob("*"))

    before = snapshot()
    StoreScan.scan(store)
    after = snapshot()

    assert before == after


def test_run_manifest_with_corrupt_artifact_object(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    content_a = canonical_json_bytes({"artifact": "a"})
    artifact_a = put_artifact(store, content_a, tag="tests/a.json")
    run_content = canonical_json_bytes({"run": "aggregate"})
    store.put(run_content)
    run = run_for(run_content, (artifact_a,))
    root = put_run_record(store, run)
    store._object_path(artifact_a.content_sha256).write_bytes(b"torn bytes")

    plan = build_gc_plan(store, [root])

    assert artifact_a.content_sha256 in plan.corrupt_objects
    assert artifact_a.content_sha256 not in plan.reachable_objects
    assert artifact_a.content_sha256 not in plan.missing_objects
    assert artifact_a.content_sha256 not in candidate_digests(plan)
    assert content_sha256(run_content) in plan.reachable_objects


def test_check_lock_bypass_is_removed(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    with pytest.raises(TypeError):
        StoreScan.scan(store, check_lock=False)  # type: ignore
    with pytest.raises(TypeError):
        build_gc_plan(store, ["0" * 64], check_lock=False)  # type: ignore


def test_build_plan_has_no_freshness_bypass(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    marked = StoreScan.scan(store).mark_roots([manifest_digest(root)])

    with pytest.raises(TypeError):
        cast(Any, marked.build_plan)()
    with pytest.raises(TypeError):
        cast(Any, marked.build_plan)(store, recheck=False)


def test_transient_writer_generation_blocks_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    original = artifact_index_module._scan_objects

    def scan_then_write(objects_root: Path, objects_root_resolved: Path):
        result = original(objects_root, objects_root_resolved)
        store.put(b"writer-completed-entirely-during-scan")
        return result

    monkeypatch.setattr(artifact_index_module, "_scan_objects", scan_then_write)
    scan = StoreScan.scan(store)

    assert scan.blocked
    assert scan.generation_digest


def test_invalid_generation_marker_blocks_scan(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    (store.root / ".state" / "generation").write_text("not-canonical", encoding="ascii")

    scan = StoreScan.scan(store)
    assert scan.blocked
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_lock_symlink_blocks_without_takeover(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    lock_path = store.root / ".locks" / "writer.lock"
    target = tmp_path / "lock-target"
    target.write_text("live:owner\n", encoding="ascii")
    try:
        os.symlink(target, lock_path)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    scan = StoreScan.scan(store)
    assert scan.blocked
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()
    assert lock_path.is_symlink()
    assert target.read_text(encoding="ascii") == "live:owner\n"


def test_dangling_lock_symlink_blocks(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    lock_path = store.root / ".locks" / "writer.lock"
    try:
        os.symlink(tmp_path / "does-not-exist", lock_path)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    scan = StoreScan.scan(store)
    assert scan.blocked
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_lock_content_change_changes_snapshot_token(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    lock = store.root / ".locks" / "writer.lock"
    lock.write_text("token-a\n", encoding="ascii")

    first = StoreScan.scan(store)
    assert first.blocked
    lock.write_text("token-b\n", encoding="ascii")
    second = StoreScan.scan(store)

    assert second.blocked
    assert first.lock_digest != second.lock_digest
    assert first.snapshot_digest != second.snapshot_digest
    assert not first.is_fresh(store)


def test_lock_appearing_after_snapshot_blocks_rechecked_plan(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    snapshot = StoreScan.scan(store)
    assert not snapshot.blocked
    indexed = snapshot.mark_roots([manifest_digest(root)])

    lock = store.root / ".locks" / "writer.lock"
    lock.write_text("live:owner\n", encoding="ascii")

    plan = indexed.build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()
    assert lock.exists()


def test_objects_root_symlink_blocks_and_never_scans_outside(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    outside = tmp_path / "outside-objects"
    outside.mkdir()
    (outside / "ab").mkdir()
    (outside / "ab" / ("c" * 62)).write_bytes(b"outside bytes")
    objects_root = store.root / "objects"
    objects_root.rmdir()
    try:
        os.symlink(outside, objects_root)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    scan = StoreScan.scan(store)
    assert scan.blocked
    assert any(issue.reason == "symlink-root" for issue in scan.object_issues)
    assert scan.objects == frozenset()
    assert scan.corrupt_objects == frozenset()
    plan = scan.build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()
    assert (outside / "ab" / ("c" * 62)).read_bytes() == b"outside bytes"


def test_objects_root_inside_symlink_blocks(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    inside = store.root / "real-objects"
    inside.mkdir()
    objects_root = store.root / "objects"
    objects_root.rmdir()
    try:
        os.symlink(inside, objects_root)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    scan = StoreScan.scan(store)
    assert scan.blocked
    assert any(issue.reason == "symlink-root" for issue in scan.object_issues)
    plan = scan.build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_manifests_root_dangling_symlink_blocks(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    manifests_root = store.root / "manifests"
    manifests_root.rmdir()
    try:
        os.symlink(tmp_path / "missing-manifests", manifests_root)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    scan = StoreScan.scan(store)
    assert scan.blocked
    assert any(issue.reason == "symlink-root" for issue in scan.invalid_manifests)
    assert scan.manifests == frozenset()
    plan = scan.build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_manifests_root_reparse_point_blocks_without_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    manifests_root = store.root / "manifests"
    original_lstat = Path.lstat
    original_iterdir = Path.iterdir
    reparse_flag = 0x400
    monkeypatch.setattr(artifact_index_module.stat, "FILE_ATTRIBUTE_REPARSE_POINT", reparse_flag)

    def fake_lstat(path: Path) -> os.stat_result:
        result = original_lstat(path)
        if path == manifests_root:
            return cast(
                os.stat_result,
                SimpleNamespace(st_mode=result.st_mode, st_file_attributes=reparse_flag),
            )
        return result

    def guarded_iterdir(path: Path):
        if path == manifests_root:
            raise AssertionError("reparse-point manifests root must not be traversed")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)

    scan = StoreScan.scan(store)
    assert scan.blocked
    assert any(
        issue.path == "manifests" and issue.reason == "symlink-root"
        for issue in scan.invalid_manifests
    )
    assert scan.manifests == frozenset()
    plan = scan.build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def _create_windows_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation is not permitted here: {result.stderr.strip()}")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_manifests_root_windows_junction_blocks_without_scanning_outside(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    outside = tmp_path / "outside-manifests"
    outside.mkdir()
    body = artifact_for(
        canonical_json_bytes({"run": "outside-decoy"}), tag="outside/decoy.json"
    ).to_dict()
    decoy_digest = content_sha256(to_json_value(body))
    decoy = outside / f"{decoy_digest}.json"
    decoy.write_bytes(canonical_json_bytes(body))

    manifests_root = store.root / "manifests"
    manifests_root.rmdir()
    try:
        _create_windows_junction(manifests_root, outside)

        scan = StoreScan.scan(store)
        assert scan.blocked
        assert any(
            issue.path == "manifests" and issue.reason == "symlink-root"
            for issue in scan.invalid_manifests
        )
        assert scan.manifests == frozenset()
        plan = scan.build_plan(store)
        assert plan.blocked
        assert plan.candidates == ()
        assert decoy.read_bytes() == canonical_json_bytes(body)
    finally:
        if os.path.isjunction(manifests_root):
            # Remove only the junction link; the outside target must survive.
            os.rmdir(manifests_root)

    assert not manifests_root.exists()
    assert decoy.exists()


def test_invalid_json_manifest_is_invalid_and_blocks(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    raw = b'{"broken": '
    digest = hashlib.sha256(raw).hexdigest()
    (store.root / "manifests" / f"{digest}.json").write_bytes(raw)

    scan = StoreScan.scan(store)
    assert any(issue.reason == "invalid-json" for issue in scan.invalid_manifests)
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_non_canonical_manifest_is_invalid_and_blocks(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    body = artifact_for(
        canonical_json_bytes({"run": "noncanonical"}), tag="tests/noncanonical.json"
    ).to_dict()
    canonical = canonical_json_bytes(body)
    raw = canonical.replace(b"{", b"{ ", 1)
    assert raw != canonical
    digest = hashlib.sha256(raw).hexdigest()
    (store.root / "manifests" / f"{digest}.json").write_bytes(raw)

    scan = StoreScan.scan(store)
    assert any(issue.reason == "non-canonical" for issue in scan.invalid_manifests)
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_object_prefix_symlink_is_classified_and_not_scanned(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    objects = store.root / "objects"
    target = objects / "zz"
    target.mkdir()
    (target / ("f" * 62)).write_bytes(b"inside bytes")
    root_prefix = root.content_sha256[:2]
    other_prefix = "ab" if root_prefix != "ab" else "cd"
    try:
        os.symlink(target, objects / other_prefix)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    scan = StoreScan.scan(store)
    assert any(issue.reason == "symlink-entry" for issue in scan.object_issues)
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert not plan.blocked
    assert other_prefix + ("f" * 62) not in candidate_digests(plan)


def test_object_prefix_reparse_point_is_classified_without_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    for index in range(1024):
        payload = f"prefix-reparse-orphan-{index}".encode()
        orphan = content_sha256(payload)
        if orphan[:2] != root.content_sha256[:2]:
            store.put(payload)
            break
    else:
        raise AssertionError("could not find an object with a distinct prefix")
    prefix = store.root / "objects" / orphan[:2]
    original_lstat = Path.lstat
    original_listdir = os.listdir
    reparse_flag = 0x400
    monkeypatch.setattr(artifact_index_module.stat, "FILE_ATTRIBUTE_REPARSE_POINT", reparse_flag)

    def fake_lstat(path: Path) -> os.stat_result:
        result = original_lstat(path)
        if path == prefix:
            return cast(
                os.stat_result,
                SimpleNamespace(st_mode=result.st_mode, st_file_attributes=reparse_flag),
            )
        return result

    def guarded_listdir(path: str | os.PathLike[str]) -> list[str]:
        if Path(path) == prefix:
            raise AssertionError("reparse-point prefix must not be traversed")
        return original_listdir(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    monkeypatch.setattr(os, "listdir", guarded_listdir)

    scan = StoreScan.scan(store)
    assert any(
        issue.path == orphan[:2] and issue.reason == "symlink-entry" for issue in scan.object_issues
    )
    assert orphan not in scan.objects
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert not plan.blocked
    assert orphan not in candidate_digests(plan)


def test_object_child_reparse_point_is_classified_without_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    orphan = store.put(b"child-reparse-orphan")
    child = store._object_path(orphan)
    original_lstat = Path.lstat
    original_read_bytes = Path.read_bytes
    reparse_flag = 0x400
    monkeypatch.setattr(artifact_index_module.stat, "FILE_ATTRIBUTE_REPARSE_POINT", reparse_flag)

    def fake_lstat(path: Path) -> os.stat_result:
        result = original_lstat(path)
        if path == child:
            return cast(
                os.stat_result,
                SimpleNamespace(st_mode=result.st_mode, st_file_attributes=reparse_flag),
            )
        return result

    def guarded_read_bytes(path: Path) -> bytes:
        if path == child:
            raise AssertionError("reparse-point child must not be read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    scan = StoreScan.scan(store)
    assert any(
        issue.digest == orphan and issue.reason == "symlink-entry" for issue in scan.object_issues
    )
    assert orphan not in scan.objects
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert not plan.blocked
    assert orphan not in candidate_digests(plan)


def test_pretty_json_manifest_is_invalid_and_blocks(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    body = artifact_for(canonical_json_bytes({"run": "pretty"}), tag="tests/pretty.json").to_dict()
    digest = content_sha256(to_json_value(body))
    (store.root / "manifests" / f"{digest}.json").write_text(
        json.dumps(body, indent=2), encoding="utf-8"
    )

    scan = StoreScan.scan(store)
    assert any(issue.reason == "raw-hash-mismatch" for issue in scan.invalid_manifests)
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_trailing_newline_manifest_is_invalid_and_blocks(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    body = artifact_for(
        canonical_json_bytes({"run": "newline"}), tag="tests/newline.json"
    ).to_dict()
    digest = content_sha256(to_json_value(body))
    (store.root / "manifests" / f"{digest}.json").write_bytes(canonical_json_bytes(body) + b"\n")

    scan = StoreScan.scan(store)
    assert any(issue.reason == "raw-hash-mismatch" for issue in scan.invalid_manifests)
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_txt_pseudo_manifest_is_invalid_and_blocks(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    body = artifact_for(canonical_json_bytes({"run": "txt"}), tag="tests/txt.json").to_dict()
    digest = content_sha256(to_json_value(body))
    (store.root / "manifests" / f"{digest}.txt").write_bytes(canonical_json_bytes(body))

    scan = StoreScan.scan(store)
    assert any(issue.reason == "wrong-name" for issue in scan.invalid_manifests)
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_raw_hash_mismatch_manifest_is_invalid_and_blocks(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    body = artifact_for(
        canonical_json_bytes({"run": "mismatch"}), tag="tests/mismatch.json"
    ).to_dict()
    digest = content_sha256(to_json_value(body))
    (store.root / "manifests" / f"{digest}.json").write_bytes(b"not the digest payload")

    scan = StoreScan.scan(store)
    assert any(issue.reason == "raw-hash-mismatch" for issue in scan.invalid_manifests)
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_pseudo_run_missing_and_extra_fields_are_invalid_and_block(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    content_a = canonical_json_bytes({"artifact": "a"})
    artifact_a = put_artifact(store, content_a, tag="tests/a.json")
    run = run_for(canonical_json_bytes({"run": "aggregate"}), (artifact_a,)).to_dict()

    missing = {key: value for key, value in run.items() if key != "generator_version"}
    missing_digest = content_sha256(to_json_value(missing))
    (store.root / "manifests" / f"{missing_digest}.json").write_bytes(canonical_json_bytes(missing))

    extra = {**run, "extra": 1}
    extra_digest = content_sha256(to_json_value(extra))
    (store.root / "manifests" / f"{extra_digest}.json").write_bytes(canonical_json_bytes(extra))

    scan = StoreScan.scan(store)
    assert len(scan.invalid_manifests) == 2
    assert all(issue.reason == "invalid-record" for issue in scan.invalid_manifests)
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_invalid_manifest_referencing_verified_object_blocks_plan(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    referenced_content = canonical_json_bytes({"referenced": True})
    referenced_digest = store.put(referenced_content)
    body = artifact_for(referenced_content, tag="tests/referenced.json").to_dict()
    digest = content_sha256(to_json_value(body))
    (store.root / "manifests" / f"{digest}.json").write_text(
        json.dumps(body, indent=2), encoding="utf-8"
    )

    plan = build_gc_plan(store, [manifest_digest(root)])
    assert plan.blocked
    assert referenced_digest not in candidate_digests(plan)
    assert plan.unreferenced_objects == frozenset()
    assert plan.reachable_objects == frozenset()


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", ""),
        ("generator_version", "   "),
        ("provenance", {}),
        ("source_build_sha256", "not-a-digest"),
    ],
)
def test_run_manifest_rejects_invalid_shared_scalars(
    tmp_path: Path, field: str, value: JsonValue
) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    run = run_for(canonical_json_bytes({"run": "strict"}), (root,)).to_dict()
    run[field] = value
    digest = content_sha256(to_json_value(run))
    (store.root / "manifests" / f"{digest}.json").write_bytes(canonical_json_bytes(run))

    scan = StoreScan.scan(store)
    assert any(
        issue.digest == digest and issue.reason == "invalid-record"
        for issue in scan.invalid_manifests
    )
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_artifact_manifest_rejects_normalized_padded_payload(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    payload = root.to_dict()
    payload["schema_version"] = "  arena.lab.artifact.v1  "
    payload["generator_version"] = "  0.1.0  "
    payload["provenance"] = {" source ": " tests/padded.json "}
    digest = content_sha256(to_json_value(payload))
    (store.root / "manifests" / f"{digest}.json").write_bytes(canonical_json_bytes(payload))

    scan = StoreScan.scan(store)
    assert any(
        issue.digest == digest and issue.reason == "invalid-record"
        for issue in scan.invalid_manifests
    )


def test_invalid_manifest_raw_change_changes_snapshot_digest(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    path = store.root / "manifests" / f"{'f' * 64}.json"
    path.write_bytes(b"A")
    first = StoreScan.scan(store)
    path.write_bytes(b"B")
    second = StoreScan.scan(store)

    assert first.invalid_manifests == second.invalid_manifests
    assert first.snapshot_digest != second.snapshot_digest
    assert first.manifests_digest != second.manifests_digest
    assert manifest_digest(root) in first.manifests


def test_verified_manifest_metadata_change_changes_snapshot_digest(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    path = store.root / "manifests" / f"{manifest_digest(root)}.json"
    first = StoreScan.scan(store)
    current = path.stat()
    os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 10_000_000))
    second = StoreScan.scan(store)

    assert first.manifests == second.manifests
    assert first.snapshot_digest != second.snapshot_digest
    assert first.manifests_digest != second.manifests_digest


def test_manifest_symlink_and_directory_entries_are_invalid(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    manifests = store.root / "manifests"
    body = artifact_for(canonical_json_bytes({"run": "linked"}), tag="tests/linked.json").to_dict()
    digest = content_sha256(to_json_value(body))
    target = manifests / "target.json"
    target.write_bytes(canonical_json_bytes(body))
    try:
        os.symlink(target, manifests / f"{digest}.json")
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")
    (manifests / f"{'d' * 64}.json").mkdir()

    scan = StoreScan.scan(store)
    reasons = {issue.reason for issue in scan.invalid_manifests}
    assert {"symlink-entry", "unexpected-entry", "wrong-name"} <= reasons
    plan = scan.mark_roots([manifest_digest(root)]).build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_manifest_tampering_after_snapshot_blocks_rechecked_plan(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    root_digest = manifest_digest(root)
    snapshot = StoreScan.scan(store)
    indexed = snapshot.mark_roots([root_digest])

    manifest_path = store.root / "manifests" / f"{root_digest}.json"
    manifest_path.write_text('{"schema_version":"arena.lab.artifact.v1"}', encoding="utf-8")

    plan = indexed.build_plan(store)
    assert plan.blocked
    assert plan.candidates == ()


def test_gcplan_deep_copies_mutable_inputs(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    orphan = store.put(b"orphan bytes")
    plan = build_gc_plan(store, [manifest_digest(root)])

    mutable_roots: list[str] = [manifest_digest(root)]
    mutable_unreferenced: list[str] = [orphan]
    mutable_candidates: list[GcCandidate] = [GcCandidate("object", orphan)]
    rebuilt = GcPlan(
        snapshot_digest=plan.snapshot_digest,
        roots=mutable_roots,  # type: ignore
        reachable_objects=frozenset(plan.reachable_objects),
        unreferenced_objects=mutable_unreferenced,  # type: ignore
        unreferenced_manifests=frozenset(plan.unreferenced_manifests),
        corrupt_objects=frozenset(plan.corrupt_objects),
        missing_objects=frozenset(plan.missing_objects),
        invalid_manifests=tuple(plan.invalid_manifests),
        object_issues=tuple(plan.object_issues),
        candidates=mutable_candidates,  # type: ignore
        blocked=False,
    )
    before = rebuilt.to_value()
    mutable_roots.append("1" * 64)
    mutable_unreferenced.append("2" * 64)
    mutable_candidates.append(GcCandidate("manifest", "3" * 64))
    assert rebuilt.to_value() == before
    assert rebuilt.roots == frozenset({manifest_digest(root)})
    assert rebuilt.unreferenced_objects == frozenset({orphan})
    assert rebuilt.candidates == (GcCandidate("object", orphan),)
    assert rebuilt.plan_digest == content_sha256(to_json_value(rebuilt.to_value()))


def test_gcplan_normalizes_ordering_and_duplicates(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    orphan = store.put(b"orphan bytes")
    plan = build_gc_plan(store, [manifest_digest(root)])

    unordered_roots: list[str] = [orphan, manifest_digest(root), orphan]
    unordered_unreferenced: list[str] = [orphan, orphan]
    unordered_candidates: list[GcCandidate] = [
        GcCandidate("manifest", orphan),
        GcCandidate("object", orphan),
    ]
    rebuilt = GcPlan(
        snapshot_digest=plan.snapshot_digest,
        roots=unordered_roots,  # type: ignore
        reachable_objects=frozenset(),
        unreferenced_objects=unordered_unreferenced,  # type: ignore
        unreferenced_manifests=frozenset(),
        corrupt_objects=frozenset(),
        missing_objects=frozenset(),
        invalid_manifests=(),
        object_issues=(),
        candidates=unordered_candidates,  # type: ignore
        blocked=False,
    )
    assert rebuilt.roots == frozenset({orphan, manifest_digest(root)})
    assert rebuilt.unreferenced_objects == frozenset({orphan})
    assert rebuilt.candidates == (
        GcCandidate("manifest", orphan),
        GcCandidate("object", orphan),
    )
    assert rebuilt.plan_digest == content_sha256(to_json_value(rebuilt.to_value()))


def test_gcplan_rejects_invalid_candidates() -> None:
    with pytest.raises(ArtifactIndexError, match="candidate kind"):
        GcCandidate("bogus", "0" * 64)
    with pytest.raises(ArtifactIndexError, match="candidate digest"):
        GcCandidate("object", "not-a-digest")
    with pytest.raises(ArtifactIndexError, match="candidates must contain"):
        GcPlan(
            snapshot_digest="0" * 64,
            roots=frozenset(),
            reachable_objects=frozenset(),
            unreferenced_objects=frozenset(),
            unreferenced_manifests=frozenset(),
            corrupt_objects=frozenset(),
            missing_objects=frozenset(),
            invalid_manifests=(),
            object_issues=(),
            candidates=("object",),  # type: ignore
            blocked=False,
        )


def test_store_scan_normalizes_and_deep_copies_inputs(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "store")
    root = put_artifact(store, canonical_json_bytes({"run": "root"}), tag="tests/root.json")
    root_digest = manifest_digest(root)

    mutable_objects: list[str] = ["0" * 64, root.content_sha256]
    mutable_manifests: list[str] = [root_digest]
    scan = StoreScan(
        store_root=store.root.as_posix(),
        objects=mutable_objects,  # type: ignore
        corrupt_objects=frozenset(),
        object_issues=(),
        manifests=mutable_manifests,  # type: ignore
        invalid_manifests=(),
        blocked=False,
        _references={},
        roots=frozenset(),
    )
    before = scan.snapshot_digest
    mutable_objects.append("1" * 64)
    mutable_manifests.append("2" * 64)
    assert scan.objects == frozenset({root.content_sha256, "0" * 64})
    assert scan.manifests == frozenset({root_digest})
    assert scan.snapshot_digest == before
    assert scan.lock_digest == ""
    assert scan.blocked is False
