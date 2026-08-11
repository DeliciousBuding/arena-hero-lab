# Simulator platform

## Purpose

`arena-hero-sim` is the backend-neutral simulator platform. It defines immutable requests,
results, rules identities, capability negotiation, backend registration, batch execution, and
performance evidence without coupling the platform to one engine implementation.

The built-in `reference-placeholder` backend validates integration contracts only. It always
returns `unsupported` and is not a game-rules engine. The readable `reference-engine` is the
correctness oracle. `optimized-python-v1` is a separate stdlib-only backend that reuses the same
rules implementation while caching static visibility geometry; it does not use incremental
hashing, native code, NumPy, or Numba.

## Components

```mermaid
graph TD
    REQUEST["SimulationRequest"] --> REGISTRY["BackendRegistry"]
    REGISTRY --> NEGOTIATE["Protocol and capability negotiation"]
    NEGOTIATE --> PLACEHOLDER["Reference placeholder"]
    NEGOTIATE --> REF["Reference engine"]
    NEGOTIATE --> OPT["Optimized Python v1"]
    PLACEHOLDER --> RESULT["SimulationResult"]
    REF --> RESULT
    OPT --> RESULT
    RESULT --> ARTIFACTS["Content-addressed artifacts"]
```

### Immutable contracts

- `RulesetRef` binds a portable rules name and version to a SHA-256 digest.
- `SimulatorConfig` freezes backend id, engine version, deterministic seed, tick budget,
  protocol version, requested features, and parameters.
- `SimulationRequest` binds a request/episode identity to contestants and input digests.
- `SimulationResult` records engine identity, rules digest, seed, status, world digest,
  metrics, artifacts, and publication eligibility.
- `BackendCapabilities` advertises batch size, execution modes, protocol versions,
  incremental hashing, zero-copy support, and interchange formats.

All incomplete, failed, and unsupported results are non-publishable.

## Hot path design

The contracts deliberately permit several replaceable hot-path implementations:

1. **Structure-of-arrays or ECS storage** for dense unit state and cache-friendly phase loops.
2. **Incremental world hashing** where a backend updates deterministic region/component
   digests instead of serializing the complete world every tick.
3. **Batch episode execution** through `simulate_batch`, with registry-controlled chunking.
4. **Process-local parallelism** with deterministic request ordering and isolated RNG streams.
5. **Optional zero-copy interchange** for stable buffers. Canonical JSON remains the baseline;
   Arrow-compatible buffers may be negotiated by capability rather than required globally.

NumPy, Pandas, Arrow, native extensions, and GPU runtimes are not core dependencies. A backend
may add them behind capabilities and must pass the same conformance suite.

### Optimized Python v1 boundary

`optimized-python-v1@0.1.0` precomputes relative supercover rays and caches visibility by the
complete static namespace `(width, height, obstacles, origin, radius)`. The current world schema
has no clipping rectangle, so width and height are derived from the world geometry and are cache
namespace inputs only; they do not clip or reinterpret visibility. Cached cells are immutable
`frozenset` values, and every public observation, replay, result, and workload run remains frozen.
The backend advertises `optimized-static-visibility-cache-v1` and explicitly keeps
`supports_incremental_world_hash=false`.

## Capability negotiation

A request selects exactly one backend id and engine version. The registry rejects:

- unknown backends;
- duplicate backend registrations;
- unsupported protocol versions;
- requested features absent from the backend capability set;
- mixed-backend batches;
- results whose request, rules, seed, or engine identity differs from the request.

This prevents an optimized backend from silently dropping a rule or feature.


## Replay identities

The replay envelope remains `arena.reference.replay.v1`. Four artifact refs make identity roles
explicit without changing those canonical replay bytes:

- `replay-sha256` is the legacy alias for the payload digest;
- `replay-payload-sha256` identifies the v1 payload;
- `replay-envelope-sha256` identifies the complete canonical envelope bytes;
- `replay-semantic-sha256` excludes only the backend-specific request id.

Strict parsing requires all four refs, rejects duplicates, and requires the legacy alias to match
the explicit payload digest. Differential comparison uses the semantic replay identity, while
storage and provenance may retain the backend-specific payload and envelope identities.

## Microbenchmark harness

Run the local contract-dispatch microbenchmark:

```bash
uv run arena-hero-sim-bench --episodes 10000 --repeats 5 --batch-size 256
```

Schema: `arena.sim.microbenchmark.v1`.

The harness measures immutable request validation, registry negotiation, batch chunking, and
placeholder result construction. It sets `production_claim=false`; it does not measure game
simulation throughput. Reports contain durations, median, p95, throughput, Python version,
platform family, backend id, and engine version without host identity.
 `MicrobenchmarkReport` validates that raw sample count matches `repeats`, every duration is a
positive integer, median/p95 and throughput agree with those raw durations, and
`production_claim` is exactly `false`.

The same command selects the real-engine harness against the frozen canonical workload:

```bash
uv run arena-hero-sim-bench --benchmark reference-workload --repeats 5 --batch-size 9
```

Schema: `arena.sim.reference-workload-benchmark.v2`. A round is exactly one execution of the
canonical 9-episode reference workload through the real engine, including the known-answer
gate and the semantic run digest. `--episodes` is rejected for this selector because a round
is a fixed semantic unit, not a copyable scenario count. The report binds the workload and
run digests, retains raw per-round durations with median/p95, and records backend identity
plus Python/platform metadata without host identity. It always sets `production_claim=false`.

