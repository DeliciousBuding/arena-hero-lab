# Reference workloads and differential performance gates

## Purpose

A simulator performance result is credible only when it is bound to an immutable semantic
workload. The workload contract in `arena_hero_sim.workload` separates the game inputs from the
backend and measurement environment so the same episodes can be executed by the readable
reference engine and every optimized implementation.

The existing contract-dispatch microbenchmark remains a platform-overhead check. It is not a
real engine workload and keeps `production_claim=false`.

## Workload identity

`WorkloadManifest` is backend-neutral and content-addressed. Its SHA-256 identity covers:

- schema, workload id, and workload version;
- ruleset name, version, and rules digest;
- ordered cases;
- each scenario artifact digest, initial-world digest, seed, tick budget, contestant ids,
  feature requirements,
  frozen parameters, labels, and repetition count;
- public, environment-neutral metadata.

A case expands into stable backend-neutral episode ids. Request ids include the backend id,
which prevents result collisions while preserving episode alignment for differential comparison.
Changing a seed, tick budget, feature, scenario digest, order, or repetition count changes the
workload digest.

Scenario bytes are not embedded in the manifest. They are resolved by SHA-256 from a verified
artifact provider. Missing or corrupt scenarios fail closed.

## First real reference workload

The first workload must exercise implemented rules rather than placeholder dispatch. It should
cover the movement dependency capability with known-answer scenarios for:

1. independent moves;
2. linear dependency chains;
3. friendly swap and longer cycles;
4. hostile swap rejection;
5. cross-player target conflicts;
6. stationary or failed occupants;
7. deterministic UUID raw-byte tie-breaking.

Combat, Beacon, Core movement, mixed Unit-Core graphs, dropped piles, respawn, healing, repair,
and official winner or score remain excluded until authoritative rules and known answers exist.
Unsupported slices must remain explicit and non-publishable.

## Measurement protocol

A performance evidence artifact binds the workload digest, backend descriptor, engine build,
measurement protocol, and public environment snapshot. The comparative implementation freezes:

- warmup rounds separate from measured rounds;
- ordered batch-size and worker-count matrices;
- `perf_counter_ns` wall-clock durations with raw samples retained;
- median and percentile summaries derived from raw samples;
- Python, OS family, architecture, CPU-count, and optional dependency versions without host identity;
- timeout and output limits;
- `production_claim=false` until an explicit reviewed release gate changes it.

A report with missing cases, failed/unsupported results, mismatched identities, or discarded raw
samples is not publishable.

The sim package now ships the first real measurement entrypoint: `--benchmark reference-workload`
on `arena-hero-sim-bench` executes the canonical 9-episode workload through the real engine,
freezes the workload and semantic run digests, and retains raw per-round durations
(`arena.sim.reference-workload-benchmark.v1`, always `production_claim=false`). This harness is
an in-process measurement signal; `arena-hero-bench` remains the owner of bounded execution,
differential binding, content-addressed storage, and publication eligibility.

## Differential gate

Every optimized backend must execute the exact same workload manifest and pass a fail-closed
comparison against the reference backend before its performance samples can be interpreted.
At minimum the gate compares aligned episode ids, status, ticks completed, rules digest, seed,
final world digest, deterministic metrics, and backend-neutral semantic replay identity. Backend
and request ids, replay payload digests, and replay envelope digests are expected to differ and
are not semantic mismatches. Malformed, incomplete, duplicated, or tampered replay artifact refs
fail the gate.

The gate runs before performance aggregation. A faster result that fails semantic equivalence is
recorded as a failed differential artifact, never as a benchmark improvement.

The reference-only measurement entrypoint recomputes the gate from the current baseline run and,
when supplied, a candidate run. An injected differential report is trusted only when it is
byte-identical to that recomputed gate: workload, reference-run, candidate-run, and content
digests must all match, so stale or forged reports cannot reach publishable evidence. Timer
outputs are validated against the integer wall-clock contract (non-integer, non-finite,
boolean, or backwards values fail closed and discard the sample).

`measure_comparative_workloads` is the real candidate path. It accepts independent runner
factories, executes both backends for baseline, warmup, and measured rounds, rejects identical or
wrong candidate backend identity, and emits
`arena.bench.comparative-performance-evidence.v1`. The evidence binds workload, protocol, public
environment, both backend/run identities, differential digest, episode-order digest, and both raw
duration series. It never converts a reference timing into candidate evidence and always keeps
`production_claim=false`.

## Ownership and extension path

- `arena-hero-sim` owns workload semantics, identity, request expansion, backend capabilities, and
  reference/optimized differential rules.
- `arena-hero-bench` owns bounded execution, measurement orchestration, raw performance evidence,
  content-addressed storage, and publication eligibility.
- `arena-hero-research` may consume verified benchmark artifacts; it does not redefine workload
  or simulator correctness.
- New backends register explicitly and do not silently fall back to the reference engine.

The first optimized backend is `optimized-python-v1@0.1.0`. It changes only internal visibility
geometry evaluation through a complete-key static cache and runs the same nine-case workload at
batch sizes 1, 3, and 9. Final worlds, metrics, semantic replay identities, and episode order must
match the reference backend exactly; any mismatch invalidates comparative evidence.
