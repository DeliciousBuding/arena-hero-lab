# arena-hero-bench

Platform services for reproducible Arena Hero benchmark execution:

- versioned contestant manifests and artifact verification;
- strict layered configuration and canonical frozen snapshots;
- experiment/run/shard identities, local executor and distributed executor seams;
- content-addressed artifact stores, resume/idempotency, and deterministic merge;
- report conversion and publication-safe artifact manifests.

It depends on `arena-hero-sim`; the simulator never depends on it. Partial and failed shards
cannot be merged into a publishable run.

## Offline differential surfaces

`arena-hero-bench` exposes two content-addressed, CLI-only differential
commands that never change public ranking semantics:

- `differential --run <manifest>` classifies a TS-legacy replay against a
  Python-agent replay per tick and per run (`arena.bench.replay-differential.v1`).
- `kpi-differential --run <manifest>` classifies an evolve-baseline replay
  against a Python-agent run across six independently computed behavior
  dimensions: tick alignment, resource growth, collection/delivery,
  population/forces, survival/terminal, and decision distribution
  (`arena.bench.kpi-differential.v1`).

Every comparison is classified into exactly one of `MATCH`, `MISMATCH`,
`EXPECTED_UNKNOWN`, or `INCONCLUSIVE`; nothing is left unclassified. Reports are
deterministic and content-addressed: identical inputs produce the same artifact
digest and input reordering does not change it. The Python side is consumed
through the versioned offline importer, so torn tails, corrupt records,
duplicate ticks, tenant mismatches, and companion-fixture tick-set mismatches
fail closed. Wire-contract gaps are declared as `EXPECTED_UNKNOWN`; a run
manifest may bind sanitized companion fixtures whose provenance is surfaced as
`evidence_kind` (the committed corpus declares `sanitized_fixture`).

## Bounded offline replay soak

`soak --run <manifest>` (P6-4) replays the differential corpus and the
canonical reference workload N times through the process executor,
monitoring for resource leaks, digest drift, uncaught exceptions, and
process-tree residue. Any anomaly fails the soak with a classification; a
clean soak reports `status=pass` with stable per-round digests. The
committed fixture `tests/fixtures/soak/run-burnin-20260802-a-v1.json` is a
seconds-scale reproducible skeleton; the same manifest with a larger
`rounds`/`max_duration_seconds` extends to a 24h offline soak.
Fault-injection steps are reachable only through a private test seam and
mark the report `attested=false`.

## Competitive evaluation battery

`competitive-eval --run <manifest>` (P6-7) runs a scenario x seed x
contestant battery through the versioned offline importer and the P6-3 KPI
differential classifier, then emits one deterministic, content-addressed
report (`arena.bench.competitive-eval-report.v1`) with per-cell digests,
per-scenario/per-contestant aggregate stats, and a presentation-level
contestant ranking (`ranking_kind=aggregate_match_count`). The committed
fixture `tests/fixtures/competitive_eval/run-burnin-20260802-a-v1.json`
is a seconds-scale reproducible battery (2 scenarios x 2 seeds x 2
contestants = 8 cells).

- Every dimension of every cell is classified into exactly one of `MATCH` /
  `MISMATCH` / `EXPECTED_UNKNOWN` / `INCONCLUSIVE`; a clean battery always
  reports `unclassified_count == 0`.
- Fail-closed: a corrupt manifest, a missing cell record, a cell
  classification error, or any unclassified dimension fails the whole
  battery with a classified issue; failed cells stay in the report for
  diagnosis.
- Deterministic: identical inputs produce the same `artifact_sha256`; cell
  order is fixed by the manifest (scenarios -> seeds -> contestants) and no
  wall-clock timestamps enter the artifact.
- Fault-injected cells are reachable only through a private test seam and
  mark the report `injected_cells=true` / `attested=false`.
- The Python-agent side is consumed from committed `agent-run-v1` records
  by default; a live-agent runs directory seam (below) is optional and the
  lab has no dependency on the agent package or SDK.

### Live agent CLI seam

