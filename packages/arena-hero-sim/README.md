# arena-hero-sim

Backend-neutral simulator platform contracts for deterministic Arena Hero experiments.

Implemented foundation:

- immutable rules, configuration, request, result, and capability contracts;
- backend protocol, registry, protocol/capability negotiation, and batch dispatch;
- an explicit `unsupported` reference placeholder for platform wiring;
- canonical JSON/SHA-256 utilities and a local contract-overhead microbenchmark.

The package has no benchmark, research, web, agent, NumPy, Pandas, SciPy, or Arrow dependency.
See [`../../docs/simulator-platform.md`](../../docs/simulator-platform.md).
