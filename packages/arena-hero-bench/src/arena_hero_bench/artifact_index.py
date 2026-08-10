"""Derived artifact index and read-only garbage-collection planning for a store.

This module builds a *derived, rebuildable, non-authoritative* view of the
content-addressed store: which verified objects and manifest records exist,
which are corrupt, missing, invalid, or unreferenced, and -- given an explicit
root set -- what a dry-run garbage-collection plan would look like.

Guarantees
----------
- Read-only by construction: scanning never creates, replaces, or deletes any
  file, never acquires or touches the writer lock, and never steals a live
  lock. The module contains no delete, apply, or write path at all; a
  :class:`GcPlan` is analysis output only and is never executable.
- Fail closed: a non-empty root set is required before reachability is
  computed, a live ``writer.lock`` marks the plan ``blocked`` with empty
  candidates, and corrupt, missing, or invalid entries are classified but
  never enter the candidate set.
- Deterministic: scan and plan digests depend only on the scanned content
  (sorted canonical JSON), never on enumeration order or root ordering.
- Stale detection: every scan snapshot carries a stable ``snapshot_digest``
  derived from the objects and manifests sets. ``build_plan(store=...)``
  re-checks the store and raises :class:`StaleScanError` when the store no
  longer matches the snapshot, so a snapshot cannot be silently reused after
  the store changed.

Layout expectations
-------------------
Objects live at ``<root>/objects/<2 hex>/<62 hex>`` (lowercase SHA-256) and
manifest records at ``<root>/manifests/<64 hex>.json``, matching
:class:`~arena_hero_bench.storage.FilesystemArtifactStore`. Anything that does
not fit the digest layout, or whose resolved path escapes the store root, is
classified as an issue and never treated as deletable. The ``.tmp`` directory
is never scanned.

Reachability
------------
Roots are *manifest record digests* (the 64-hex filename under ``manifests/``,
which is the SHA-256 of the canonical record serialization). Starting from the
roots, the reference graph is walked: each manifest record makes its
``content_sha256`` object reachable, and a run-style record (one carrying an
``artifacts`` list) also makes each artifact's ``content_sha256`` object and
artifact manifest record reachable, recursively.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Self

from arena_hero_bench.manifest import ArtifactManifest
from arena_hero_bench.storage import FilesystemArtifactStore
from arena_hero_sim.serialization import content_sha256, to_json_value

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEX2 = re.compile(r"^[0-9a-f]{2}$")
_HEX62 = re.compile(r"^[0-9a-f]{62}$")


class ArtifactIndexError(ValueError):
    """Base error for derived artifact index and plan operations."""


class StaleScanError(ArtifactIndexError):
    """The store changed after a snapshot was taken; re-scan before planning."""


@dataclass(frozen=True, slots=True)
class ObjectIssue:
    """One object entry that cannot be interpreted as a sealed object."""

    path: str
    reason: str
    digest: str | None = None

    def to_value(self) -> dict[str, object]:
        return {"path": self.path, "reason": self.reason, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class ManifestIssue:
    """One manifest entry that cannot be interpreted as a valid record."""

    path: str
    reason: str
    digest: str | None = None

    def to_value(self) -> dict[str, object]:
        return {"path": self.path, "reason": self.reason, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class GcCandidate:
    """One sealed, unreferenced object or manifest record in a plan."""

    kind: str
    digest: str

    def to_value(self) -> dict[str, str]:
        return {"kind": self.kind, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class _ManifestRef:
    """Parsed reference edges for one verified manifest record."""

    content_sha256: str
    artifact_objects: tuple[str, ...] = ()
    artifact_records: tuple[str, ...] = ()


def _store_relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _scan_objects(
    objects_root: Path, objects_root_resolved: Path
) -> tuple[set[str], set[str], list[ObjectIssue]]:
    """Enumerate the two-level object layout, verifying every candidate.

    Returns (verified digests, corrupt digests, issues). Entries that do not
    fit ``<2 hex>/<62 hex>`` or whose resolved path escapes ``objects/`` are
    issues; corrupt entries are present under a digest name but do not verify.
    """
    verified: set[str] = set()
    corrupt: set[str] = set()
    issues: list[ObjectIssue] = []
    if not objects_root.is_dir():
        return verified, corrupt, issues
    try:
        prefixes = sorted(objects_root.iterdir(), key=lambda entry: entry.name)
    except OSError:
        issues.append(ObjectIssue("objects", "unreadable-directory", None))
        return verified, corrupt, issues
    for prefix in prefixes:
        rel = _store_relative(objects_root, prefix)
        if not _HEX2.fullmatch(prefix.name):
            issues.append(ObjectIssue(rel, "non-digest-name", None))
            continue
        resolved_prefix = prefix.resolve()
        if not resolved_prefix.is_relative_to(objects_root_resolved):
            issues.append(ObjectIssue(rel, "escaped-path", None))
            continue
        if not prefix.is_dir():
            issues.append(ObjectIssue(rel, "unexpected-entry", None))
            continue
        try:
            names = sorted(os.listdir(prefix))
        except OSError:
            issues.append(ObjectIssue(rel, "unreadable-directory", None))
            continue
        for name in names:
            child = prefix / name
            child_rel = f"{rel}/{name}"
            if not _HEX62.fullmatch(name):
                issues.append(ObjectIssue(child_rel, "non-digest-name", None))
                continue
            digest = f"{prefix.name}{name}"
            resolved = child.resolve()
            if not resolved.is_relative_to(objects_root_resolved):
                issues.append(ObjectIssue(child_rel, "escaped-path", digest))
                continue
            if not resolved.is_file():
                issues.append(ObjectIssue(child_rel, "unexpected-entry", digest))
                continue
            try:
                payload = resolved.read_bytes()
            except OSError:
                corrupt.add(digest)
                continue
            if hashlib.sha256(payload).hexdigest() == digest:
                verified.add(digest)
            else:
                corrupt.add(digest)
    return verified, corrupt, issues


def _parse_manifest_ref(payload: Mapping[str, object]) -> _ManifestRef | None:
    """Interpret a digest-verified manifest payload as reference edges."""
    content = payload.get("content_sha256")
    if not isinstance(content, str) or not _SHA256.fullmatch(content):
        return None
    if "artifacts" not in payload:
        try:
            ArtifactManifest.from_dict(payload)
        except ValueError:
            return None
        return _ManifestRef(content_sha256=content)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    artifact_objects: list[str] = []
    artifact_records: list[str] = []
    for item in artifacts:
        if not isinstance(item, Mapping):
            return None
        item_content = item.get("content_sha256")
        if not isinstance(item_content, str) or not _SHA256.fullmatch(item_content):
            return None
        try:
            ArtifactManifest.from_dict(item)
        except ValueError:
            return None
        artifact_objects.append(item_content)
        artifact_records.append(content_sha256(to_json_value(item)))
    return _ManifestRef(
        content_sha256=content,
        artifact_objects=tuple(artifact_objects),
        artifact_records=tuple(artifact_records),
    )


def _scan_manifests(
    manifests_root: Path, manifests_root_resolved: Path, store_root: Path
) -> tuple[set[str], dict[str, _ManifestRef], list[ManifestIssue]]:
    """Enumerate manifest records, classifying every entry.

    Returns (verified record digests, parsed references, issues). A record is
    verified when its filename stem is a legal SHA-256 and equals the SHA-256
    of the canonical serialization of its parsed payload; run-style records
    (with an ``artifacts`` list) are accepted alongside artifact records.
    """
    verified: set[str] = set()
    references: dict[str, _ManifestRef] = {}
    issues: list[ManifestIssue] = []
    if not manifests_root.is_dir():
        return verified, references, issues
    try:
        entries = sorted(manifests_root.iterdir(), key=lambda entry: entry.name)
    except OSError:
        issues.append(ManifestIssue("manifests", "unreadable-directory", None))
        return verified, references, issues
    for path in entries:
        rel = _store_relative(store_root, path)
        resolved = path.resolve()
        if not resolved.is_relative_to(manifests_root_resolved):
            issues.append(ManifestIssue(rel, "escaped-path", None))
            continue
        if not path.is_file():
            issues.append(ManifestIssue(rel, "unexpected-entry", None))
            continue
        stem = path.stem
        if not _SHA256.fullmatch(stem):
            issues.append(ManifestIssue(rel, "wrong-name", None))
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            issues.append(ManifestIssue(rel, "invalid-json", stem))
            continue
        if not isinstance(payload, Mapping):
            issues.append(ManifestIssue(rel, "invalid-record", stem))
            continue
        if content_sha256(payload) != stem:
            issues.append(ManifestIssue(rel, "content-digest-mismatch", stem))
            continue
        ref = _parse_manifest_ref(payload)
        if ref is None:
            issues.append(ManifestIssue(rel, "invalid-record", stem))
            continue
        verified.add(stem)
        references[stem] = ref
    return verified, references, issues


@dataclass(frozen=True, slots=True)
class StoreScan:
    """A frozen, derived snapshot of one store at one point in time.

    Construct via :meth:`scan`; the snapshot is immutable and carries stable
    digests over the scanned content. Roots are marked with :meth:`mark_roots`
    and plans are built with :meth:`build_plan`.
    """

    store_root: str
    objects: frozenset[str]
    corrupt_objects: frozenset[str]
    object_issues: tuple[ObjectIssue, ...]
    manifests: frozenset[str]
    invalid_manifests: tuple[ManifestIssue, ...]
    blocked: bool
    _references: Mapping[str, _ManifestRef] = field(repr=False, compare=False)
    roots: frozenset[str] = frozenset()
    objects_digest: str = field(init=False, repr=False)
    manifests_digest: str = field(init=False, repr=False)
    snapshot_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        objects_digest = content_sha256(
            to_json_value(
                {
                    "verified": sorted(self.objects),
                    "corrupt": sorted(self.corrupt_objects),
                    "issues": [issue.to_value() for issue in self.object_issues],
                }
            )
        )
        manifests_digest = content_sha256(
            to_json_value(
                {
                    "verified": sorted(self.manifests),
                    "invalid": [issue.to_value() for issue in self.invalid_manifests],
                }
            )
        )
        snapshot_digest = content_sha256(
            to_json_value({"objects": objects_digest, "manifests": manifests_digest})
        )
        object.__setattr__(self, "objects_digest", objects_digest)
        object.__setattr__(self, "manifests_digest", manifests_digest)
        object.__setattr__(self, "snapshot_digest", snapshot_digest)

    @classmethod
    def scan(cls, store: FilesystemArtifactStore, *, check_lock: bool = True) -> Self:
        """Scan a store read-only and return a frozen snapshot.

        ``check_lock`` controls whether a present ``.locks/writer.lock`` marks
        the snapshot (and later plans) as blocked; the lock is never acquired,
        taken over, or otherwise modified.
        """
        root = Path(store.root).resolve()
        objects_root = root / "objects"
        manifests_root = root / "manifests"
        lock_path = root / ".locks" / "writer.lock"
        blocked = check_lock and lock_path.exists()
        verified_objects, corrupt_objects, object_issues = _scan_objects(
            objects_root, objects_root.resolve()
        )
        manifest_digests, references, manifest_issues = _scan_manifests(
            manifests_root, manifests_root.resolve(), root
        )
        return cls(
            store_root=root.as_posix(),
            objects=frozenset(verified_objects),
            corrupt_objects=frozenset(corrupt_objects),
            object_issues=tuple(object_issues),
            manifests=frozenset(manifest_digests),
            invalid_manifests=tuple(manifest_issues),
            blocked=blocked,
            _references=MappingProxyType(references),
        )

    def mark_roots(self, roots: Iterable[str]) -> Self:
        """Mark manifest record digests as roots, returning a new snapshot.

        Roots must be verified manifest records present in this snapshot;
        duplicates and ordering are irrelevant. An empty root set raises
        :class:`ArtifactIndexError` so the whole store can never be planned as
        garbage.
        """
        normalized: list[str] = []
        seen: set[str] = set()
        for value in roots:
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ArtifactIndexError(
                    f"root manifest digest must be a lowercase SHA-256 digest: {value!r}"
                )
            if value in self.manifests:
                if value not in seen:
                    normalized.append(value)
                    seen.add(value)
            else:
                raise ArtifactIndexError(
                    f"root manifest digest is not a verified manifest in this snapshot: {value}"
                )
        if not normalized:
            raise ArtifactIndexError(
                "root set is empty; refusing to plan collection over the entire store"
            )
        return replace(self, roots=frozenset(normalized))

    def is_fresh(self, store: FilesystemArtifactStore) -> bool:
        """Return whether the store still matches this snapshot's content."""
        fresh = StoreScan.scan(store)
        return fresh.snapshot_digest == self.snapshot_digest

    def build_plan(
        self, store: FilesystemArtifactStore | None = None, *, recheck: bool = True
    ) -> GcPlan:
        """Compute a frozen, dry-run GC plan from this snapshot.

        When ``store`` is provided and ``recheck`` is true the store is
        re-scanned: a newly present writer lock yields a ``blocked`` plan, and
        any content change raises :class:`StaleScanError`. When ``store`` is
        omitted the plan is derived purely from the snapshot; callers are then
        responsible for snapshot freshness (use :meth:`is_fresh` or re-scan).
        """
        if self.blocked:
            return GcPlan.blocked_plan(self)
        if store is not None and recheck:
            fresh = StoreScan.scan(store)
            if fresh.blocked:
                return GcPlan.blocked_plan(self)
            if fresh.snapshot_digest != self.snapshot_digest:
                raise StaleScanError(
                    "store changed since snapshot "
                    f"({self.snapshot_digest} != {fresh.snapshot_digest}); re-scan before planning"
                )
        if not self.roots:
            raise ArtifactIndexError("root set is empty; call mark_roots before building a plan")
        reachable_objects, reachable_manifests, missing_objects = self._reachability()
        unreferenced_objects = frozenset(sorted(self.objects - reachable_objects))
        unreferenced_manifests = frozenset(sorted(self.manifests - reachable_manifests))
        candidates = tuple(
            sorted(
                [GcCandidate("object", digest) for digest in unreferenced_objects]
                + [GcCandidate("manifest", digest) for digest in unreferenced_manifests],
                key=lambda candidate: (candidate.kind, candidate.digest),
            )
        )
        return GcPlan(
            snapshot_digest=self.snapshot_digest,
            roots=self.roots,
            reachable_objects=frozenset(sorted(reachable_objects)),
            unreferenced_objects=unreferenced_objects,
            unreferenced_manifests=unreferenced_manifests,
            corrupt_objects=self.corrupt_objects,
            missing_objects=frozenset(sorted(missing_objects)),
            invalid_manifests=self.invalid_manifests,
            object_issues=self.object_issues,
            candidates=candidates,
            blocked=False,
        )

    def _reachability(self) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        """Walk the reference graph from roots.

        Returns (reachable objects, reachable manifest records, missing
        objects). A referenced object that is present but corrupt is not
        missing and never reachable; a referenced object that is absent is
        missing.
        """
        reachable_manifests: set[str] = set()
        stack = [digest for digest in self.roots if digest in self._references]
        while stack:
            digest = stack.pop()
            if digest in reachable_manifests:
                continue
            reachable_manifests.add(digest)
            for record in self._references[digest].artifact_records:
                if record in self._references and record not in reachable_manifests:
                    stack.append(record)
        reachable_objects: set[str] = set()
        missing_objects: set[str] = set()
        for digest in reachable_manifests:
            ref = self._references[digest]
            for obj in (ref.content_sha256, *ref.artifact_objects):
                if obj in self.objects:
                    reachable_objects.add(obj)
                elif obj not in self.corrupt_objects:
                    missing_objects.add(obj)
        return (
            frozenset(reachable_objects),
            frozenset(reachable_manifests),
            frozenset(missing_objects),
        )


