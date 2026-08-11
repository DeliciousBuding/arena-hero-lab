"""Deterministic GA/evolution and holdout independence tests (P3-13)."""

from __future__ import annotations

import random

import pytest

from arena_hero_research.evolution import (
    EVOLUTION_REPORT_SCHEMA,
    EvolutionConfig,
    EvolutionError,
    EvolutionReport,
    FitnessEvaluator,
    GeneKind,
    GenerationRecord,
    GeneSpec,
    Genome,
    GenomeSpec,
    run_evolution,
)
from arena_hero_research.reference_evolution import (
    REFERENCE_EVIDENCE_SCHEMA,
    ReferenceComplexityFitness,
    ReferenceWorkloadEvidence,
    WorkloadCaseEvidence,
    load_reference_workload_evidence,
    reference_complexity_genome_spec,
    reference_evolution_config,
)
from arena_hero_sim.reference_workload import CANONICAL_REFERENCE_WORKLOAD_SHA256
from arena_hero_sim.serialization import quantized_content_sha256


def _spec() -> GenomeSpec:
    return GenomeSpec(
        (
            GeneSpec(name="attack", low=0.0, high=1.0),
            GeneSpec(name="defense", low=-1.0, high=1.0),
            GeneSpec(name="count", low=0.0, high=5.0, kind=GeneKind.INTEGER),
        )
    )


def _config(
    *,
    evolution_corpus: tuple[str, ...] = ("a", "b"),
    holdout_corpus: tuple[str, ...] = ("c",),
    seed: int = 7,
    population_size: int = 8,
    generations: int = 4,
    tournament_size: int = 3,
    elitism: int = 2,
    crossover_rate: float = 0.8,
    mutation_rate: float = 0.2,
    mutation_strength: float = 0.2,
    maximize: bool = True,
    spec: GenomeSpec | None = None,
) -> EvolutionConfig:
    return EvolutionConfig(
        run_id="run-p3-13",
        spec=spec or _spec(),
        seed=seed,
        population_size=population_size,
        generations=generations,
        tournament_size=tournament_size,
        elitism=elitism,
        crossover_rate=crossover_rate,
        mutation_rate=mutation_rate,
        mutation_strength=mutation_strength,
        evolution_corpus=evolution_corpus,
        holdout_corpus=holdout_corpus,
        maximize=maximize,
    )


class ConstantFitness:
    """Deterministic evaluator with a fixed per-genome value."""

    def __init__(self, value: float = 1.0, corpus_ids: tuple[str, ...] = ("a", "b")) -> None:
        self._value = value
        self._corpus_ids = corpus_ids

    @property
    def corpus_ids(self) -> tuple[str, ...]:
        return self._corpus_ids

    def evaluate(self, genome: Genome) -> float:
        return self._value


class RecordingFitness(FitnessEvaluator):
    """Evaluator that records every corpus observed per evaluate call."""

    def __init__(self, corpus_ids: tuple[str, ...], value: float = 0.0) -> None:
        self._corpus_ids = corpus_ids
        self._value = value
        self.observed_corpora: list[tuple[str, ...]] = []

    @property
    def corpus_ids(self) -> tuple[str, ...]:
        return self._corpus_ids

    def evaluate(self, genome: Genome) -> float:
        self.observed_corpora.append(self._corpus_ids)
        return self._value


def _synthetic_evidence() -> ReferenceWorkloadEvidence:
    return ReferenceWorkloadEvidence(
        schema_version=REFERENCE_EVIDENCE_SCHEMA,
        generator_version="0.1.0",
        workload_id="synthetic",
        workload_version="v1",
        workload_sha256="0" * 64,
        cases=(
            WorkloadCaseEvidence(
                case_id="a",
                features={"contestant_count": 2.0, "turn_count": 1.0},
                target=1.0,
            ),
            WorkloadCaseEvidence(
                case_id="b",
                features={"contestant_count": 3.0, "turn_count": 5.0},
                target=5.0,
            ),
            WorkloadCaseEvidence(
                case_id="c",
                features={"contestant_count": 2.0, "turn_count": 1.0},
                target=1000.0,
            ),
        ),
    )


def test_genome_spec_validation() -> None:
    with pytest.raises(EvolutionError, match="at least one gene"):
        GenomeSpec(())
    with pytest.raises(EvolutionError, match="unique"):
        GenomeSpec((GeneSpec("x", 0.0, 1.0), GeneSpec("x", 0.0, 1.0)))
    with pytest.raises(EvolutionError, match="below"):
        GeneSpec(name="x", low=1.0, high=1.0)
    with pytest.raises(EvolutionError, match="unique"):
        _config(spec=GenomeSpec((GeneSpec("x", 0.0, 1.0), GeneSpec("x", 0.0, 1.0))))


