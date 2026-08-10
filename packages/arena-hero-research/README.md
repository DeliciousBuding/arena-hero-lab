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
- a random-intercept hierarchical surface (`fit_random_intercept`,
  `cross_validate_random_intercept`, `paired_to_cluster_observations`) for clustered or
  repeated-measure outcomes: within-cluster treatment contrast, conservative
  between-cluster t intervals, and an independent MoM/ANOVA cross-validation path;
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

The initial confirmatory analysis remains a paired-comparison workflow. The hierarchical
surface (random-intercept REML) is a separate, explicitly bounded capability: it does not
claim cluster-robust errors, Kenward-Roger or Satterthwaite degrees of freedom, random
slopes, nested or multilevel structures, GLMMs, or inference for cluster-randomized
(whole-cluster assignment) designs, and cluster-level hierarchical power is deferred. The
package does not claim sequential testing, adaptive randomization, or causal identification
outside the registered design. The package includes a local filesystem reference ledger
adapter; distributed executors, remote or replicated ledger adapters, signed provenance,
and independent publication services remain future adapters behind the implemented ports.

## Random-intercept hierarchical surface

For clustered or repeated-measure outcomes the package provides a minimal but real
two-level random-intercept model over the frozen `ClusterObservation` grain
(outcome, cluster, observation, treatment, value). The estimand is the within-cluster
(conditional) average treatment contrast. The authoritative estimator is profile REML
(stdlib only); `cross_validate_random_intercept` compares it against an independent
within-OLS + between method-of-moments path with declared tolerances. The paired design is
the balanced degenerate case and `paired_to_cluster_observations` bridges pairs so the REML
effect reproduces the existing paired mean difference.

Design gates fail closed: fewer than two clusters, non-finite values, missing levels (under
a declared `fail` / `drop-cluster` policy), and cluster-randomized designs are rejected;
singular or boundary variance disables interval and effect-size claims. The confidence
interval is a conservative between-cluster t with `df = cluster_count - 1` (not
Satterthwaite or Kenward-Roger), and the effect label is `hierarchical-d-v1` (not Cohen's
d or dz). See `docs/research-platform.md` for the full capability matrix and non-claims.

## Minimal offline flow

The durable chronology must be committed one phase at a time. Design records are frozen in
the pilot transaction, each successor must extend the durable predecessor exactly once, and
confirmatory evidence is accepted only after the confirmatory freeze is durable.

```python
storage = FilesystemResearchLedgerStorage("research-ledger")
ledger = DurableResearchLedger(storage)

assignment = generate_assignments(
    preregistration,
    assignment_units,
    treatment_factor="strategy",
)
pilot = ResearchLifecycle.create(
    study_id="study-1",
    preregistration=preregistration,
    assignment=assignment,
)
ledger.freeze_design(
    operation_id="freeze-pilot",
    lifecycle=pilot,
    preregistration=preregistration,
    assignment=assignment,
)

exploratory = pilot.transition(
    ResearchPhase.EXPLORATORY,
    preregistration=preregistration,
    assignment=assignment,
)
ledger.freeze_design(
    operation_id="freeze-exploratory",
    lifecycle=exploratory,
    preregistration=preregistration,
    assignment=assignment,
)
confirmatory = exploratory.transition(
    ResearchPhase.CONFIRMATORY,
    preregistration=preregistration,
    assignment=assignment,
)
ledger.freeze_design(
    operation_id="freeze-confirmatory",
    lifecycle=confirmatory,
    preregistration=preregistration,
    assignment=assignment,
)

tasks = build_replication_tasks(
    lifecycle=confirmatory,
    preregistration=preregistration,
    assignment=assignment,
    provenance_by_environment=provenance_by_environment,
)
results = ReplicationRunner(fake_executor).run(
    operation_id="confirmatory-run-1",
    tasks=tasks,
)
ledger.record_replication_results(
    operation_id="confirmatory-run-1",
    lifecycle=confirmatory,
    preregistration=preregistration,
    assignment=assignment,
    tasks=tasks,
    results=results,
    environment=environment_snapshot,
    sbom=sbom,
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

## Durable ledger storage port

`ResearchLedgerStorage` is the persistence port. The reference
`FilesystemResearchLedgerStorage` adapter stores canonical public records under
content-addressed SHA-256 object paths and commits operations as one canonical JSONL line
in a sequence-checked, hash-chained journal. A single-host OS file lock serializes writers.
Objects use same-directory temporary files, file `fsync`, and atomic replacement before the
journal line is appended, `fsync`ed, and read back. Reusing an `operation_id` with identical
record identities is idempotent; conflicting content fails closed. Immutable
`(study, kind, subject)` keys cannot be rewritten and no delete API is provided.

The protocol is designed to fail closed across ordinary process crashes: a crash before the
journal commit can leave an unreferenced immutable object and may require computation to run
again, but that object is not accepted as committed evidence. A final journal fragment
without a newline is treated as uncommitted, never silently accepted.
`recover_torn_tail()` must be called explicitly; it verifies the complete prefix,
quarantines the discarded bytes by SHA-256, atomically restores the prefix, and verifies it
again. Corruption in a committed line, hash-chain break, missing object, or object digest
mismatch is not auto-repaired.

Durability against sudden power loss is filesystem- and OS-dependent. Parent-directory
`fsync` is best-effort on POSIX, is skipped by this adapter on Windows, and the first
`mkdir(parents=True)` directory chain is not individually `fsync`ed. Atomic replacement,
rename persistence, controller caches, and storage hardware may therefore provide weaker
power-loss guarantees than the process-crash protocol. The adapter is a local reference,
not a distributed exactly-once transaction service, remote object store, signed timestamp,
complete supply-chain attestation, or protection against an attacker who can coherently
rewrite every local byte.

## Durable scientific record service

`DurableResearchLedger` is the application layer over the storage port. A new study must
begin with a pilot transaction that freezes the preregistration, assignment manifest, and
analysis plan. Every later lifecycle record must be the unique next durable successor with
exact predecessor history and unchanged design bindings; phases cannot be skipped or
backfilled after evidence. Evidence is checked against the complete durable predecessor
chain. Data-use claims must match the transaction operation, study, active phase role, and
confirmatory freeze when held-out data is involved.

Replication commits retain the held-out data-use claims, tasks, complete/partial/failed
results, environment snapshot, minimal SBOM, and their provenance binding. Analysis commits
retain result bundles whether the effect is favorable, null, adverse, partial, or failed. A
conflicting rewrite of the same scientific subject or `operation_id` is rejected, and there
is no deletion surface.

Durable data-use claims are replayed on every restart, so pilot/exploratory leakage, holdout
reuse by another study or operation, and post-hoc replacement remain blocked after process
exit. Callers should invoke `replay_replication_results()` before executing an operation; a
matching committed operation returns verified results without recomputation, while a changed
task plan fails closed. This is idempotent durable recording, not distributed exactly-once
execution; the process-crash and power-loss limits above remain part of the adapter contract.