## Reference workload gate

The backend-neutral workload contract and the mandatory reference/optimized differential gate are
defined in [`reference-workloads.md`](reference-workloads.md). A real performance claim must bind a
content-addressed workload manifest and retain raw measurement samples. Contract dispatch remains
a separate overhead signal and cannot be promoted into engine-throughput evidence.
`arena_hero_bench.measure_comparative_workloads` accepts two concrete simulator backends, builds
its runners internally, verifies resolved replay envelope bytes when a resolver is supplied, and binds the
reference and candidate run identities plus the differential and episode-order digests, retains
both raw timing series, and always records `production_claim=false`.

## Performance budgets

Performance budgets are evidence gates, not marketing claims:

| Surface | Foundation budget |
|---|---|
| Contract-dispatch median | No more than 15% regression against a same-machine pinned baseline |
| Batch determinism | Identical result ordering and digests for batch sizes 1, 2, 4, and configured maximum |
| Allocation policy | Hot-path state may be reused internally but public requests/results remain immutable |
| World hashing | Full and incremental digests must match on conformance fixtures |
| Parallel execution | Worker count must not alter episode or merged run digests |
| Interchange | Canonical JSON is required; zero-copy/Arrow paths are optional negotiated accelerators |

A real engine baseline will add ticks/second, p50/p95/p99 tick latency, peak RSS, world size,
and artifact throughput using shared semantic fixtures.

## Extension points

- `SimulatorBackend` for Python, native, remote, or accelerator-backed engines.
- `BackendCapabilities` for feature and data-boundary negotiation.
- `BackendRegistry` for deterministic selection and validation.
- `simulate_batch` for vectorized or multi-episode backends.
- content-addressed inputs/results for replay, resume, and distributed execution.

## Benchmark platform control plane

```mermaid
graph LR
    MANIFEST["ContestantManifest"] --> CREG["ContestantRegistry"]
    LAYERS["defaults -> experiment -> contestant -> run"] --> SNAPSHOT["FrozenConfig"]
    CREG --> PLAN["ShardPlan"]
    SNAPSHOT --> PLAN
    PLAN --> LOCAL["LocalExecutor"]
    PLAN -. future .-> DIST["DistributedExecutor"]
    LOCAL --> STORE["ArtifactStore"]
    DIST --> STORE
    STORE --> MERGE["Deterministic merge"]
    MERGE --> PUBLISH["Publishable complete run"]
```

### Contestant registry

A contestant is registered by a versioned manifest containing entry point, language,
runtime, protocol version, artifact SHA-256, configuration schema, resource requirements,
capabilities, and isolation policy. The registry rejects duplicate id/version pairs and
artifact digest mismatches. Core packages do not contain a fixed contestant or model catalog.

### Configuration snapshots

Configuration resolution is strict and ordered:

```text
schema defaults -> defaults layer -> experiment -> contestant -> run overrides
```

Every layer rejects unknown keys and type/range violations. Secret-designated fields are
runtime-only and rejected from frozen snapshots. The resolved snapshot and each source layer
receive canonical SHA-256 digests so a run can be reproduced without embedding credentials.

### Execution and merge

`ShardPlan` binds experiment, run, shard, operation, and the complete canonical request identity:
request/episode ids, backend/engine/rules, seed, tick budget, protocol, determinism, requested
features, parameters, initial/scenario artifact digests, contestants, and labels. This prevents a
different scenario or execution contract from colliding with a resumable plan digest.
`LocalBatchExecutor` executes in-process through the backend registry. The bounded
`ProcessExecutor` uses explicit backend specifications, spawn-safe versioned envelopes,
timeouts, process-tree cleanup, output limits, and deterministic result ordering; it never
silently falls back to in-process execution and is not a contestant security sandbox.

`FilesystemArtifactStore` is the local reference `ArtifactStore`: immutable bytes and manifest
records are content-addressed, atomically written, verified on read, and protected by a
fail-closed writer mutex. Identical operations resume through the execution ledger; reusing an
operation id with a different plan fails closed. `DistributedExecutor` remains a protocol for
future schedulers and remote stores. Merge requires exact shard coverage, one result per shard,
one run id, complete status, and publishable artifacts. Input order does not affect the digest.
## M3-M7 evolution

- **M3 — platform contracts:** immutable contracts, registry, placeholder, batch API, local
  microbenchmark, and conformance tests. Implemented in this slice.
- **M4 — deterministic reference engine:** immutable world, visibility, replay, harvest/deposit,
  and simultaneous unit movement chains/swaps/cycles are implemented against pinned oracle
  evidence. Combat, Beacon, Core movement, and official winner/score remain unsupported.
- **M5 — optimized backend:** the first stdlib-only backend is implemented with precomputed
  visibility rays, a complete-key immutable cache boundary, backend-neutral replay identity, and
  real candidate comparative evidence. SoA/ECS storage, bounded allocation work, and broader
  differential regression budgets remain future slices.
- **M6 — local scale:** deterministic sharding/resume, the filesystem artifact store, and a
  bounded spawn-safe process executor are implemented as reference adapters. Arrow, resource
  isolation, and contestant sandboxing remain planned.
- **M7 — distributed scale:** remote executor adapters and stores, failure injection, and
  reproducible distributed release evidence remain planned.
