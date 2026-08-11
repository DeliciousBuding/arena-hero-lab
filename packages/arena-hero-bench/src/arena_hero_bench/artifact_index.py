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
  computed; a present ``writer.lock`` (regular file or symlink, including a
  dangling symlink) marks the snapshot and any plan ``blocked`` with empty
  candidates; any invalid manifest record also blocks the plan because an
  unparsable record may hide references; corrupt, missing, or invalid entries
  are classified but never enter the candidate set.
- Layout containment: the store root, ``objects/``, and ``manifests/`` roots
  are all resolved and checked for containment; a layout root that is a
  symlink/reparse point or that resolves outside the store root marks the
  snapshot blocked and is never scanned. Entry symlinks and paths that escape
  the layout root are issues, never candidates.
- Deterministic: scan and plan digests depend only on scanned object content,
  every manifest entry's raw digest and lstat metadata, the raw writer-lock
  token, and the persistent writer generation token; enumeration and root
  ordering do not affect identity.
- Stale detection: every scan snapshot carries a stable ``snapshot_digest``.
  The ephemeral lock and persistent monotonic ``.state/generation`` marker are
  probed before and after enumeration. A writer that appears and disappears
  entirely during the scan still changes generation and blocks the snapshot.
  ``build_plan(store)`` always re-scans and raises
  :class:`StaleScanError` when the store no longer matches the snapshot, so a
  snapshot cannot be silently reused after the store changed.

Layout expectations
-------------------
Objects live at ``<root>/objects/<2 hex>/<62 hex>`` (lowercase SHA-256) and
manifest records at ``<root>/manifests/<64 hex>.json``, matching
:class:`~arena_hero_bench.storage.FilesystemArtifactStore`. Anything that does
not fit the digest layout, whose resolved path escapes the store root, or that
is not a regular file is classified as an issue and never treated as
deletable. The ``.tmp`` directory is never scanned.

Manifest strictness
-------------------
A manifest record is only *verified* when all of the following hold:

- the filename matches ``<64 hex>.json`` exactly (``.txt`` and other suffixes
  are invalid),
- the entry is a regular file (a symlink or directory entry is invalid),
- the SHA-256 of the *raw file bytes* equals the filename digest,
- the payload parses as JSON and its canonical serialization is byte-identical
  to the raw bytes (pretty JSON and non-canonical whitespace/newlines are
  invalid),
- the payload matches the strict artifact or run schema with no extra or
  missing fields and strictly typed values.

Any invalid manifest blocks the plan: an unparsable record may hide references
to otherwise-unreferenced objects, so no candidates are produced while one
exists.

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
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Self

from arena_hero_bench.manifest import ArtifactManifest, ArtifactStatus, RunManifest
from arena_hero_bench.storage import FilesystemArtifactStore
from arena_hero_sim.serialization import canonical_json_bytes, content_sha256, to_json_value

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEX2 = re.compile(r"^[0-9a-f]{2}$")
_HEX62 = re.compile(r"^[0-9a-f]{62}$")
_MANIFEST_NAME = re.compile(r"^[0-9a-f]{64}\.json$")

