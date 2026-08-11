"""Deterministic genetic-algorithm evolution core (P3-13).

The GA evolves parameterized strategies (genomes) against a caller-supplied,
deterministic fitness function. It is stdlib-only, fully seeded, and records
the complete evolution trajectory in a content-addressed report, so the same
seed and inputs always produce the same evolution results.

Holdout independence is structural, not advisory:

- ``EvolutionConfig`` requires the evolution corpus and the holdout corpus to
  be disjoint and both non-empty.
- Every ``FitnessEvaluator`` is bound to exactly one corpus
  (``corpus_ids``). ``run_evolution`` refuses an evolution evaluator whose
  corpus differs from ``config.evolution_corpus`` and a holdout evaluator
  whose corpus differs from ``config.holdout_corpus``.
- The evolution loop therefore never observes holdout case ids, and the
  holdout evaluation only ever observes the holdout corpus.

The core is generic: any deterministic evaluator implementing
:class:`FitnessEvaluator` works, including simulator-backed evaluators that a
caller wires outside this package. The package ships a reference evaluator
over the frozen reference workload evidence in
:mod:`arena_hero_research.reference_evolution`.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from arena_hero_research.validation import (
    require_float,
    require_identifier,
    require_int,
    require_json_mapping,
    require_sequence,
    require_sha256,
    require_text,
)
from arena_hero_sim.serialization import JsonValue, content_sha256, quantized_content_sha256

EVOLUTION_REPORT_SCHEMA: Final = "arena.research.evolution-report.v1"
EVOLUTION_GENERATOR_VERSION: Final = "0.1.0"


class EvolutionError(ValueError):
    """Raised when an evolution run violates its frozen, deterministic contract."""


class GeneKind(StrEnum):
    """Supported gene value kinds for a genome specification."""

    CONTINUOUS = "continuous"
    INTEGER = "integer"


@dataclass(frozen=True, slots=True)
class GeneSpec:
    """One bounded gene in a genome specification."""

    name: str
    low: float
    high: float
    kind: GeneKind = GeneKind.CONTINUOUS

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, "gene name"))
        low = require_float(self.low, "gene low bound")
        high = require_float(self.high, "gene high bound")
        if not math.isfinite(low) or not math.isfinite(high):
            raise EvolutionError("gene bounds must be finite")
        if low >= high:
            raise EvolutionError("gene low bound must be strictly below the high bound")
        if not isinstance(self.kind, GeneKind):
            raise EvolutionError("gene kind must be continuous or integer")
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"name": self.name, "low": self.low, "high": self.high, "kind": self.kind.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> GeneSpec:
        kind = str(value["kind"])
        if kind not in {item.value for item in GeneKind}:
            raise EvolutionError(f"unsupported gene kind: {kind!r}")
        return cls(
            name=str(value["name"]),
            low=require_float(value["low"], "low"),
            high=require_float(value["high"], "high"),
            kind=GeneKind(kind),
        )


@dataclass(frozen=True, slots=True)
class GenomeSpec:
    """Ordered, validated set of genes shared by every genome in one run."""

    genes: tuple[GeneSpec, ...]

    def __post_init__(self) -> None:
        genes = tuple(self.genes)
        if not genes:
            raise EvolutionError("a genome specification requires at least one gene")
        names = [gene.name for gene in genes]
        if len(names) != len(set(names)):
            raise EvolutionError("gene names must be unique")
        object.__setattr__(self, "genes", genes)

    @property
    def gene_count(self) -> int:
        return len(self.genes)

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())

    def random_genome(self, rng: random.Random) -> Genome:
        """Sample one uniform random genome from this specification."""
        values = tuple(self._sample_gene(rng, gene) for gene in self.genes)
        return Genome(self, values)

    @staticmethod
    def _sample_gene(rng: random.Random, gene: GeneSpec) -> float:
        value = gene.low + rng.random() * (gene.high - gene.low)
        if gene.kind is GeneKind.INTEGER:
            return float(round(value))
        return value

    def to_dict(self) -> dict[str, JsonValue]:
        return {"genes": [gene.to_dict() for gene in self.genes]}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> GenomeSpec:
        genes = require_sequence(value["genes"], "genes")
        parsed: list[GeneSpec] = []
        for item in genes:
            if not isinstance(item, Mapping):
                raise TypeError("gene must be a mapping")
            parsed.append(GeneSpec.from_dict(item))
        return cls(tuple(parsed))


@dataclass(frozen=True, slots=True)
class Genome:
    """One immutable candidate strategy: values aligned to a genome spec."""

    spec: GenomeSpec
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.values) != self.spec.gene_count:
            raise EvolutionError("genome values must match the genome specification")
        normalized = tuple(require_float(item, "genome value") for item in self.values)
        for gene, value in zip(self.spec.genes, normalized, strict=True):
            if not gene.low <= value <= gene.high:
                raise EvolutionError(f"genome value for {gene.name} is outside its bounds")
            if gene.kind is GeneKind.INTEGER and not value.is_integer():
                raise EvolutionError(f"genome value for {gene.name} must be an integer")
        object.__setattr__(self, "values", normalized)

    @property
    def sha256(self) -> str:
        return quantized_content_sha256({"spec_sha256": self.spec.sha256, "values": self.values})

    def to_dict(self) -> dict[str, JsonValue]:
        return {"spec_sha256": self.spec.sha256, "values": list(self.values)}

    @classmethod
    def from_values(cls, spec: GenomeSpec, values: Sequence[float]) -> Genome:
        return cls(spec, tuple(require_float(item, "genome value") for item in values))

    @classmethod
    def from_dict(cls, spec: GenomeSpec, value: Mapping[str, object]) -> Genome:
        values = require_sequence(value["values"], "values")
        return cls.from_values(spec, tuple(require_float(item, "genome value") for item in values))


@dataclass(frozen=True, slots=True)
class EvolutionConfig:
    """Frozen configuration for one deterministic evolution run.

    The evolution corpus is the only evidence the evolution loop may use; the
    holdout corpus is reserved for the final evaluation. Both must be
    non-empty and disjoint, which makes the holdout independent by
    construction.
    """

    run_id: str
    spec: GenomeSpec
    seed: int
    population_size: int
    generations: int
    tournament_size: int
    elitism: int
    crossover_rate: float
    mutation_rate: float
    mutation_strength: float
    evolution_corpus: tuple[str, ...]
    holdout_corpus: tuple[str, ...]
    maximize: bool = True
    workload_id: str | None = None
    workload_version: str | None = None
    workload_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", require_identifier(self.run_id, "run_id"))
        if not isinstance(self.spec, GenomeSpec):
            raise EvolutionError("spec must be a GenomeSpec")
        if self.seed < 0:
            raise EvolutionError("seed must be non-negative")
        population_size = require_int(self.population_size, "population_size")
        generations = require_int(self.generations, "generations")
        tournament_size = require_int(self.tournament_size, "tournament_size")
        elitism = require_int(self.elitism, "elitism")
        if population_size < 2:
            raise EvolutionError("population_size must be at least two")
        if generations < 1:
            raise EvolutionError("generations must be at least one")
        if not 2 <= tournament_size <= population_size:
            raise EvolutionError("tournament_size must be between two and population_size")
        if not 0 <= elitism < population_size:
            raise EvolutionError("elitism must be below population_size")
        object.__setattr__(self, "population_size", population_size)
        object.__setattr__(self, "generations", generations)
        object.__setattr__(self, "tournament_size", tournament_size)
        object.__setattr__(self, "elitism", elitism)
        for name in ("crossover_rate", "mutation_rate", "mutation_strength"):
            value = require_float(getattr(self, name), name)
            if not math.isfinite(value):
                raise EvolutionError(f"{name} must be finite")
            if name == "mutation_strength" and value <= 0:
                raise EvolutionError("mutation_strength must be positive")
            if name != "mutation_strength" and not 0 <= value <= 1:
                raise EvolutionError(f"{name} must be between zero and one")
            object.__setattr__(self, name, value)
        evolution_corpus = tuple(
            dict.fromkeys(
                require_identifier(item, "evolution corpus case id")
                for item in self.evolution_corpus
            )
        )
        holdout_corpus = tuple(
            dict.fromkeys(
                require_identifier(item, "holdout corpus case id") for item in self.holdout_corpus
            )
        )
        if not evolution_corpus:
            raise EvolutionError("evolution_corpus must not be empty")
        if not holdout_corpus:
            raise EvolutionError("holdout_corpus must not be empty")
        if set(evolution_corpus) & set(holdout_corpus):
            raise EvolutionError("evolution_corpus and holdout_corpus must be disjoint")
        object.__setattr__(self, "evolution_corpus", evolution_corpus)
        object.__setattr__(self, "holdout_corpus", holdout_corpus)
        if not isinstance(self.maximize, bool):
            raise EvolutionError("maximize must be a boolean")
        for name in ("workload_id", "workload_version"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_text(value, name))
        if self.workload_sha256 is not None:
            object.__setattr__(
                self, "workload_sha256", require_sha256(self.workload_sha256, "workload_sha256")
            )

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "spec": self.spec.to_dict(),
            "seed": self.seed,
            "population_size": self.population_size,
            "generations": self.generations,
            "tournament_size": self.tournament_size,
            "elitism": self.elitism,
            "crossover_rate": self.crossover_rate,
            "mutation_rate": self.mutation_rate,
            "mutation_strength": self.mutation_strength,
            "evolution_corpus": list(self.evolution_corpus),
            "holdout_corpus": list(self.holdout_corpus),
            "maximize": self.maximize,
            "workload_id": self.workload_id,
            "workload_version": self.workload_version,
            "workload_sha256": self.workload_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvolutionConfig:
        return cls(
            run_id=str(value["run_id"]),
            spec=GenomeSpec.from_dict(require_json_mapping(value["spec"], "spec")),
            seed=require_int(value["seed"], "seed"),
            population_size=require_int(value["population_size"], "population_size"),
            generations=require_int(value["generations"], "generations"),
            tournament_size=require_int(value["tournament_size"], "tournament_size"),
            elitism=require_int(value["elitism"], "elitism"),
            crossover_rate=require_float(value["crossover_rate"], "crossover_rate"),
            mutation_rate=require_float(value["mutation_rate"], "mutation_rate"),
            mutation_strength=require_float(value["mutation_strength"], "mutation_strength"),
            evolution_corpus=tuple(
                str(item)
                for item in require_sequence(value["evolution_corpus"], "evolution_corpus")
            ),
            holdout_corpus=tuple(
                str(item) for item in require_sequence(value["holdout_corpus"], "holdout_corpus")
            ),
            maximize=bool(value["maximize"]),
            workload_id=(None if value.get("workload_id") is None else str(value["workload_id"])),
            workload_version=(
                None if value.get("workload_version") is None else str(value["workload_version"])
            ),
            workload_sha256=(
                None if value.get("workload_sha256") is None else str(value["workload_sha256"])
            ),
        )


@dataclass(frozen=True, slots=True)
class GenerationRecord:
    """Deterministic summary of one evolution generation."""

    generation: int
    best_fitness: float
    mean_fitness: float
    best_values: tuple[float, ...]
    best_sha256: str
    population_sha256: str

    def __post_init__(self) -> None:
        generation = require_int(self.generation, "generation")
        if generation < 0:
            raise EvolutionError("generation must be non-negative")
        for name in ("best_fitness", "mean_fitness"):
            value = require_float(getattr(self, name), name)
            if not math.isfinite(value):
                raise EvolutionError(f"{name} must be finite")
        best_sha256 = require_sha256(self.best_sha256, "best_sha256")
        population_sha256 = require_sha256(self.population_sha256, "population_sha256")
        object.__setattr__(self, "generation", generation)
        object.__setattr__(
            self,
            "best_values",
            tuple(require_float(item, "best value") for item in self.best_values),
        )
        object.__setattr__(self, "best_sha256", best_sha256)
        object.__setattr__(self, "population_sha256", population_sha256)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "generation": self.generation,
            "best_fitness": self.best_fitness,
            "mean_fitness": self.mean_fitness,
            "best_values": list(self.best_values),
            "best_sha256": self.best_sha256,
            "population_sha256": self.population_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> GenerationRecord:
        best_values = require_sequence(value["best_values"], "best_values")
        return cls(
            generation=require_int(value["generation"], "generation"),
            best_fitness=require_float(value["best_fitness"], "best_fitness"),
            mean_fitness=require_float(value["mean_fitness"], "mean_fitness"),
            best_values=tuple(require_float(item, "best value") for item in best_values),
            best_sha256=str(value["best_sha256"]),
            population_sha256=str(value["population_sha256"]),
        )


@dataclass(frozen=True, slots=True)
class EvolutionReport:
    """Content-addressed record of one complete deterministic evolution run."""

    schema_version: str
    generator_version: str
    config: EvolutionConfig
    initial_population_sha256: str
    generations: tuple[GenerationRecord, ...]
    best_values: tuple[float, ...]
    best_sha256: str
    best_fitness: float
    holdout_best_fitness: float
    canonical_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema_version", require_text(self.schema_version, "schema_version")
        )
        object.__setattr__(
            self, "generator_version", require_text(self.generator_version, "generator_version")
        )
        if not isinstance(self.config, EvolutionConfig):
            raise EvolutionError("config must be an EvolutionConfig")
        generations = tuple(self.generations)
        if not generations:
            raise EvolutionError("an evolution report requires at least one generation")
        for record in generations:
            if not isinstance(record, GenerationRecord):
                raise EvolutionError("generation records must be GenerationRecord instances")
        indices = [record.generation for record in generations]
        if indices != sorted(indices) or len(indices) != len(set(indices)):
            raise EvolutionError("generation records must be ordered and unique")
        object.__setattr__(self, "generations", generations)
        best_values = tuple(require_float(item, "best value") for item in self.best_values)
        if len(best_values) != self.config.spec.gene_count:
            raise EvolutionError("best genome values must match the config genome spec")
        object.__setattr__(self, "best_values", best_values)
        expected_best_sha256 = quantized_content_sha256(
            {"spec_sha256": self.config.spec.sha256, "values": best_values}
        )
        if expected_best_sha256 != self.best_sha256:
            raise EvolutionError("best_sha256 must match the best genome values")
        for name in ("best_fitness", "holdout_best_fitness"):
            value = require_float(getattr(self, name), name)
            if not math.isfinite(value):
                raise EvolutionError(f"{name} must be finite")
        object.__setattr__(self, "best_sha256", require_sha256(self.best_sha256, "best_sha256"))
        object.__setattr__(
            self,
            "initial_population_sha256",
            require_sha256(self.initial_population_sha256, "initial_population_sha256"),
        )
        object.__setattr__(
            self, "canonical_sha256", require_sha256(self.canonical_sha256, "canonical_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
            "config": self.config.to_dict(),
            "initial_population_sha256": self.initial_population_sha256,
            "generations": [record.to_dict() for record in self.generations],
            "best_values": list(self.best_values),
            "best_sha256": self.best_sha256,
            "best_fitness": self.best_fitness,
            "holdout_best_fitness": self.holdout_best_fitness,
        }

    def verify(self) -> bool:
        return quantized_content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvolutionReport:
        return cls(
            schema_version=str(value["schema_version"]),
            generator_version=str(value["generator_version"]),
            config=EvolutionConfig.from_dict(require_json_mapping(value["config"], "config")),
            initial_population_sha256=str(value["initial_population_sha256"]),
            generations=_parse_generation_records(
                require_sequence(value["generations"], "generations")
            ),
            best_values=tuple(
                require_float(item, "best value")
                for item in require_sequence(value["best_values"], "best_values")
            ),
            best_sha256=str(value["best_sha256"]),
            best_fitness=require_float(value["best_fitness"], "best_fitness"),
            holdout_best_fitness=require_float(
                value["holdout_best_fitness"], "holdout_best_fitness"
            ),
            canonical_sha256=str(value["canonical_sha256"]),
        )


def _parse_generation_records(items: Sequence[object]) -> tuple[GenerationRecord, ...]:
    parsed: list[GenerationRecord] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError("generation record must be a mapping")
        parsed.append(GenerationRecord.from_dict(item))
    return tuple(parsed)


class FitnessEvaluator(Protocol):
    """Deterministic fitness function bound to exactly one corpus of case ids."""

    @property
    def corpus_ids(self) -> tuple[str, ...]:
        """The only case ids this evaluator may use; never the holdout."""

    def evaluate(self, genome: Genome) -> float:
        """Return a deterministic fitness for one genome, higher is better."""


def _fitness_key(item: tuple[Genome, float], *, maximize: bool) -> tuple[float, str]:
    fitness, genome = item[1], item[0]
    return (-fitness if maximize else fitness, genome.sha256)


def _better(
    candidate: tuple[Genome, float], current: tuple[Genome, float], *, maximize: bool
) -> bool:
    candidate_key = _fitness_key(candidate, maximize=maximize)
    current_key = _fitness_key(current, maximize=maximize)
    return candidate_key < current_key


def _tournament(
    rng: random.Random,
    items: Sequence[tuple[Genome, float]],
    *,
    maximize: bool,
    tournament_size: int,
) -> Genome:
    best: tuple[Genome, float] | None = None
    for _ in range(tournament_size):
        candidate = items[rng.randrange(len(items))]
        if best is None or _better(candidate, best, maximize=maximize):
            best = candidate
    assert best is not None
    return best[0]


def _crossover(
    rng: random.Random,
    config: EvolutionConfig,
    parent_a: Genome,
    parent_b: Genome,
) -> Genome:
    if rng.random() >= config.crossover_rate:
        return parent_a
    values = tuple(
        a_value if rng.random() < 0.5 else b_value
        for a_value, b_value in zip(parent_a.values, parent_b.values, strict=True)
    )
    return Genome(config.spec, values)


def _mutate(rng: random.Random, config: EvolutionConfig, genome: Genome) -> Genome:
    mutated: list[float] = []
    for gene, value in zip(config.spec.genes, genome.values, strict=True):
        if rng.random() >= config.mutation_rate:
            mutated.append(value)
            continue
        noise = rng.gauss(0.0, config.mutation_strength * (gene.high - gene.low))
        candidate = value + noise
        if gene.kind is GeneKind.INTEGER:
            candidate = round(candidate)
        candidate = min(gene.high, max(gene.low, candidate))
        if gene.kind is GeneKind.INTEGER:
            candidate = min(gene.high, max(gene.low, round(candidate)))
        mutated.append(candidate)
    return Genome(config.spec, tuple(mutated))


def run_evolution(
    *,
    config: EvolutionConfig,
    evaluator: FitnessEvaluator,
    holdout_evaluator: FitnessEvaluator,
) -> EvolutionReport:
    """Run one deterministic evolution and evaluate the best genome on holdout.

    The evolution loop evaluates every genome only through ``evaluator``,
    which must be bound to exactly ``config.evolution_corpus``. The final best
    genome is evaluated once through ``holdout_evaluator``, which must be
    bound to exactly ``config.holdout_corpus``. Any binding mismatch fails
    closed before evaluation so holdout ids can never reach the evolution loop.
    """
    if tuple(evaluator.corpus_ids) != config.evolution_corpus:
        raise EvolutionError(
            "evolution evaluator corpus must equal config.evolution_corpus; "
            "holdout ids must never reach the evolution loop"
        )
    if tuple(holdout_evaluator.corpus_ids) != config.holdout_corpus:
        raise EvolutionError("holdout evaluator corpus must equal config.holdout_corpus")

    rng = random.Random(config.seed)
    population = [config.spec.random_genome(rng) for _ in range(config.population_size)]
    initial_population_sha256 = quantized_content_sha256(
        {"genomes": [genome.to_dict() for genome in population]}
    )

    generations: list[GenerationRecord] = []
    global_best: tuple[Genome, float] | None = None
    for generation in range(config.generations):
        fitnesses = [evaluator.evaluate(genome) for genome in population]
        ranked = sorted(
            zip(population, fitnesses, strict=True),
            key=lambda item: _fitness_key(item, maximize=config.maximize),
        )
        best_genome, best_fitness = ranked[0]
        mean_fitness = sum(fitnesses) / len(fitnesses)
        if global_best is None or _better(
            (best_genome, best_fitness), global_best, maximize=config.maximize
        ):
            global_best = (best_genome, best_fitness)
        generations.append(
            GenerationRecord(
                generation=generation,
                best_fitness=best_fitness,
                mean_fitness=mean_fitness,
                best_values=best_genome.values,
                best_sha256=best_genome.sha256,
                population_sha256=quantized_content_sha256(
                    {"genomes": [genome.to_dict() for genome in population]}
                ),
            )
        )
        next_population = [genome for genome, _ in ranked[: config.elitism]]
        while len(next_population) < config.population_size:
            parent_a = _tournament(
                rng, ranked, maximize=config.maximize, tournament_size=config.tournament_size
            )
            parent_b = _tournament(
                rng, ranked, maximize=config.maximize, tournament_size=config.tournament_size
            )
            child = _crossover(rng, config, parent_a, parent_b)
            child = _mutate(rng, config, child)
            next_population.append(child)
        population = next_population

    assert global_best is not None
    best_genome, best_fitness = global_best
    holdout_best_fitness = holdout_evaluator.evaluate(best_genome)

    payload = {
        "schema_version": EVOLUTION_REPORT_SCHEMA,
        "generator_version": EVOLUTION_GENERATOR_VERSION,
        "config": config.to_dict(),
        "initial_population_sha256": initial_population_sha256,
        "generations": [record.to_dict() for record in generations],
        "best_values": list(best_genome.values),
        "best_sha256": best_genome.sha256,
        "best_fitness": best_fitness,
        "holdout_best_fitness": holdout_best_fitness,
    }
    return EvolutionReport(
        schema_version=EVOLUTION_REPORT_SCHEMA,
        generator_version=EVOLUTION_GENERATOR_VERSION,
        config=config,
        initial_population_sha256=initial_population_sha256,
        generations=tuple(generations),
        best_values=best_genome.values,
        best_sha256=best_genome.sha256,
        best_fitness=best_fitness,
        holdout_best_fitness=holdout_best_fitness,
        canonical_sha256=quantized_content_sha256(payload),
    )
