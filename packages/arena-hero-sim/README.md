# arena-hero-sim

Backend-neutral simulator platform contracts for deterministic Arena Hero experiments.

The package is deliberately split between platform contracts and replaceable engines:

- `ReferenceBackendPlaceholder` validates registry and negotiation wiring and always returns
  `unsupported`;
- `ReferenceEngineBackend` is a real correctness-oriented engine for one bounded,
  explicitly versioned rules slice;
- future optimized, native, process, or remote backends must implement the same public
  backend protocol and conformance behavior.

The package has no benchmark, research, web, agent, NumPy, Pandas, SciPy, or Arrow dependency.
See [`../../docs/simulator-platform.md`](../../docs/simulator-platform.md).

## M4 reference-engine slice

The rules identity is `arena-hero/v0.14-reference-harvest-v1`. The additive simultaneous
movement behavior is separately advertised as `arena.reference.movement-dependency.v1`; this
keeps the original rules digest and harvest-to-deposit replay identity stable while making the
newly supported boundary explicit. A result is `complete` only when all of the following are
exact matches:

- backend `reference-engine`, engine `0.2.0-replay-identity`, and protocol `arena.sim.v1`;
- the exported `REFERENCE_RULESET` digest;
- a scenario registered by its content SHA-256;
- the scenario initial-world SHA-256, deterministic seed, contestants, and frozen config;
- only capabilities advertised by the backend;
- enough tick budget to finish the registered script.

The implemented vertical slice covers:

1. immutable rules, world, terrain, player, unit, scenario, turn, and command contracts;
2. canonical full-world and scenario SHA-256 identities;
3. Manhattan visibility with integer supercover obstacle blocking;
4. schema-level legal actions for `WORKER` units;
5. one-cell simultaneous movement with fixed-point dependency settlement, atomic linear
   chains, same-player swaps and cycles, hostile-swap rejection, stationary/failed-departure
   blocking, cross-player contested destinations, and same-player capacity arbitration by
   ascending raw UUID bytes;
6. natural resource-node harvest, worker cargo, movement back to a stationary core, partial or
   full deposit, capacity settlement, and deterministic event ordering;
7. explicit commit and next-observation phases;
8. versioned canonical replay/event records, frame hash chaining, final-world binding,
   tamper rejection, and byte-for-byte semantic re-execution from the registered scenario;
9. stable single and batch execution through the platform registry.

The golden closed loop is the public, synthetic sequence
`HARVEST -> MOVE LEFT x3 -> DEPOSIT`. It starts with one worker at `(3, 0)`, one natural
resource node, and a core at `(0, 0)`. The supported run consumes the node, returns the worker
to the core, raises core resources from 5 to 6, and consumes zero random draws.

## Honest unsupported boundary

This is not a complete official Arena Hero engine. The backend returns `unsupported`, or
`partial` for an exhausted tick budget, with `publishable=false` when the request is outside
the exact slice. In particular, M4 does not implement:

- combat, beacon effects, dropped resource piles, refill placement, respawn, core migration,
  spawn, healing, repair, self-destruct, or contested opponent economy actions;
- Core movement intents or mixed Unit/Core movement dependency graphs;
- official match termination, winners, or official scoring;
- incremental hashing, zero-copy interchange, process/distributed execution, or a security
  sandbox.

`supports_incremental_world_hash` and `supports_zero_copy` therefore remain `false`. The
current slice uses full canonical world hashes after every committed tick. Its deterministic
RNG stream is implemented and versioned, but the supported harvest/deposit workload requires
no random draw, so replay positions remain unchanged.

## TypeScript oracle boundary

The M4 behavior was migrated from read-only, repository-local TypeScript oracle evidence. The
movement resolver and golden matrix are pinned to commit
`d56a5e7cd94b9873d39a366aa12d22911e2f62ab`; this is reference evidence, not a claim that
ambiguous behavior is an official Arena Hero rule:

- `packages/arena-agent/src/sim/world/world.ts` — cell capacity and world invariants;
- `packages/arena-agent/src/sim/world/canonical.ts` — canonical world identity intent;
- `packages/arena-agent/src/sim/visibility/visibility.ts` — Manhattan visibility and integer
  supercover blocking;
- `packages/arena-agent/src/sim/engine/movement.ts` — simultaneous fixed-point movement,
  dependency propagation, same-player cycles/swaps, hostile-swap rejection, raw-UUID tie-breaks,
  occupancy, and contested destinations;
- `packages/arena-agent/src/sim/engine/economy.ts` — natural-node harvest and deposit;
- `packages/arena-agent/src/sim/engine/settlement.ts` — stable phase ordering and atomic commit;
- `packages/arena-agent/test/sim-movement.test.ts` and
  `packages/arena-agent/test/sim-economy.test.ts` — golden movement cases and the
  harvest-to-deposit loop.

No production state, server-only source, tenant mapping, secret, or private topology is needed
or encoded. Rules outside the source/test evidence above remain unsupported rather than
inferred.

## Performance evidence

The existing `arena-hero-sim-bench` command still measures only contract validation and
placeholder batch dispatch. It is not an engine-throughput benchmark and always records
`production_claim=false`.

A second, versioned harness measures the real reference engine against the frozen
canonical workload:

```bash
uv run arena-hero-sim-bench --benchmark reference-workload --repeats 5 --batch-size 9
```

Schema: `arena.sim.reference-workload-benchmark.v1`. Each round is exactly one execution
of the canonical 9-episode `reference-movement-dependency` workload through
`run_canonical_reference_workload`; the episode count is fixed by the manifest and cannot
be scaled by a caller-supplied `--episodes` value. The report binds the workload digest and
the semantic run digest, retains raw per-round `perf_counter_ns` durations with median/p95,
records backend identity plus public Python/platform metadata, and always sets
`production_claim=false`. Reports never include host identity: no absolute paths, user
names, or hostnames.

The reference engine remains the correctness oracle and carries no hardware-independent
throughput claim. `WorkloadManifest` and `WorkloadCase` freeze a backend-neutral,
content-addressed workload identity and deterministic request expansion; see
[`../../docs/reference-workloads.md`](../../docs/reference-workloads.md). Comparative evidence
must execute both backends, retain raw samples, pass the reference/optimized differential gate,
and keep `production_claim=false` until an independently reviewed production-equivalent protocol
exists.

## Optimized Python backend

`OptimizedEngineBackend` registers `optimized-python-v1@0.1.0` independently from the reference
engine. It is pure Python/stdlib and optimizes static visibility geometry with precomputed
supercover rays and a complete `(width, height, obstacles, origin, radius)` cache key. The cache
stores immutable values and never crosses the public result boundary. The backend advertises no
incremental hashing, zero-copy, native, NumPy, or Numba capability.

Replay envelopes stay on `arena.reference.replay.v1`. Results expose legacy/payload, envelope, and
backend-neutral semantic replay SHA-256 refs. Differential workload comparison requires semantic
replay equality while allowing backend-specific request, payload, and envelope identities.
