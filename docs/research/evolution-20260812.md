# GA/evolution over the frozen reference workload (P3-13)

> 实现日期：2026-08-12 · 分支 `w12/p3-13` · 基线 master@f7fbb60（774 tests）

## Purpose

P3-13 adds a deterministic genetic-algorithm (GA) evolution surface to
`arena-hero-research`. It evolves parameterized strategies (genomes) against the
frozen reference workload, with fixed-seed reproducibility and an independent
holdout. It is offline-only: no production connection, no simulator engine
execution inside the research package, and no heavy dependencies (stdlib only).

The GA core is generic. The package ships a concrete reference wiring over the
canonical reference workload (`reference-movement-dependency@2026-08-10.v1`,
SHA-256 `7b7267499afa6032585f40d069e605cc767bbb473e889cbb8ebfc63c4193fc0c`).

## Components

| Surface | Module | Role |
| --- | --- | --- |
| `GeneSpec` / `GenomeSpec` / `Genome` | `evolution.py` | Bounded, ordered, validated genome encoding with integer/continuous genes |
| `EvolutionConfig` | `evolution.py` | Frozen run configuration; requires disjoint non-empty evolution/holdout corpora |
| `GenerationRecord` / `EvolutionReport` | `evolution.py` | Per-generation trajectory summaries and the content-addressed run report (`arena.research.evolution-report.v1`) |
| `run_evolution` | `evolution.py` | Deterministic GA loop: seeded population, tournament selection, uniform crossover, gaussian mutation, elitism |
| `FitnessEvaluator` | `evolution.py` | Protocol: one deterministic fitness function bound to exactly one corpus |
| `ReferenceWorkloadEvidence` / `load_reference_workload_evidence` | `reference_evolution.py` | Frozen per-case features and known-answer targets from the canonical workload (contracts only, no engine) |
| `ReferenceComplexityFitness` | `reference_evolution.py` | Corpus-bound linear policy evaluator over workload case features |
| `reference_evolution_config` | `reference_evolution.py` | Config factory that binds the frozen workload identity |

## Holdout independence (structural, not advisory)

1. `EvolutionConfig` rejects overlapping or empty evolution/holdout corpora.
2. Every evaluator is bound to exactly one corpus via `corpus_ids`.
3. `run_evolution` fails closed if the evolution evaluator corpus differs from
   `config.evolution_corpus` or the holdout evaluator corpus differs from
   `config.holdout_corpus`.
4. The evolution loop therefore never observes holdout case ids; the final
   best genome is evaluated exactly once through the holdout-bound evaluator.

## Fixed-seed reproducibility

Same seed + same inputs produce an identical `EvolutionReport`: identical
per-generation trajectory, best genome, holdout evaluation, and
`canonical_sha256`. The digest is computed with the cross-platform quantized
canonical JSON hash (`quantized_content_sha256`), so the report is stable
across Windows and Linux for the same source state.

Pinned evidence for this source state (default canonical run, seed
`20260812`, population 12, 6 generations):

- workload SHA-256: `7b7267499afa6032585f40d069e605cc767bbb473e889cbb8ebfc63c4193fc0c`
- evidence SHA-256: `8c38d444cc13d79124078dcd94f544950d74213c0bc3fee20b579147e6ab0ef3`
- report canonical SHA-256: `5e38f73735d908f10577aa4aef5e08c4184f958cc16aca7f285bfe4492dd1901`
- best fitness (evolution): `-0.2456` · holdout fitness: `-0.2432`

Reproduce:

```python
from arena_hero_research.evolution import run_evolution
from arena_hero_research.reference_evolution import (
    ReferenceComplexityFitness,
    load_reference_workload_evidence,
    reference_complexity_genome_spec,
    reference_evolution_config,
)

evidence = load_reference_workload_evidence()
case_ids = evidence.case_ids
evolution_corpus = tuple(sorted(case_ids)[:6])
holdout_corpus = tuple(sorted(case_ids)[6:])
spec = reference_complexity_genome_spec(evidence)
config = reference_evolution_config(
    run_id="p3-13-canonical",
    evidence=evidence,
    evolution_corpus=evolution_corpus,
    holdout_corpus=holdout_corpus,
    seed=20260812,
    population_size=12,
    generations=6,
)
report = run_evolution(
    config=config,
    evaluator=ReferenceComplexityFitness(evidence=evidence, corpus_ids=evolution_corpus, spec=spec),
    holdout_evaluator=ReferenceComplexityFitness(
        evidence=evidence, corpus_ids=holdout_corpus, spec=spec
    ),
)
assert report.canonical_sha256 == "5e38f73735d908f10577aa4aef5e08c4184f958cc16aca7f285bfe4492dd1901"
```

The canonical split: evolution = `cross-player-contested-target`,
`failed-occupant-blocks-dependent`, `friendly-swap`, `friendly-three-unit-cycle`,
`harvest-deposit-golden`, `hostile-swap-rejection`; holdout = `independent-moves`,
`linear-dependency-chain`, `uuid-raw-byte-tie-break`. The sets are disjoint by
construction and the report records both.

## Boundaries

- Offline only; never publishes or deploys; never touches production.
- The research package does not construct or run a simulator engine; evidence is
  derived from the frozen workload manifest and verified scenario registry.
- Simulator-backed evaluators can be wired by callers outside the package and
  still inherit the same corpus-bound holdout guarantees from `run_evolution`.
- No NumPy/Pandas/network dependencies; stdlib only.