@dataclass(frozen=True, slots=True)
class GcPlan:
    """A frozen, canonical, dry-run garbage-collection plan.

    Plans are analysis output only: nothing in this module deletes, rewrites,
    or applies anything. ``candidates`` contains only sealed (verified),
    unreferenced objects and manifest records; corrupt, missing, and invalid
    entries are reported separately and never become candidates.
    """

    snapshot_digest: str
    roots: frozenset[str]
    reachable_objects: frozenset[str]
    unreferenced_objects: frozenset[str]
    unreferenced_manifests: frozenset[str]
    corrupt_objects: frozenset[str]
    missing_objects: frozenset[str]
    invalid_manifests: tuple[ManifestIssue, ...]
    object_issues: tuple[ObjectIssue, ...]
    candidates: tuple[GcCandidate, ...]
    blocked: bool
    dry_run: bool = field(default=True, init=False)
    plan_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_digest", content_sha256(to_json_value(self.to_value())))

    def to_value(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation of this plan."""
        return {
            "snapshot_digest": self.snapshot_digest,
            "roots": sorted(self.roots),
            "reachable_objects": sorted(self.reachable_objects),
            "unreferenced_objects": sorted(self.unreferenced_objects),
            "unreferenced_manifests": sorted(self.unreferenced_manifests),
            "corrupt_objects": sorted(self.corrupt_objects),
            "missing_objects": sorted(self.missing_objects),
            "invalid_manifests": [issue.to_value() for issue in self.invalid_manifests],
            "object_issues": [issue.to_value() for issue in self.object_issues],
            "candidates": [candidate.to_value() for candidate in self.candidates],
            "blocked": self.blocked,
            "dry_run": True,
        }

    @classmethod
    def blocked_plan(cls, snapshot: StoreScan) -> GcPlan:
        """Return a fail-closed plan for a snapshot with a live writer lock."""
        return cls(
            snapshot_digest=snapshot.snapshot_digest,
            roots=snapshot.roots,
            reachable_objects=frozenset(),
            unreferenced_objects=frozenset(),
            unreferenced_manifests=frozenset(),
            corrupt_objects=snapshot.corrupt_objects,
            missing_objects=frozenset(),
            invalid_manifests=snapshot.invalid_manifests,
            object_issues=snapshot.object_issues,
            candidates=(),
            blocked=True,
        )


def build_gc_plan(
    store: FilesystemArtifactStore,
    roots: Iterable[str],
    *,
    check_lock: bool = True,
) -> GcPlan:
    """Scan a store, mark roots, and build a plan from one snapshot.

    This convenience path scans once; for re-validated freshness use the
    two-phase API (:meth:`StoreScan.scan` + :meth:`StoreScan.build_plan` with
    ``store``) so staleness is detected before planning.
    """
    scan = StoreScan.scan(store, check_lock=check_lock)
    return scan.mark_roots(roots).build_plan()