_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "generator_version",
        "provenance",
        "source_build_sha256",
        "content_sha256",
        "status",
        "publishable",
    }
)
_RUN_KEYS = _ARTIFACT_KEYS | frozenset({"artifacts"})
_STATUS_VALUES = frozenset({"complete", "partial", "failed"})


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

    def __post_init__(self) -> None:
        if self.kind not in {"object", "manifest"}:
            raise ArtifactIndexError(
                f"candidate kind must be 'object' or 'manifest': {self.kind!r}"
            )
        if not isinstance(self.digest, str) or not _SHA256.fullmatch(self.digest):
            raise ArtifactIndexError("candidate digest must be a lowercase SHA-256 digest")

    def to_value(self) -> dict[str, str]:
        return {"kind": self.kind, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class _ManifestRef:
    """Parsed reference edges for one verified manifest record."""

    content_sha256: str
    artifact_objects: tuple[str, ...] = ()
    artifact_records: tuple[str, ...] = ()


def _validated_digest_str(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ArtifactIndexError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _validated_digests(value: object, name: str) -> set[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray)):
        raise ArtifactIndexError(f"{name} must be an iterable of digests")
    return {_validated_digest_str(item, name) for item in value}


def _validated_content_hash(value: object) -> tuple[str, str]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ArtifactIndexError("content hashes must be (digest, file_sha256) pairs")
    digest, file_sha = value
    return _validated_digest_str(digest, "content hash digest"), _validated_digest_str(
        file_sha, "content hash value"
    )


def _coerce_object_issue(value: object) -> ObjectIssue:
    if not isinstance(value, ObjectIssue):
        raise ArtifactIndexError("object_issues must contain ObjectIssue instances")
    return value


def _coerce_manifest_issue(value: object) -> ManifestIssue:
    if not isinstance(value, ManifestIssue):
        raise ArtifactIndexError("invalid_manifests must contain ManifestIssue instances")
    return value


def _coerce_candidate(value: object) -> GcCandidate:
    if not isinstance(value, GcCandidate):
        raise ArtifactIndexError("candidates must contain GcCandidate instances")
    return value


def _object_issue_key(issue: ObjectIssue) -> tuple[str, str, str]:
    return (issue.path, issue.reason, issue.digest or "")


def _manifest_issue_key(issue: ManifestIssue) -> tuple[str, str, str]:
    return (issue.path, issue.reason, issue.digest or "")


def _store_relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_reparse_point(st: os.stat_result) -> bool:
    attributes = getattr(st, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse)


def _lock_token(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _probe_lock(root: Path) -> tuple[bool, str]:
    """Read-only probe of ``<root>/.locks/writer.lock``.

    Returns ``(blocked, token)``. Any present lock -- regular file or symlink,
    including a dangling symlink -- blocks. The token digests the raw file
    content plus lstat metadata, so a lock that appears, disappears, or
    changes content/metadata changes the token. Nothing is acquired, taken
    over, or otherwise modified.
    """
    locks_dir = root / ".locks"
    lock_path = locks_dir / "writer.lock"
    try:
        locks_st = locks_dir.lstat()
    except FileNotFoundError:
        return False, ""
    except OSError:
        return True, _lock_token({"anomaly": "unreadable-locks"})
    if stat.S_ISLNK(locks_st.st_mode) or _is_reparse_point(locks_st):
        return True, _lock_token({"anomaly": "locks-symlink"})
    if not stat.S_ISDIR(locks_st.st_mode):
        return True, _lock_token({"anomaly": "locks-not-directory"})
    try:
        st = lock_path.lstat()
    except FileNotFoundError:
        return False, ""
    except OSError:
        return True, _lock_token({"anomaly": "unreadable-lock"})
    if stat.S_ISLNK(st.st_mode) or _is_reparse_point(st):
        try:
            target = os.readlink(lock_path)
        except OSError:
            target = ""
        return True, _lock_token(
            {
                "type": "symlink",
                "target": target,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "ctime_ns": getattr(st, "st_ctime_ns", 0),
            }
        )
    try:
        raw = lock_path.read_bytes()
    except OSError:
        raw = b""
    return True, _lock_token(
        {
            "type": "file",
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "ctime_ns": getattr(st, "st_ctime_ns", 0),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    )


def _probe_generation(root: Path) -> tuple[bool, str]:
    """Read the persistent monotonic writer generation marker fail-closed."""

    state_dir = root / ".state"
    generation = state_dir / "generation"
    try:
        state_st = state_dir.lstat()
    except OSError:
        return True, _lock_token({"generation": "missing-state"})
    if stat.S_ISLNK(state_st.st_mode) or _is_reparse_point(state_st):
        return True, _lock_token({"generation": "state-symlink"})
    if not stat.S_ISDIR(state_st.st_mode):
        return True, _lock_token({"generation": "state-not-directory"})
    try:
        marker_st = generation.lstat()
    except OSError:
        return True, _lock_token({"generation": "missing-marker"})
    if stat.S_ISLNK(marker_st.st_mode) or _is_reparse_point(marker_st):
        return True, _lock_token({"generation": "marker-symlink"})
    if not stat.S_ISREG(marker_st.st_mode):
        return True, _lock_token({"generation": "marker-not-file"})
    try:
        raw = generation.read_bytes()
    except OSError:
        return True, _lock_token({"generation": "marker-unreadable"})
    if re.fullmatch(rb"(?:0|[1-9][0-9]*)\n", raw) is None:
        return True, _lock_token(
            {
                "generation": "marker-invalid",
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    token = _lock_token(
        {
            "generation": raw.decode("ascii").rstrip("\n"),
            "size": marker_st.st_size,
            "mtime_ns": marker_st.st_mtime_ns,
            "ctime_ns": marker_st.st_ctime_ns,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    return False, token


def _probe_layout_root(root: Path, name: str) -> tuple[Path, Path, str | None]:
    """Resolve a layout root and report containment/symlink issues.

    Returns ``(path, resolved, issue)``. A layout root that is absent is fine
    (an empty store). A root that is a symlink or reparse point, that is not a
    directory, or that resolves outside the store root is an issue; the caller
    must then treat the snapshot as blocked and never scan through it.
    """
    path = root / name
    try:
        st = path.lstat()
    except FileNotFoundError:
        return path, path, None
    except OSError:
        return path, path, "unreadable-root"
    if stat.S_ISLNK(st.st_mode) or _is_reparse_point(st):
        return path, path, "symlink-root"
    if not stat.S_ISDIR(st.st_mode):
        return path, path, "unexpected-root"
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        return path, resolved, "escaped-root"
    return path, resolved, None


def _scan_objects(
    objects_root: Path, objects_root_resolved: Path
) -> tuple[set[str], set[str], list[ObjectIssue], list[tuple[str, str]]]:
    """Enumerate the two-level object layout, verifying every candidate.

    Returns ``(verified, corrupt, issues, content_hashes)``. Verified entries
    hash to their digest name; corrupt entries are present under a digest name
    but do not verify. ``content_hashes`` carries ``(digest, actual_sha256)``
    for every readable digest-named file so raw content changes (even between
    two corrupt payloads) are reflected in the snapshot token.
    """
    verified: set[str] = set()
    corrupt: set[str] = set()
    issues: list[ObjectIssue] = []
    content_hashes: list[tuple[str, str]] = []
    if not objects_root.is_dir():
        return verified, corrupt, issues, content_hashes
    try:
        prefixes = sorted(objects_root.iterdir(), key=lambda entry: entry.name)
    except OSError:
        issues.append(ObjectIssue("objects", "unreadable-directory", None))
        return verified, corrupt, issues, content_hashes
    for prefix in prefixes:
        rel = _store_relative(objects_root, prefix)
        if not _HEX2.fullmatch(prefix.name):
            issues.append(ObjectIssue(rel, "non-digest-name", None))
            continue
        resolved_prefix = prefix.resolve()
        if not resolved_prefix.is_relative_to(objects_root_resolved):
            issues.append(ObjectIssue(rel, "escaped-path", None))
            continue
        if prefix.is_symlink():
            issues.append(ObjectIssue(rel, "symlink-entry", None))
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
            if child.is_symlink():
                issues.append(ObjectIssue(child_rel, "symlink-entry", digest))
                continue
            if not resolved.is_file():
                issues.append(ObjectIssue(child_rel, "unexpected-entry", digest))
                continue
            try:
                payload = resolved.read_bytes()
            except OSError:
                corrupt.add(digest)
                continue
            file_sha = hashlib.sha256(payload).hexdigest()
            content_hashes.append((digest, file_sha))
            if file_sha == digest:
                verified.add(digest)
            else:
                corrupt.add(digest)
    return verified, corrupt, issues, content_hashes


def _strict_shared_fields(
    payload: Mapping[str, object],
) -> tuple[str, str, Mapping[str, str], str, str, ArtifactStatus, bool] | None:
    """Validate shared fields without trimming or coercing raw JSON values."""

    schema_version = payload.get("schema_version")
    generator_version = payload.get("generator_version")
    provenance = payload.get("provenance")
    source_build_sha256 = payload.get("source_build_sha256")
    content = payload.get("content_sha256")
    status_value = payload.get("status")
    publishable = payload.get("publishable")
    if (
        not isinstance(schema_version, str)
        or not schema_version
        or schema_version != schema_version.strip()
    ):
        return None
    if (
        not isinstance(generator_version, str)
        or not generator_version
        or generator_version != generator_version.strip()
    ):
        return None
    if not isinstance(source_build_sha256, str) or not _SHA256.fullmatch(source_build_sha256):
        return None
    if not isinstance(content, str) or not _SHA256.fullmatch(content):
        return None
    if not isinstance(provenance, Mapping) or not provenance:
        return None
    strict_provenance: dict[str, str] = {}
    for key, value in provenance.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or not value
            or key != key.strip()
            or value != value.strip()
        ):
            return None
        strict_provenance[key] = value
    if not isinstance(status_value, str) or status_value not in _STATUS_VALUES:
        return None
    if not isinstance(publishable, bool):
        return None
    status = ArtifactStatus(status_value)
    if status is not ArtifactStatus.COMPLETE and publishable:
        return None
    return (
        schema_version,
        generator_version,
        strict_provenance,
        source_build_sha256,
        content,
        status,
        publishable,
    )


def _strict_validate_artifact(payload: Mapping[str, object]) -> ArtifactManifest | None:
    if set(payload.keys()) != _ARTIFACT_KEYS:
        return None
    shared = _strict_shared_fields(payload)
    if shared is None:
        return None
    try:
        artifact = ArtifactManifest(
            schema_version=shared[0],
            generator_version=shared[1],
            provenance=shared[2],
            source_build_sha256=shared[3],
            content_sha256=shared[4],
            status=shared[5],
            publishable=shared[6],
        )
    except ValueError:
        return None
    if artifact.to_dict() != dict(payload):
        return None
    return artifact


def _strict_validate_run(
    payload: Mapping[str, object],
) -> tuple[str, tuple[ArtifactManifest, ...]] | None:
    """Construct a strict RunManifest and require exact raw-payload roundtrip."""

    if set(payload.keys()) != _RUN_KEYS:
        return None
    shared = _strict_shared_fields(payload)
    if shared is None:
        return None
    artifacts_value = payload.get("artifacts")
    if not isinstance(artifacts_value, list):
        return None
    artifacts: list[ArtifactManifest] = []
    for item in artifacts_value:
        if not isinstance(item, Mapping):
            return None
        artifact = _strict_validate_artifact(item)
        if artifact is None:
            return None
        artifacts.append(artifact)
    try:
        run = RunManifest(
            schema_version=shared[0],
            generator_version=shared[1],
            provenance=shared[2],
            source_build_sha256=shared[3],
            content_sha256=shared[4],
            status=shared[5],
            publishable=shared[6],
            artifacts=tuple(artifacts),
        )
    except ValueError:
        return None
    if run.to_dict() != dict(payload):
        return None
    return run.content_sha256, run.artifacts


def _parse_manifest_ref(payload: Mapping[str, object]) -> _ManifestRef | None:
    if "artifacts" in payload:
        run = _strict_validate_run(payload)
        if run is None:
            return None
        run_content, artifacts = run
        return _ManifestRef(
            content_sha256=run_content,
            artifact_objects=tuple(artifact.content_sha256 for artifact in artifacts),
            artifact_records=tuple(
                content_sha256(to_json_value(artifact.to_dict())) for artifact in artifacts
            ),
        )
    artifact = _strict_validate_artifact(payload)
    if artifact is None:
        return None
    return _ManifestRef(content_sha256=artifact.content_sha256)


def _scan_manifests(
    manifests_root: Path, manifests_root_resolved: Path, store_root: Path
) -> tuple[
    set[str],
    dict[str, _ManifestRef],
    list[ManifestIssue],
    list[tuple[str, str, int, int, int, str]],
]:
    """Enumerate manifest records and retain raw/metadata freshness tokens."""

    verified: set[str] = set()
    references: dict[str, _ManifestRef] = {}
    issues: list[ManifestIssue] = []
    tokens: list[tuple[str, str, int, int, int, str]] = []
    if not manifests_root.is_dir():
        return verified, references, issues, tokens
    try:
        entries = sorted(manifests_root.iterdir(), key=lambda entry: entry.name)
    except OSError:
        issues.append(ManifestIssue("manifests", "unreadable-directory", None))
        return verified, references, issues, tokens
    for path in entries:
        rel = _store_relative(store_root, path)
        try:
            entry_st = path.lstat()
        except OSError:
            tokens.append((rel, "lstat-error", -1, -1, -1, ""))
            issues.append(ManifestIssue(rel, "unreadable", None))
            continue
        if stat.S_ISLNK(entry_st.st_mode) or _is_reparse_point(entry_st):
            tokens.append(
                (
                    rel,
                    "symlink",
                    entry_st.st_size,
                    entry_st.st_mtime_ns,
                    entry_st.st_ctime_ns,
                    "",
                )
            )
            stem = path.name[:64] if _MANIFEST_NAME.fullmatch(path.name) else None
            issues.append(ManifestIssue(rel, "symlink-entry", stem))
            continue
        if not stat.S_ISREG(entry_st.st_mode):
            tokens.append(
                (
                    rel,
                    "non-file",
                    entry_st.st_size,
                    entry_st.st_mtime_ns,
                    entry_st.st_ctime_ns,
                    "",
                )
            )
            stem = path.name[:64] if _MANIFEST_NAME.fullmatch(path.name) else None
            issues.append(ManifestIssue(rel, "unexpected-entry", stem))
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            tokens.append(
                (
                    rel,
                    "unreadable-file",
                    entry_st.st_size,
                    entry_st.st_mtime_ns,
                    entry_st.st_ctime_ns,
                    "",
                )
            )
            stem = path.name[:64] if _MANIFEST_NAME.fullmatch(path.name) else None
            issues.append(ManifestIssue(rel, "unreadable", stem))
            continue
        raw_sha = hashlib.sha256(raw).hexdigest()
        tokens.append(
            (
                rel,
                "file",
                entry_st.st_size,
                entry_st.st_mtime_ns,
                entry_st.st_ctime_ns,
                raw_sha,
            )
        )
        if not _MANIFEST_NAME.fullmatch(path.name):
            issues.append(ManifestIssue(rel, "wrong-name", None))
            continue
        stem = path.name[:64]
        resolved = path.resolve()
        if not resolved.is_relative_to(manifests_root_resolved):
            issues.append(ManifestIssue(rel, "escaped-path", stem))
            continue
        if raw_sha != stem:
            issues.append(ManifestIssue(rel, "raw-hash-mismatch", stem))
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            issues.append(ManifestIssue(rel, "invalid-json", stem))
            continue
        if not isinstance(payload, Mapping):
            issues.append(ManifestIssue(rel, "invalid-record", stem))
            continue
        if canonical_json_bytes(payload) != raw:
            issues.append(ManifestIssue(rel, "non-canonical", stem))
            continue
        ref = _parse_manifest_ref(payload)
        if ref is None:
            issues.append(ManifestIssue(rel, "invalid-record", stem))
            continue
        verified.add(stem)
        references[stem] = ref
    return verified, references, issues, tokens


@dataclass(frozen=True, slots=True)
class StoreScan:
    """A frozen, derived snapshot of one store at one point in time.

    Construct via :meth:`scan`; the snapshot is immutable and carries stable
    digests over scanned content, manifest metadata, the raw writer-lock token,
    and the persistent writer generation. Roots are
    marked with :meth:`mark_roots` and plans are built with :meth:`build_plan`.
    """

    store_root: str
    objects: frozenset[str]
    corrupt_objects: frozenset[str]
    object_issues: tuple[ObjectIssue, ...]
    manifests: frozenset[str]
    invalid_manifests: tuple[ManifestIssue, ...]
    blocked: bool
    _references: Mapping[str, _ManifestRef] = field(default_factory=dict, repr=False, compare=False)
    roots: frozenset[str] = frozenset()
    _content_hashes: tuple[tuple[str, str], ...] = field(default=(), repr=False, compare=False)
    _manifest_tokens: tuple[tuple[str, str, int, int, int, str], ...] = field(
        default=(), repr=False, compare=False
    )
    _lock_token: str = field(default="", repr=False, compare=False)
    _generation_token: str = field(default="", repr=False, compare=False)
    lock_digest: str = field(init=False, repr=False)
    generation_digest: str = field(init=False, repr=False)
    objects_digest: str = field(init=False, repr=False)
    manifests_digest: str = field(init=False, repr=False)
    snapshot_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.store_root, str):
            raise ArtifactIndexError("store_root must be a string")
        if not isinstance(self.blocked, bool):
            raise ArtifactIndexError("blocked must be a bool")
        objects = frozenset(sorted(_validated_digests(self.objects, "objects")))
        corrupt = frozenset(sorted(_validated_digests(self.corrupt_objects, "corrupt_objects")))
        manifests = frozenset(sorted(_validated_digests(self.manifests, "manifests")))
        roots = frozenset(sorted(_validated_digests(self.roots, "roots")))
        object_issues = tuple(
            sorted(
                (_coerce_object_issue(item) for item in self.object_issues),
                key=_object_issue_key,
            )
        )
        invalid_manifests = tuple(
            sorted(
                (_coerce_manifest_issue(item) for item in self.invalid_manifests),
                key=_manifest_issue_key,
            )
        )
        references: dict[str, _ManifestRef] = {}
        for key, value in self._references.items():
            if not isinstance(key, str) or not isinstance(value, _ManifestRef):
                raise ArtifactIndexError("references must map digests to _ManifestRef")
            references[key] = value
        content_hashes = tuple(
            sorted(_validated_content_hash(item) for item in self._content_hashes)
        )
        manifest_tokens = tuple(sorted(self._manifest_tokens))
        if any(
            not isinstance(item, tuple)
            or len(item) != 6
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            or not all(isinstance(value, int) for value in item[2:5])
            or not isinstance(item[5], str)
            for item in manifest_tokens
        ):
            raise ArtifactIndexError("manifest tokens must be canonical tuples")
        lock_token = self._lock_token if isinstance(self._lock_token, str) else ""
        generation_token = self._generation_token if isinstance(self._generation_token, str) else ""
        object.__setattr__(self, "objects", objects)
        object.__setattr__(self, "corrupt_objects", corrupt)
        object.__setattr__(self, "object_issues", object_issues)
        object.__setattr__(self, "manifests", manifests)
        object.__setattr__(self, "invalid_manifests", invalid_manifests)
        object.__setattr__(self, "roots", roots)
        object.__setattr__(self, "_references", MappingProxyType(references))
        object.__setattr__(self, "_content_hashes", content_hashes)
        object.__setattr__(self, "_manifest_tokens", manifest_tokens)
        object.__setattr__(self, "_lock_token", lock_token)
        object.__setattr__(self, "_generation_token", generation_token)
        objects_digest = content_sha256(
            to_json_value(
                {
                    "verified": sorted(objects),
                    "corrupt": sorted(corrupt),
                    "content": content_hashes,
                    "issues": [issue.to_value() for issue in object_issues],
                }
            )
        )
        manifests_digest = content_sha256(
            to_json_value(
                {
                    "verified": sorted(manifests),
                    "invalid": [issue.to_value() for issue in invalid_manifests],
                    "entries": manifest_tokens,
                }
            )
        )
        snapshot_digest = content_sha256(
            to_json_value(
                {
                    "objects": objects_digest,
                    "manifests": manifests_digest,
                    "lock": lock_token,
                    "generation": generation_token,
                }
            )
        )
        object.__setattr__(self, "objects_digest", objects_digest)
        object.__setattr__(self, "manifests_digest", manifests_digest)
        object.__setattr__(self, "lock_digest", lock_token)
        object.__setattr__(self, "generation_digest", generation_token)
        object.__setattr__(self, "snapshot_digest", snapshot_digest)

    @classmethod
    def scan(cls, store: FilesystemArtifactStore) -> Self:
        """Scan read-only, checking both ephemeral lock and persistent generation."""

        root = Path(store.root).resolve()
        start_generation_blocked, start_generation = _probe_generation(root)
        start_lock_blocked, start_lock = _probe_lock(root)
        objects_root, objects_resolved, objects_root_issue = _probe_layout_root(root, "objects")
        if objects_root_issue is not None:
            object_blocked = True
            object_issues = [ObjectIssue("objects", objects_root_issue, None)]
            verified_objects: set[str] = set()
            corrupt_objects: set[str] = set()
            content_hashes: list[tuple[str, str]] = []
        else:
            object_blocked = False
            (
                verified_objects,
                corrupt_objects,
                object_issues,
                content_hashes,
            ) = _scan_objects(objects_root, objects_resolved)
        manifests_root, manifests_resolved, manifests_root_issue = _probe_layout_root(
            root, "manifests"
        )
        if manifests_root_issue is not None:
            manifest_blocked = True
            manifest_digests: set[str] = set()
            references: dict[str, _ManifestRef] = {}
            manifest_issues = [ManifestIssue("manifests", manifests_root_issue, None)]
            manifest_tokens: list[tuple[str, str, int, int, int, str]] = []
        else:
            manifest_blocked = False
            manifest_digests, references, manifest_issues, manifest_tokens = _scan_manifests(
                manifests_root, manifests_resolved, root
            )
        end_lock_blocked, end_lock = _probe_lock(root)
        end_generation_blocked, end_generation = _probe_generation(root)
        lock_blocked = start_lock_blocked or end_lock_blocked or start_lock != end_lock
        generation_blocked = (
            start_generation_blocked or end_generation_blocked or start_generation != end_generation
        )
        lock_token = end_lock if end_lock else start_lock
        generation_token = end_generation if end_generation else start_generation
        blocked = generation_blocked or lock_blocked or object_blocked or manifest_blocked
        return cls(
            store_root=root.as_posix(),
            objects=frozenset(verified_objects),
            corrupt_objects=frozenset(corrupt_objects),
            object_issues=tuple(object_issues),
            manifests=frozenset(manifest_digests),
            invalid_manifests=tuple(manifest_issues),
            blocked=blocked,
            _references=references,
            _content_hashes=tuple(content_hashes),
            _manifest_tokens=tuple(manifest_tokens),
            _lock_token=lock_token,
            _generation_token=generation_token,
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

    def build_plan(self, store: FilesystemArtifactStore) -> GcPlan:
        """Compute a frozen, dry-run GC plan from this snapshot.

        A snapshot that is blocked (writer lock present or changed during the
        scan, or a layout root that is a symlink/escaped) or that contains any
        invalid manifest record yields a ``blocked`` plan with empty
        candidates: unparsable records may hide references. ``store`` is
        mandatory and is always re-scanned; there is no freshness bypass. A
        newly present lock, generation change, or invalid record yields a
        blocked plan, while any stable content change raises
        :class:`StaleScanError`.
        """
        if self.blocked or self.invalid_manifests:
            return GcPlan.blocked_plan(self)
        fresh = StoreScan.scan(store)
        if fresh.blocked or fresh.invalid_manifests:
            return GcPlan.blocked_plan(fresh)
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
    entries are reported separately and never become candidates. All inputs
    are deep-copied and normalized (sorted, de-duplicated, strictly typed) at
    construction so later mutation of caller-owned containers cannot affect
    the plan, and ``plan_digest`` binds the final normalized representation.
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
        if not isinstance(self.snapshot_digest, str) or not _SHA256.fullmatch(self.snapshot_digest):
            raise ArtifactIndexError("snapshot_digest must be a lowercase SHA-256 digest")
        if not isinstance(self.blocked, bool):
            raise ArtifactIndexError("blocked must be a bool")
        roots = frozenset(sorted(_validated_digests(self.roots, "roots")))
        reachable_objects = frozenset(
            sorted(_validated_digests(self.reachable_objects, "reachable_objects"))
        )
        unreferenced_objects = frozenset(
            sorted(_validated_digests(self.unreferenced_objects, "unreferenced_objects"))
        )
        unreferenced_manifests = frozenset(
            sorted(_validated_digests(self.unreferenced_manifests, "unreferenced_manifests"))
        )
        corrupt_objects = frozenset(
            sorted(_validated_digests(self.corrupt_objects, "corrupt_objects"))
        )
        missing_objects = frozenset(
            sorted(_validated_digests(self.missing_objects, "missing_objects"))
        )
        invalid_manifests = tuple(
            sorted(
                (_coerce_manifest_issue(item) for item in self.invalid_manifests),
                key=_manifest_issue_key,
            )
        )
        object_issues = tuple(
            sorted(
                (_coerce_object_issue(item) for item in self.object_issues),
                key=_object_issue_key,
            )
        )
        candidates = tuple(
            sorted(
                (_coerce_candidate(item) for item in self.candidates),
                key=lambda candidate: (candidate.kind, candidate.digest),
            )
        )
        object.__setattr__(self, "roots", roots)
        object.__setattr__(self, "reachable_objects", reachable_objects)
        object.__setattr__(self, "unreferenced_objects", unreferenced_objects)
        object.__setattr__(self, "unreferenced_manifests", unreferenced_manifests)
        object.__setattr__(self, "corrupt_objects", corrupt_objects)
        object.__setattr__(self, "missing_objects", missing_objects)
        object.__setattr__(self, "invalid_manifests", invalid_manifests)
        object.__setattr__(self, "object_issues", object_issues)
        object.__setattr__(self, "candidates", candidates)
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
        """Return a fail-closed plan for a snapshot that must not be swept."""
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
) -> GcPlan:
    """Scan a store, mark roots, and build a plan from one snapshot.

    The writer lock and persistent generation are always probed read-only. Any
    invalid manifest blocks the result, and ``StoreScan.build_plan(store)``
    always performs a mandatory freshness re-scan; there is no bypass.
    """
    scan = StoreScan.scan(store)
    return scan.mark_roots(roots).build_plan(store)