def test_genome_values_validated_against_spec() -> None:
    spec = _spec()
    with pytest.raises(EvolutionError, match="match the genome specification"):
        Genome(spec, (0.5,))
    with pytest.raises(EvolutionError, match="outside its bounds"):
        Genome.from_values(spec, (2.0, 0.0, 1.0))
    with pytest.raises(EvolutionError, match="integer"):
        Genome.from_values(spec, (0.5, 0.0, 1.5))


def test_random_genome_respects_bounds_and_kinds() -> None:
    spec = _spec()
    rng = random.Random(11)
    for _ in range(50):
        genome = spec.random_genome(rng)
        for gene, value in zip(spec.genes, genome.values, strict=True):
            assert gene.low <= value <= gene.high
            if gene.kind is GeneKind.INTEGER:
                assert value.is_integer()
    # The same seeded rng produces the same genome sequence.
    first = [spec.random_genome(random.Random(11)).values for _ in range(3)]
    second = [spec.random_genome(random.Random(11)).values for _ in range(3)]
    assert first == second


def test_evolution_config_holdout_corpora_must_be_disjoint() -> None:
    with pytest.raises(EvolutionError, match="disjoint"):
        _config(evolution_corpus=("a", "b"), holdout_corpus=("b", "c"))
    with pytest.raises(EvolutionError, match="evolution_corpus must not be empty"):
        _config(evolution_corpus=())
    with pytest.raises(EvolutionError, match="holdout_corpus must not be empty"):
        _config(holdout_corpus=())


def test_evolution_config_bounds_validation() -> None:
    with pytest.raises(EvolutionError, match="at least two"):
        _config(population_size=1)
    with pytest.raises(EvolutionError, match="tournament_size"):
        _config(tournament_size=1)
    with pytest.raises(EvolutionError, match="elitism"):
        _config(elitism=8)
    with pytest.raises(EvolutionError, match="mutation_strength"):
        _config(mutation_strength=0.0)
    with pytest.raises(EvolutionError, match="between zero and one"):
        _config(crossover_rate=1.5)


def test_evolution_config_round_trip() -> None:
    config = _config(seed=3)
    restored = EvolutionConfig.from_dict(config.to_dict())
    assert restored.to_dict() == config.to_dict()
    assert restored.sha256 == config.sha256


def test_same_seed_same_input_produces_identical_report() -> None:
    config = _config(seed=42)
    evaluator = RecordingFitness(("a", "b"), value=0.25)
    holdout = RecordingFitness(("c",), value=-0.5)
    first = run_evolution(config=config, evaluator=evaluator, holdout_evaluator=holdout)
    second = run_evolution(config=config, evaluator=evaluator, holdout_evaluator=holdout)
    assert first.to_dict() == second.to_dict()
    assert first.canonical_sha256 == second.canonical_sha256
    assert first.verify()
    assert second.verify()


def test_different_seed_changes_trajectory_digest() -> None:
    evaluator = RecordingFitness(("a", "b"), value=0.25)
    holdout = RecordingFitness(("c",), value=-0.5)
    first = run_evolution(config=_config(seed=1), evaluator=evaluator, holdout_evaluator=holdout)
    second = run_evolution(config=_config(seed=2), evaluator=evaluator, holdout_evaluator=holdout)
    assert first.canonical_sha256 != second.canonical_sha256


def test_evolution_loop_runs_exact_evaluation_count() -> None:
    config = _config(population_size=6, generations=5)
    evaluator = RecordingFitness(("a", "b"), value=0.25)
    holdout = RecordingFitness(("c",), value=-0.5)
    run_evolution(config=config, evaluator=evaluator, holdout_evaluator=holdout)
    assert len(evaluator.observed_corpora) == config.population_size * config.generations
    assert len(holdout.observed_corpora) == 1


def test_evolution_evaluator_never_observes_holdout_corpus() -> None:
    config = _config(
        evolution_corpus=("a", "b"),
        holdout_corpus=("c",),
        seed=9,
    )
    evaluator = RecordingFitness(("a", "b"), value=0.5)
    holdout = RecordingFitness(("c",), value=-0.25)
    report = run_evolution(config=config, evaluator=evaluator, holdout_evaluator=holdout)
    for corpus in evaluator.observed_corpora:
        assert set(corpus) == {"a", "b"}
        assert "c" not in corpus
    for corpus in holdout.observed_corpora:
        assert set(corpus) == {"c"}
    assert report.config.evolution_corpus == ("a", "b")
    assert report.config.holdout_corpus == ("c",)


