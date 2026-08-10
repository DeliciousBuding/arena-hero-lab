"""Filesystem content-addressed artifact store for benchmark run artifacts.

The store implements the ``ArtifactStore`` contract used by orchestration
(``put``/``get``) and adds ``verify`` plus a publishable-aware manifest layer
on top. Objects are addressed by the lowercase SHA-256 of their exact bytes,
written atomically (exclusive temp file + fsync + rename), and verified
fail-closed on every read.

Design notes
------------
- No database, network, or external service is involved.
- A writer lock serializes check-then-write sections. Acquisition uses atomic
  exclusive file creation (``O_CREAT | O_EXCL``), which is reliable within and
  across processes on POSIX and Windows, so concurrent writers cannot
  interleave object creation with conflict detection. The mutex is fail-closed:
  a lock file, however old, blocks a second writer until the timeout, and a
  lock left behind by a crashed writer is never stolen automatically. Crash
  recovery is explicit and manual (``StoreLock.recover``).
- Digest-derived paths are hex-only; traversal, Windows separator, and
  absolute-path identifiers are rejected by validation before any filesystem
  operation.
- An identity is immutable: a repeated ``put`` of identical bytes is an
  idempotent no-op; a torn or corrupt object under a valid identity is
  atomically rewritten (self-healing) while holding the lock, and different
  bytes under a genuinely reused identity are refused.
- ``partial`` and ``failed`` artifacts may be retained for diagnostics but can
  never be stored as publishable and never report ``is_publishable``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, suppress
from pathlib import Path

from arena_hero_bench.manifest import ArtifactManifest, ArtifactStatus
from arena_hero_sim.serialization import canonical_json_bytes, content_sha256, to_json_value

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStoreError(ValueError):
    """Base error for the filesystem artifact store."""


class MissingObjectError(ArtifactStoreError):
    """The addressed object is not present in the store."""


class CorruptObjectError(ArtifactStoreError):
    """The addressed object exists but its bytes do not match its digest."""


class ArtifactConflictError(ArtifactStoreError):
    """An identity was reused for different bytes or content."""


class StoreLockError(ArtifactStoreError):
    """The writer lock could not be acquired before the timeout."""


def _validated_digest(value: str, field_name: str = "digest") -> str:
    if not _SHA256.fullmatch(value):
        raise ArtifactStoreError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


class StoreLock(AbstractContextManager["StoreLock"]):
    """Writer mutex backed by atomic exclusive file creation.

    Acquisition uses ``O_CREAT | O_EXCL``, which is atomic on POSIX and
    Windows both within and across processes, so two writers cannot both hold
    the lock. The mutex is fail-closed: a lock file, however old, always blocks
    a second writer until the timeout, and a lock left behind by a crashed
    writer is never stolen automatically. Release removes the lock file only
    when it still carries this owner's token, so a timed-out acquirer can never
    delete a live lock. The lock is not re-entrant. Crash recovery is explicit:
    confirm no live writer holds the lock, then call ``StoreLock.recover`` or
    remove the lock file.
    """

    def __init__(self, path: str | Path, *, timeout: float = 10.0) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self._token: str | None = None

    def __enter__(self) -> StoreLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout
        token = f"{os.getpid()}:{uuid.uuid4().hex}"
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise StoreLockError(
                        f"could not acquire writer lock before timeout: {self.path}"
                    ) from None
                time.sleep(0.05)
                continue
            with os.fdopen(fd, "w", encoding="ascii", closefd=True) as handle:
                handle.write(token)
            self._token = token
            return

    @classmethod
    def recover(cls, path: str | Path) -> None:
        """Explicitly remove a lock file left behind by a crashed writer.

        This is the only recovery path and is never performed automatically.
        Callers must first confirm that no live writer holds the lock.
        """
        Path(path).unlink()

    def release(self) -> None:
        if self._token is None:
            return
        try:
            try:
                current = self.path.read_text(encoding="ascii")
            except FileNotFoundError:
                current = ""
            if current == self._token:
                with suppress(FileNotFoundError):
                    self.path.unlink()
        finally:
            self._token = None


class FilesystemArtifactStore:
    """Content-addressed artifact store persisted under a local root directory.

    ``put``/``get``/``verify`` operate on raw immutable bytes. The manifest
    layer (``store_artifact``/``load_artifact``) binds an
    :class:`~arena_hero_bench.manifest.ArtifactManifest` to its content and
    records the ``status``/``publishable`` contract so ``partial`` and
    ``failed`` artifacts can never become publishable.
    """

    def __init__(self, root: str | Path, *, lock_timeout: float = 10.0) -> None:
        self.root = Path(root).resolve()
        self._objects = self.root / "objects"
        self._manifests = self.root / "manifests"
        self._tmp = self.root / ".tmp"
        self._locks = self.root / ".locks"
        self._lock = StoreLock(self._locks / "writer.lock", timeout=lock_timeout)
        self._local = threading.Lock()
        for directory in (self._objects, self._manifests, self._tmp, self._locks):
            directory.mkdir(parents=True, exist_ok=True)

    def _object_path(self, digest: str) -> Path:
        validated = _validated_digest(digest)
        path = self._objects / validated[:2] / validated[2:]
        resolved = path.resolve()
        if not resolved.is_relative_to(self._objects):
            raise ArtifactStoreError("digest path escapes the object root")
        return resolved

    def put(self, payload: bytes, *, expected_sha256: str | None = None) -> str:
        """Store immutable bytes and return their SHA-256 identity.

        A repeated put of identical bytes is an idempotent no-op. Different
        bytes under the same identity are refused.
        """
        if not isinstance(payload, bytes):
            raise ArtifactStoreError("payload must be bytes")
        digest = content_sha256(payload)
        if expected_sha256 is not None and expected_sha256 != digest:
            raise ArtifactStoreError("artifact payload does not match expected SHA-256")
        with self._local, self._lock:
            self._store_bytes(digest, payload)
        return digest

    def get(self, digest: str) -> bytes:
        """Return object bytes, failing closed on missing or corrupt objects."""
        path = self._object_path(digest)
        try:
            payload = path.read_bytes()
        except FileNotFoundError as exc:
            raise MissingObjectError(f"artifact not found: {digest}") from exc
        if hashlib.sha256(payload).hexdigest() != digest:
            raise CorruptObjectError(f"stored artifact does not match digest: {digest}")
        return payload

    def verify(self, digest: str) -> bool:
        """Return whether the object exists and matches its digest."""
        path = self._object_path(digest)
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            return False
        return hashlib.sha256(payload).hexdigest() == digest

    def contains(self, digest: str) -> bool:
        """Return whether the object file exists, without verifying bytes."""
        return self._object_path(digest).is_file()

    def store_artifact(self, manifest: ArtifactManifest, content: bytes) -> None:
        """Persist content bound to a manifest, enforcing the status contract.

        The content must match ``manifest.content_sha256`` exactly. A manifest
        that is ``partial`` or ``failed`` is retained for diagnostics but can
        never be stored as publishable.
        """
        digest = content_sha256(content)
        if digest != manifest.content_sha256:
            raise ArtifactConflictError("artifact content does not match manifest content_sha256")
        if manifest.publishable and manifest.status is not ArtifactStatus.COMPLETE:
            raise ArtifactStoreError(
                f"{manifest.status.value} artifacts cannot be stored as publishable"
            )
        with self._local, self._lock:
            self._store_bytes(digest, content)
            self._store_manifest_record(manifest)

    def load_artifact(self, manifest: ArtifactManifest) -> bytes:
        """Load verified content for a manifest (fail-closed on corruption)."""
        return self.get(manifest.content_sha256)

    def manifest_records(self) -> Iterator[ArtifactManifest]:
        """Yield stored manifest records, skipping unreadable or invalid ones.

        A record is yielded only when its filename stem is a legal SHA-256 and
        equals the canonical digest of the parsed record; wrong names and
        tampered records fail closed by being skipped.
        """
        for path in sorted(self._manifests.glob("*.json")):
            if not _SHA256.fullmatch(path.stem):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            try:
                record = ArtifactManifest.from_dict(payload)
            except ValueError:
                continue
            if content_sha256(to_json_value(record.to_dict())) != path.stem:
                continue
            yield record

    def is_publishable(self, digest: str) -> bool:
        """Return whether any stored manifest marks this content publishable.

        The object itself must first verify; a missing or corrupt object is
        never publishable.
        """
        validated = _validated_digest(digest)
        if not self.verify(validated):
            return False
        return any(
            record.content_sha256 == validated
            and record.status is ArtifactStatus.COMPLETE
            and record.publishable
            for record in self.manifest_records()
        )

    def _store_bytes(self, digest: str, payload: bytes) -> None:
        path = self._object_path(digest)
        try:
            existing = path.read_bytes()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if hashlib.sha256(existing).hexdigest() != digest:
                # A torn or corrupt object under a valid identity: rewrite the
                # canonical bytes atomically while the writer lock is held.
                self._atomic_write(path, payload)
                return
            if existing != payload:
                # The existing object itself verifies, so this is a genuine
                # digest identity reused for different bytes.
                raise ArtifactConflictError("digest identity already stored with different bytes")
            return
        self._atomic_write(path, payload)

    def _store_manifest_record(self, manifest: ArtifactManifest) -> None:
        record_digest = content_sha256(to_json_value(manifest.to_dict()))
        path = self._manifests / f"{record_digest}.json"
        payload = canonical_json_bytes(manifest.to_dict())
        try:
            existing = path.read_bytes()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if hashlib.sha256(existing).hexdigest() != record_digest:
                self._atomic_write(path, payload)
                return
            if existing != payload:
                raise ArtifactConflictError("manifest identity already stored with different bytes")
            return
        self._atomic_write(path, payload)

    def _atomic_write(self, final_path: Path, payload: bytes) -> None:
        name = final_path.name[:24] or "object"
        tmp_path = self._tmp / f"tmp-{name}-{os.getpid()}-{uuid.uuid4().hex}"
        try:
            fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            parent = final_path.parent
            parent_was_missing = not parent.exists()
            parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_path, final_path)
            self._fsync_directory(parent)
            if parent_was_missing:
                self._fsync_directory(parent.parent)
        finally:
            with suppress(FileNotFoundError):
                tmp_path.unlink()

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name != "posix":
            return
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)
