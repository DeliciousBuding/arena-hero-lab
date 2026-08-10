# arena-hero-bench

Platform services for reproducible Arena Hero benchmark execution:

- versioned contestant manifests and artifact verification;
- strict layered configuration and canonical frozen snapshots;
- experiment/run/shard identities, local executor and distributed executor seams;
- content-addressed artifact stores, resume/idempotency, and deterministic merge;
- report conversion and publication-safe artifact manifests.

It depends on `arena-hero-sim`; the simulator never depends on it. Partial and failed shards
cannot be merged into a publishable run.

## Filesystem artifact store

`arena_hero_bench.storage.FilesystemArtifactStore` is a reference
content-addressed store persisted under a local root directory. Objects are
addressed by the lowercase SHA-256 of their exact bytes and written atomically
(temp file + fsync + rename), so concurrent writers never observe a partial
object and repeated `put` calls are idempotent.

- `put(payload, *, expected_sha256=None)` stores bytes and returns the digest;
  different bytes under the same identity are refused.
- `get(digest)` returns verified bytes and fails closed on missing or corrupt
  objects; `verify(digest)` reports integrity without raising.
- `store_artifact(manifest, content)` binds an `ArtifactManifest` to its
  content, requiring the bytes to match `content_sha256`. `partial` and
  `failed` artifacts are retained for diagnostics but can never be stored as
  publishable and never report `is_publishable`.
- A writer lock (atomic exclusive create with timeout) serializes
  check-then-write sections and is fail-closed: an old lock file is never
  stolen automatically, so a crash-left lock blocks writers until it is
  removed explicitly (`StoreLock.recover`); digest-derived paths are hex-only,
  so traversal and Windows separator inputs are rejected before any filesystem
  operation.

No database, network, or external service is used.