def test_run_evolution_rejects_corpus_binding_mismatch() -> None:
    config = _config(evolution_corpus=("a", "b"), holdout_corpus=("c",))
    with pytest.raises(EvolutionError, match="evolution evaluator corpus"):
        run_evolution(
            config=config,
            evaluator=ConstantFitness(corpus_ids=("c",)),
            holdout_evaluator=ConstantFitness(corpus_ids=("c",)),
        )
    with pytest.raises(EvolutionError, match="holdout evaluator corpus"):
        run_evolution(
            config=config,
            evaluator=ConstantFitness(corpus_ids=("a", "b")),
            holdout_evaluator=ConstantFitness(corpus_ids=("a",)),
        )


def test_elitism_preserves_best_fitness_monotonicity() -> None:
    # A fitness that rewards smaller first-gene values; with elitism the best
    # fitness (maximize) must never worsen across generations.
    class LinearFitness(FitnessEvaluator):
        def __init__(self, corpus_ids: tuple[str, ...]) -> None:
            self._corpus_ids = corpus_ids

        @property
        def corpus_ids(self) -> tuple[str, ...]:
            return self._corpus_ids

        def evaluate(self, genome: Genome) -> float:
            return -abs(genome.values[0] - 0.0)

    config = _config(seed=5, population_size=12, generations=8, elitism=2)
    report = run_evolution(
        config=config,
        evaluator=LinearFitness(("a", "b")),
        holdout_evaluator=LinearFitness(("c",)),
    )
    # With elitism the maximizing best fitness must never worsen (non-decreasing).
    best_fitnesses = [record.best_fitness for record in report.generations]
    assert best_fitnesses == sorted(best_fitnesses)


def test_minimize_mode_orders_by_lowest_fitness() -> None:
    class InverseFitness(FitnessEvaluator):
        def __init__(self, corpus_ids: tuple[str, ...]) -> None:
            self._corpus_ids = corpus_ids

        @property
        def corpus_ids(self) -> tuple[str, ...]:
            return self._corpus_ids

        def evaluate(self, genome: Genome) -> float:
            return genome.values[0]

    config = _config(seed=5, population_size=12, generations=6, elitism=2, maximize=False)
    report = run_evolution(
        config=config,
        evaluator=InverseFitness(("a", "b")),
        holdout_evaluator=InverseFitness(("c",)),
    )
    # With elitism the minimizing best fitness must never worsen (non-increasing).
    best_fitnesses = [record.best_fitness for record in report.generations]
    assert best_fitnesses == sorted(best_fitnesses, reverse=True)


def test_report_round_trip_and_verify() -> None:
    config = _config(seed=42)
    report = run_evolution(
        config=config,
        evaluator=ConstantFitness(value=0.5, corpus_ids=("a", "b")),
        holdout_evaluator=ConstantFitness(value=-0.5, corpus_ids=("c",)),
    )
    assert report.schema_version == EVOLUTION_REPORT_SCHEMA
    restored = EvolutionReport.from_dict(report.to_dict())
    assert restored.to_dict() == report.to_dict()
    assert restored.verify()
    assert restored.canonical_sha256 == report.canonical_sha256


def test_report_tamper_fails_verification() -> None:
    config = _config(seed=42)
    report = run_evolution(
        config=config,
        evaluator=ConstantFitness(value=0.5, corpus_ids=("a", "b")),
        holdout_evaluator=ConstantFitness(value=-0.5, corpus_ids=("c",)),
    )
    tampered = report.to_dict()
    tampered["holdout_best_fitness"] = 999.0
    restored = EvolutionReport.from_dict(tampered)
    assert not restored.verify()


def test_report_rejects_best_sha_mismatch() -> None:
    config = _config(seed=42)
    report = run_evolution(
        config=config,
        evaluator=ConstantFitness(value=0.5, corpus_ids=("a", "b")),
        holdout_evaluator=ConstantFitness(value=-0.5, corpus_ids=("c",)),
    )
    payload = report.payload()
    with pytest.raises(EvolutionError, match="best_sha256"):
        EvolutionReport(
            schema_version=str(payload["schema_version"]),
            generator_version=str(payload["generator_version"]),
            config=report.config,
            initial_population_sha256=str(payload["initial_population_sha256"]),
            generations=report.generations,
            best_values=(0.123, *report.best_values[1:]),
            best_sha256=report.best_sha256,
            best_fitness=report.best_fitness,
            holdout_best_fitness=report.holdout_best_fitness,
            canonical_sha256=report.canonical_sha256,
        )


