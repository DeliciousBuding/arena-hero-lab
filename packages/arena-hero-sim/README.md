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

- backend `reference-engine`, engine `0.1.1-m4`, and protocol `arena.sim.v1`;
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
  spawn, healing, repair, self-destruct, or opponent actions;
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

M4 intentionally adds no reference-engine throughput claim. The reference engine is the
correctness oracle. A future engine benchmark must name its workload, separate setup from hot
path execution, pin engine/rules identities, report full environment metadata, and retain
`production_claim=false` until an independently reviewed production-equivalent protocol exists.
