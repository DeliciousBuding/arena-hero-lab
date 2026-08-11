"""Frozen reference workload evidence and corpus-bound fitness (P3-13).

Bridges the generic GA core to the frozen reference workload without
importing a concrete simulator engine. Evidence is derived from the canonical
workload manifest and its verified scenario registry: per-case structural
features (contestants, units, turns, obstacles, resource cells) plus frozen
known-answer metrics (events, rng draws). The target is the frozen
known-answer ``ticks_completed``, which the reference policy learns to
predict.

``ReferenceComplexityFitness`` is a linear policy evaluator bound to exactly
one corpus of case ids. The genome encodes one weight per feature plus an
intercept; fitness is the negative mean absolute prediction error over the
bound corpus. Because the evaluator is corpus-bound and :func:`run_evolution`
refuses binding mismatches, the evolution loop can never observe holdout case
ids and the final holdout evaluation can never observe evolution case ids.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from arena_hero_research.evolution import (
    EvolutionConfig,
    EvolutionError,
    GeneSpec,
    Genome,
    GenomeSpec,
)
from arena_hero_research.validation import require_float, require_identifier, require_sha256
from arena_hero_sim.reference_workload import (
    canonical_reference_scenario_registry,
    canonical_reference_workload_manifest,
)
from arena_hero_sim.serialization import JsonValue, quantized_content_sha256

REFERENCE_EVIDENCE_SCHEMA: str = "arena.research.reference-workload-evidence.v1"
REFERENCE_EVIDENCE_GENERATOR_VERSION: str = "0.1.0"

_WEIGHT_BOUND = 1.0
_INTERCEPT_BOUND = 1.0


@dataclass(frozen=True, slots=True)
class WorkloadCaseEvidence:
    """Frozen per-case features and target from the canonical workload."""

    case_id: str
    features: Mapping[str, float]
    target: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", require_identifier(self.case_id, "case_id"))
        features = {
            require_identifier(str(key), "feature name"): float(value)
            for key, value in self.features.items()
        }
        if not features:
            raise EvolutionError("case evidence requires at least one feature")
        if not all(math.isfinite(value) for value in features.values()):
            raise EvolutionError("case features must be finite")
        target = float(self.target)
        if not math.isfinite(target):
            raise EvolutionError("case target must be finite")
        object.__setattr__(self, "features", MappingProxyType(dict(sorted(features.items()))))
        object.__setattr__(self, "target", target)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "case_id": self.case_id,
            "features": {key: value for key, value in self.features.items()},
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> WorkloadCaseEvidence:
        raw_features = value["features"]
        if not isinstance(raw_features, Mapping):
            raise TypeError("features must be a mapping")
        return cls(
            case_id=str(value["case_id"]),
            features={
                str(key): require_float(item, f"feature {key}")
                for key, item in raw_features.items()
            },
            target=require_float(value["target"], "target"),
        )


@dataclass(frozen=True, slots=True)
class ReferenceWorkloadEvidence:
    """Content-addressed frozen evidence over the canonical reference workload."""

    schema_version: str
    generator_version: str
    workload_id: str
    workload_version: str
    workload_sha256: str
    cases: tuple[WorkloadCaseEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workload_id", require_identifier(self.workload_id, "workload_id"))
        cases = tuple(self.cases)
        if not cases:
            raise EvolutionError("reference workload evidence requires at least one case")
        case_ids = [case.case_id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise EvolutionError("case ids must be unique")
        object.__setattr__(self, "cases", cases)
        object.__setattr__(
            self, "workload_sha256", require_sha256(self.workload_sha256, "workload_sha256")
        )
        feature_names = {feature for case in cases for feature in case.features}
        for case in cases:
            if set(case.features) != feature_names:
                raise EvolutionError("all cases must expose the same feature names")

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(sorted({feature for case in self.cases for feature in case.features}))

    @property
    def sha256(self) -> str:
        return quantized_content_sha256(self.payload())

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
            "workload_id": self.workload_id,
            "workload_version": self.workload_version,
            "workload_sha256": self.workload_sha256,
            "cases": [case.to_dict() for case in self.cases],
        }

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ReferenceWorkloadEvidence:
        cases = value["cases"]
        if not isinstance(cases, Sequence) or isinstance(cases, str | bytes | bytearray):
            raise TypeError("cases must be a sequence")
        parsed: list[WorkloadCaseEvidence] = []
        for item in cases:
            if not isinstance(item, Mapping):
                raise TypeError("case evidence must be a mapping")
            parsed.append(WorkloadCaseEvidence.from_dict(item))
        return cls(
            schema_version=str(value["schema_version"]),
            generator_version=str(value["generator_version"]),
            workload_id=str(value["workload_id"]),
            workload_version=str(value["workload_version"]),
            workload_sha256=str(value["workload_sha256"]),
            cases=tuple(parsed),
        )


def load_reference_workload_evidence() -> ReferenceWorkloadEvidence:
    """Derive frozen evidence from the canonical reference workload manifest.

    Reads only the frozen workload manifest and the verified scenario
    registry (pure contracts and known answers). It never constructs or runs a
    simulator engine, keeps research off the simulation hot path, and returns
    the same evidence bytes for the same package state.
    """
    manifest = canonical_reference_workload_manifest()
    registry = canonical_reference_scenario_registry()
    cases: list[WorkloadCaseEvidence] = []
    for case in manifest.cases:
        record = registry.resolve(case)
        scenario = record.scenario
        initial_world = scenario.initial_world
        metrics = record.known_answer.metrics
        cases.append(
            WorkloadCaseEvidence(
                case_id=case.case_id,
                features={
                    "contestant_count": float(len(case.contestant_ids)),
                    "unit_count": float(sum(len(player.units) for player in initial_world.players)),
                    "turn_count": float(len(scenario.turns)),
                    "obstacle_count": float(len(initial_world.terrain.obstacles)),
                    "resource_cell_count": float(len(initial_world.terrain.resource_cells)),
                    "events": float(metrics.get("events", 0.0)),
                    "rng_draws": float(metrics.get("rng_draws", 0.0)),
                },
                target=float(record.known_answer.ticks_completed),
            )
        )
    return ReferenceWorkloadEvidence(
        schema_version=REFERENCE_EVIDENCE_SCHEMA,
        generator_version=REFERENCE_EVIDENCE_GENERATOR_VERSION,
        workload_id=manifest.workload_id,
        workload_version=manifest.workload_version,
        workload_sha256=manifest.sha256,
        cases=tuple(cases),
    )


def reference_complexity_genome_spec(
    evidence: ReferenceWorkloadEvidence,
) -> GenomeSpec:
    """Genome spec for the linear complexity policy: one weight per feature + intercept."""
    genes: list[GeneSpec] = [
        GeneSpec(name=feature, low=-_WEIGHT_BOUND, high=_WEIGHT_BOUND)
        for feature in evidence.feature_names
    ]
    genes.append(GeneSpec(name="intercept", low=-_INTERCEPT_BOUND, high=_INTERCEPT_BOUND))
    return GenomeSpec(tuple(genes))


def _genome_spec_sha256(genome: Genome, expected: GenomeSpec) -> None:
    if genome.spec.sha256 != expected.sha256:
        raise EvolutionError("genome spec does not match the expected policy specification")


class ReferenceComplexityFitness:
    """Corpus-bound deterministic fitness for the reference complexity policy.

    The policy is a linear predictor over frozen workload case features; the
    last gene is the intercept. Fitness is the negative mean absolute
    prediction error over the bound corpus, so higher is better and identical
    inputs produce an identical fitness.
    """

    def __init__(
        self,
        *,
        evidence: ReferenceWorkloadEvidence,
        corpus_ids: Sequence[str],
        spec: GenomeSpec,
    ) -> None:
        corpus = tuple(
            dict.fromkeys(require_identifier(item, "corpus case id") for item in corpus_ids)
        )
        if not corpus:
            raise EvolutionError("fitness corpus must not be empty")
        known = set(evidence.case_ids)
        unknown = set(corpus) - known
        if unknown:
            raise EvolutionError(
                f"corpus references unknown case ids: {', '.join(sorted(unknown))}"
            )
        self._evidence = evidence
        self._corpus = corpus
        self._spec = spec
        self._feature_names = evidence.feature_names
        self._feature_index = {name: index for index, name in enumerate(self._feature_names)}
        self._intercept_index = len(self._feature_names)
        if spec.gene_count != len(self._feature_names) + 1:
            raise EvolutionError("policy spec must have one gene per feature plus an intercept")

    @property
    def corpus_ids(self) -> tuple[str, ...]:
        return self._corpus

    def evaluate(self, genome: Genome) -> float:
        _genome_spec_sha256(genome, self._spec)
        total_error = 0.0
        for case in self._evidence.cases:
            if case.case_id not in self._corpus:
                continue
            score = genome.values[self._intercept_index]
            for feature_name, value in case.features.items():
                weight = genome.values[self._feature_index[feature_name]]
                score += weight * value
            total_error += abs(score - case.target)
        return -total_error / len(self._corpus)


def reference_evolution_config(
    *,
    run_id: str,
    evidence: ReferenceWorkloadEvidence,
    evolution_corpus: Sequence[str],
    holdout_corpus: Sequence[str],
    seed: int,
    population_size: int = 12,
    generations: int = 8,
    tournament_size: int = 3,
    elitism: int = 2,
    crossover_rate: float = 0.8,
    mutation_rate: float = 0.25,
    mutation_strength: float = 0.15,
) -> EvolutionConfig:
    """Build a frozen config for the reference complexity policy.

    The config binds the frozen workload identity and requires disjoint,
    non-empty evolution and holdout corpora.
    """
    return EvolutionConfig(
        run_id=run_id,
        spec=reference_complexity_genome_spec(evidence),
        seed=seed,
        population_size=population_size,
        generations=generations,
        tournament_size=tournament_size,
        elitism=elitism,
        crossover_rate=crossover_rate,
        mutation_rate=mutation_rate,
        mutation_strength=mutation_strength,
        evolution_corpus=tuple(evolution_corpus),
        holdout_corpus=tuple(holdout_corpus),
        workload_id=evidence.workload_id,
        workload_version=evidence.workload_version,
        workload_sha256=evidence.workload_sha256,
    )