def test_reference_evidence_loads_frozen_canonical_workload() -> None:
    evidence = load_reference_workload_evidence()
    assert evidence.workload_id == "reference-movement-dependency"
    assert evidence.workload_sha256 == CANONICAL_REFERENCE_WORKLOAD_SHA256
    assert len(evidence.cases) == 9
    assert set(evidence.feature_names) == {
        "contestant_count",
        "events",
        "obstacle_count",
        "resource_cell_count",
        "rng_draws",
        "turn_count",
        "unit_count",
    }
    # Evidence is deterministic: two loads produce identical digests.
    assert evidence.to_dict() == load_reference_workload_evidence().to_dict()
    assert evidence.sha256 == load_reference_workload_evidence().sha256
    restored = ReferenceWorkloadEvidence.from_dict(evidence.to_dict())
    assert restored.to_dict() == evidence.to_dict()


def test_reference_fitness_is_corpus_bound() -> None:
    evidence = _synthetic_evidence()
    spec = reference_complexity_genome_spec(evidence)
    fitness = ReferenceComplexityFitness(evidence=evidence, corpus_ids=("a",), spec=spec)
    # Predict target=1 exactly: intercept 1 and zero weights.
    genome = Genome.from_values(spec, (0.0, 0.0, 1.0))
    assert fitness.evaluate(genome) == pytest.approx(0.0)
    # Case "c" (target 1000) must never influence the fitness.
    assert fitness.corpus_ids == ("a",)
    with pytest.raises(EvolutionError, match="unknown case ids"):
        ReferenceComplexityFitness(evidence=evidence, corpus_ids=("missing",), spec=spec)
    with pytest.raises(EvolutionError, match="policy spec"):
        ReferenceComplexityFitness(
            evidence=evidence,
            corpus_ids=("a",),
            spec=GenomeSpec((GeneSpec("x", 0.0, 1.0),)),
        )


def test_reference_fitness_deterministic_and_reproducible() -> None:
    evidence = load_reference_workload_evidence()
    spec = reference_complexity_genome_spec(evidence)
    fitness = ReferenceComplexityFitness(
        evidence=evidence, corpus_ids=("independent-moves",), spec=spec
    )
    genome = spec.random_genome(random.Random(3))
    assert fitness.evaluate(genome) == fitness.evaluate(genome)
    assert quantized_content_sha256({"fitness": fitness.evaluate(genome)}) == (
        quantized_content_sha256({"fitness": fitness.evaluate(genome)})
    )


def test_canonical_reference_evolution_end_to_end() -> None:
    evidence = load_reference_workload_evidence()
    case_ids = evidence.case_ids
    evolution_corpus = tuple(sorted(case_ids)[:6])
    holdout_corpus = tuple(sorted(case_ids)[6:])
    assert set(evolution_corpus) & set(holdout_corpus) == set()
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
    assert config.workload_id == "reference-movement-dependency"
    assert config.workload_sha256 == CANONICAL_REFERENCE_WORKLOAD_SHA256
    assert config.evolution_corpus == evolution_corpus
    assert config.holdout_corpus == holdout_corpus

    evaluator = ReferenceComplexityFitness(
        evidence=evidence, corpus_ids=evolution_corpus, spec=spec
    )
    holdout = ReferenceComplexityFitness(evidence=evidence, corpus_ids=holdout_corpus, spec=spec)
    report = run_evolution(config=config, evaluator=evaluator, holdout_evaluator=holdout)
    assert report.verify()
    assert report.best_sha256 == quantized_content_sha256(
        {"spec_sha256": spec.sha256, "values": report.best_values}
    )
    assert len(report.generations) == config.generations
    for record in report.generations:
        assert isinstance(record, GenerationRecord)
    # Holdout and evolution corpora are recorded and disjoint.
    assert set(report.config.evolution_corpus) == set(evolution_corpus)
    assert set(report.config.holdout_corpus) == set(holdout_corpus)
    assert set(report.config.evolution_corpus) & set(report.config.holdout_corpus) == set()

    # Reproducibility: the same seed reproduces the whole trajectory digest.
    repeat = run_evolution(
        config=reference_evolution_config(
            run_id="p3-13-canonical",
            evidence=evidence,
            evolution_corpus=evolution_corpus,
            holdout_corpus=holdout_corpus,
            seed=20260812,
            population_size=12,
            generations=6,
        ),
        evaluator=ReferenceComplexityFitness(
            evidence=evidence, corpus_ids=evolution_corpus, spec=spec
        ),
        holdout_evaluator=ReferenceComplexityFitness(
            evidence=evidence, corpus_ids=holdout_corpus, spec=spec
        ),
    )
    assert repeat.to_dict() == report.to_dict()
    assert repeat.canonical_sha256 == report.canonical_sha256
