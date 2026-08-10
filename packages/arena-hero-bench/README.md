# arena-hero-bench

Platform services for reproducible Arena Hero benchmark execution:

- versioned contestant manifests and artifact verification;
- strict layered configuration and canonical frozen snapshots;
- experiment/run/shard identities, local executor and distributed executor seams;
- content-addressed artifact stores, resume/idempotency, and deterministic merge;
- report conversion and publication-safe artifact manifests.

It depends on `arena-hero-sim`; the simulator never depends on it. Partial and failed shards
cannot be merged into a publishable run.