`competitive-eval` can consume live offline agent runs produced by the
external `arena-hero-agent` CLI through an external uv environment — the
lab adds no dependency on the agent package or SDK. Point the battery at an
external agent batch output directory with `--agent-runs-dir <dir>` (or the
manifest field `agent_runs_dir`, or the `ARENA_AGENT_RUNS_DIR` environment
variable; CLI flag > manifest > env). The directory uses the lab-owned
layout `<dir>/<contestant_id>/<scenario_id>/<seed_id>/<tenant_id>/ticks.jsonl`:
one run directory per battery cell whose `ticks.jsonl` holds `agent-run-v1`
records (`schemaVersion` / `recordType` / `tenantId` / `tick` /
`deadlineOutcome` / `submitResult` …), optionally beside a `health.json`.

Produce the runs with the offline agent CLI through the external uv env
(each cell's `--data-root` is the cell directory, so the agent's native
`<data-root>/<tenant>/ticks.jsonl` output is the lab layout directly):

```bash
uv run --project <arena-hero-agent-checkout> arena-hero-agent run \
  --tenant <tenant-id> --input <scenario-turns> --run-id <run-id> \
  --data-root <agent-runs-dir>/<contestant-id>/<scenario-id>/<seed-id>
```

Then run the battery with the live records:

```bash
arena-hero-bench competitive-eval --run <manifest> \
  --agent-runs-dir <agent-runs-dir>
```

The seam is fail-closed: a missing directory, a missing or extra cell, or a
run whose `tenantId`/`schemaVersion` does not match the battery fails the
whole battery with a clear error (exit code 2) and never silently falls back
to the committed fixtures. Live batteries should declare
`"evidence_kind": "production"`. `map_agent_runs_dir(runs_dir, manifest)` is
the public mapping helper (batch output directory -> per-cell record paths),
and the end-to-end live test
`tests/test_competitive_eval.py::test_live_agent_runs_dir_integration` stays
skipped unless `ARENA_AGENT_RUNS_DIR` is set to a real agent output directory.

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
  stolen automatically. Every admitted writer also advances the persistent
  `.state/generation` counter before mutation. Read-only index scans compare
  this monotonic token around enumeration, so a writer whose ephemeral lock is
  acquired and released entirely during a scan is still detected. Digest-derived
  paths are hex-only, so traversal and Windows separator inputs are rejected
  before any filesystem operation.
- Artifact-index GC output is dry-run only. Candidate-bearing plans require
  `StoreScan.build_plan(store)`, which always re-scans the store; no omitted-store
  or `recheck=false` freshness bypass exists. Strict artifact/run records must
  round-trip byte-for-byte without trimming or coercion, and snapshot identity
  includes every manifest entry's raw SHA-256 and lstat metadata.

Power-loss durability: file bytes are fsynced before the atomic rename on every
platform. On POSIX the parent directory is fsynced after the rename (and the
store's `objects/` directory is fsynced after first creating a shard
directory), which makes the rename durable on local filesystems; if a directory
fsync is unavailable the write degrades to best-effort. On Windows the rename
is atomic but directories are not fsynced because there is no portable
directory fsync, so a power loss immediately after the rename may not persist
the rename itself. This is a reference adapter, not a database-grade durability
layer.

No database, network, or external service is used.


## Local process executor

`arena_hero_bench.process_executor.ProcessExecutor` is a reference adapter
that runs each bounded work item in a fresh child Python process. Backends are
declared through an explicit `BackendProcessSpec` allowlist; an unknown backend,
engine version, protocol, or requested capability is rejected immediately and
never silently falls back to in-process execution. `reference_engine_process_executor`
pre-registers only the deterministic `reference-engine` backend.

- Versioned work/result envelopes (`arena.process.work.v1` /
  `arena.process.result.v1`) are exchanged as one canonical JSON line over the
  child's stdin/stdout; requests run in fixed plan order and results are
  reassembled in that order, so `max_workers` never changes shard digests.
  Scenarios are carried once per envelope in a top-level map keyed by their
  content SHA-256; request entries only reference the digest.
