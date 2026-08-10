# arena-hero-research

`arena-hero-research` is an offline, preregistered research execution system over
immutable Arena Hero benchmark evidence. It is intentionally outside the simulator hot
path. It does not connect to production, choose the best seed, or reinterpret confirmatory
outcomes after they are observed.

## Implemented scientific lifecycle

### Frozen design and deterministic assignment

- immutable question, hypothesis, factor, outcome, replication, and analysis contracts;
- canonical preregistration SHA-256 generation and verification;
- deterministic block-balanced assignment across scenario, seed, seat, block, treatment,
  replication, and environment;
- versioned assignment manifests with canonical digest and round-trip parsing;
- explicit `pilot -> exploratory -> confirmatory -> replication -> complete` lifecycle;
- confirmatory freeze binding hypotheses, outcomes, analysis plan, seeds, and assignments.

### Replication execution and quality gates

- versioned replication tasks bound to preregistration, design, analysis, assignment,
  frozen config, source, input, environment, and SBOM digests;
- injected `ReplicationExecutor` Protocol for synthetic/local executors without network or
  simulator ownership;
- deterministic operation ledger: exact replay is idempotent, while the same operation ID
  with a different task plan fails closed;
- data-use ledger preventing pilot/exploratory leakage into confirmatory evidence and
  rejecting holdout reuse by another operation or study;
- strict merge rejection for duplicate, missing, unexpected, mixed-identity, partial, and
  failed replication results;
- `DROP_PAIR` only when preregistered and accompanied by an exact dropped-pair record;
- complete-pair and minimum-successful-replication gates before analysis.

### Analysis, power, and conclusions

- paired mean differences, Cohen's dz, deterministic paired-bootstrap percentile
  confidence intervals, and direction-aware approximate p-values;
- Benjamini-Hochberg false-discovery-rate adjustment;
- paired normal-approximation sample-size planning;
- deterministic Monte Carlo power simulation using a disclosed Cornish-Fisher
  approximation to Student-t critical values;
- data-quality reports and content-addressed `ResearchRun` / `ResultBundle` artifacts;
- replication-aware `ResearchConclusion` artifacts that retain every confirmatory result
  and every replication, including null and adverse evidence;
- qualification requires effect magnitude, confidence interval, multiplicity-adjusted
  evidence, data quality, minimum successful replications, and replication support
  together. A p-value alone never qualifies a conclusion.

## Approximation assumptions and limits

The Monte Carlo planner is **not exact**. It assumes normally distributed paired
differences, estimates power under a fixed synthetic effect and standard deviation, and
uses a Cornish-Fisher approximation to Student-t critical values. Its artifact records the
method, seed, assumptions, Monte Carlo standard error, and limitations. Planning is allowed
only in pilot or exploratory phases and is rejected after the confirmatory freeze.

The initial confirmatory analysis remains a paired-comparison workflow. It does not claim
hierarchical inference, cluster-robust errors, sequential testing, adaptive randomization,
or causal identification outside the registered design. Distributed executors, durable
ledger adapters, signed provenance, and independent publication services remain future
adapters behind the implemented contracts.

## Minimal offline flow

```python
assignment = generate_assignments(
    preregistration,
    assignment_units,
    treatment_factor="strategy",
)
lifecycle = ResearchLifecycle.create(
    study_id="study-1",
    preregistration=preregistration,
    assignment=assignment,
)
lifecycle = lifecycle.transition(
    ResearchPhase.EXPLORATORY,
    preregistration=preregistration,
    assignment=assignment,
).transition(
    ResearchPhase.CONFIRMATORY,
    preregistration=preregistration,
    assignment=assignment,
)

tasks = build_replication_tasks(
    lifecycle=lifecycle,
    preregistration=preregistration,
    assignment=assignment,
    provenance_by_environment=provenance_by_environment,
)
results = ReplicationRunner(fake_executor).run(
    operation_id="confirmatory-run-1",
    tasks=tasks,
)
merged = merge_replications(
    preregistration=preregistration,
    assignment=assignment,
    expected_tasks=tasks,
    results=results,
)
```

The executor in this example is injected. The research package does not import a concrete
simulator engine, open network connections, read secrets, or own production execution.

## Validation

```bash
uv sync --locked --all-groups
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run ty check packages/arena-hero-research
uv run pytest -q packages/arena-hero-research/tests
uv build --package arena-hero-research
```

## Public environment and SBOM provenance

`EnvironmentSnapshot` captures only bounded runtime facts (Python implementation/version,
OS family/release, machine class, executor id, and explicit metadata). It never reads
process environment variables, hostnames, user names, executable paths, or credentials.
`SoftwareBillOfMaterials` records an explicit allowlist of disclosed components. Both are
canonical JSON artifacts with SHA-256 identities, and `EnvironmentProvenance` binds their
digests for later verification. Recursive metadata validation rejects keys containing
secret-, token-, password-, credential-, authorization-, API-key-, or private-key-like
terms.

This minimal SBOM is reproducibility evidence, not a complete CycloneDX/SPDX document,
signature, vulnerability scan, build attestation, or proof that every transitive or native
component was captured. Callers must disclose the components relevant to their execution
and may layer stronger external supply-chain attestations on the same digests.
