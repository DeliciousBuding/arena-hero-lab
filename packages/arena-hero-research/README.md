# arena-hero-research

`arena-hero-research` provides preregistered, reproducible analysis over immutable
Arena Hero benchmark artifacts. Research code is intentionally outside the simulator hot
path and may grow optional scientific dependencies without imposing them on simulation.

## Implemented surface

- immutable `ResearchQuestion`, `Hypothesis`, `ExperimentDesign`, `Factor`, `Outcome`,
  `ReplicationPlan`, and `AnalysisPlan` contracts;
- canonical preregistration SHA-256 generation and verification;
- paired mean differences, Cohen's dz, deterministic paired-bootstrap percentile
  confidence intervals, and direction-aware p-values;
- Benjamini-Hochberg false-discovery-rate adjustment;
- paired normal-approximation sample-size planning;
- explicit missing-pair policies and data-quality reports;
- content-addressed `ResearchRun` and `ResultBundle` records with provenance,
  environment, and SBOM digests;
- publication guards for partial and failed research runs.

The analysis entry point evaluates every preregistered confirmatory outcome in declared
order. It rejects undeclared outcomes and does not select whichever result looks best.

```python
from arena_hero_research import analyze_preregistered_paired_outcomes

estimates, quality = analyze_preregistered_paired_outcomes(
    preregistration,
    observations,
    bootstrap_seed=20260810,
)
```

## Current boundary

The package currently implements paired confirmatory comparisons. Assignment generation,
replication execution, simulated power analysis, hierarchical models, distributed research
execution, SBOM generation, and publication services are explicit extension points rather
than claimed implementations. Benchmark execution and artifact storage remain owned by
`arena-hero-bench`.

See [the research platform design](../../docs/research-platform.md) for lifecycle,
invariants, and the M3-M7 roadmap.

## Validation

```bash
uv run ruff format --check packages/arena-hero-research
uv run ruff check packages/arena-hero-research
uv run ty check packages/arena-hero-research
uv run pytest packages/arena-hero-research -q
```