- Each child is bounded by `max_workers` concurrency, a per-task timeout, and a
  configurable hard output cap (`max_output_bytes`). Crashes, non-zero exits,
  invalid payloads, oversized output, and timeouts fail closed: the affected
  requests become failed results and the shard is never publishable.
- On timeout the whole process tree is reaped within a finite window: POSIX
  uses a fresh session plus `killpg`; Windows assigns each child to a Job
  Object (stdlib `ctypes`) and terminates the job, falling back to
  terminate/kill with a bounded drain and pipe close when job assignment is
  unavailable. `execute` still returns FAILED when a worker spawns a grandchild
  that inherits the output pipes.
- The same operation id/plan resume contract as `LocalBatchExecutor` applies,
  and shard artifacts share the identical `arena.bench.shard-result.v1` schema.
  The plan digest covers every resume-sensitive request field, including scenario input,
  tick/protocol/determinism settings, requested features, parameters, and labels; reusing an
  operation id for any changed request fails closed instead of resuming the old scenario.
- Cancellation (`close`) terminates active process trees; spawn bookkeeping is
  lock-serialized, so no child can be spawned after `close`.
- Trust boundary: the allowlist constrains request routing only. Constructing a
  `BackendProcessSpec` grants the code-execution authority of the child
  process, so specs are trusted configuration. This is a reference adapter,
  not a security sandbox: no shell, network, secrets, dynamic imports, or
  production data are used, and children inherit the parent environment
  without resource isolation.
- Thread-safety: spawn bookkeeping is thread-safe and `close` may be called
  from another thread. Concurrent `execute` calls on the same executor are
  safe for process bookkeeping, but the ledger and artifact store are
  single-writer contracts, so callers must serialize concurrent executions.

## Performance evidence

`arena_hero_bench.performance.measure_reference_workload` measures real reference-engine runs
of the canonical content-addressed workload and returns a raw, content-addressed
`PerformanceEvidence` artifact:

- warmup rounds run outside the samples; measured durations come from the integer
  `perf_counter_ns` clock with every raw sample retained. A clock that returns non-integer,
  non-finite, boolean, or backwards values fails closed: the sample is discarded, an issue is
  recorded, and the evidence is not publishable.
- each measured round must reproduce the baseline semantic run digest; any drift makes the
  evidence not publishable.
- `median_ns`, `p95_ns`, and `p99_ns` are derived only from complete, credible raw samples.
- `production_claim` is fixed to `false`; publishable evidence can never carry issues.

For an actual candidate comparison, use `measure_comparative_workloads` with concrete
reference and candidate `SimulatorBackend` objects. The benchmark layer constructs the workload
runners internally and uses the fixed `perf_counter_ns` clock. Runner factories and injected
clocks exist only in a private test seam and always produce non-publishable evidence.

The `arena.bench.comparative-performance-evidence.v2` artifact binds workload, protocol, public
environment, reference/candidate backend and run identities, the recomputed differential digest,
episode-order digest, both raw timing series, and replay-attestation provenance. A caller may
supply a `ReplayArtifactResolver` that returns canonical replay envelope bytes. Every claimed
payload, envelope, and semantic digest is recomputed from those bytes before evidence can be
publishable. Without a resolver, or when any replay fails verification, the artifact is explicitly
`self-reported/unattested` and `publishable=false`.

This attestation proves that resolved bytes match the artifact refs used by the differential gate;
it does not independently prove how the backend produced those bytes, prevent a trusted backend
from caching work, or constitute production benchmarking. Backends and resolvers remain trusted
inputs, and `production_claim` is always `false`.

The differential gate is recomputed inside the measurement from the current baseline run and,
when supplied, a candidate run (`compare_workload_runs(baseline, candidate)`). An externally
injected `DifferentialReport` is trusted only when it is byte-identical to that recomputed
gate: the workload, reference run, candidate run, and content digests must all match. Stale,
forged, or wrong-identity reports fail closed and never enter publishable evidence.
