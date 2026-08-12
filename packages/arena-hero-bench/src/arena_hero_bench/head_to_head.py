"""Head-to-head terminal outcome comparator: arena-evolve champion vs Python agent.

The competitive evaluation battery (``arena_hero_bench.competitive_eval``)
answers "does the Python agent reproduce the evolve champion's trajectory"
(MATCH/MISMATCH). This module answers a different question: on the same
scenario and seed, which side ends the match better, measured by terminal
survival, final resources, resource growth, population, and delivered cargo.

Because ``arena-hero-sim`` hosts fixed scripts (not interactive
``Strategy.decide`` agents) and arena-evolve runs inside its own ``ahsim``
engine, the two sides never share a simulator process. The only common,
versioned ground is the world-state record each side leaves behind: the evolve
corpus (``TsLegacyCanonicalRecord``) and the Python agent's ``agent-run-v1``
ticks plus sanitized observation snapshots. This comparator therefore reuses
the same fail-closed loaders as the KPI differential
(:func:`arena_hero_bench.kpi_differential.build_kpi_differential_run`) and then
reads the terminal statistics those loaders already compute, producing a
deterministic per-metric win/loss/tie verdict plus a content-addressed report.

Live paths (documented, not wired here):

- evolve side: inside the arena-evolve checkout, ``python3 deploy.py --local
  --genes genes/evolve_v7_best.json`` runs one full match in its own ``ahsim``
  engine and prints the terminal line (pop/harvest/dmg/alive).
- Python agent side: the external ``arena-hero-agent`` CLI writes
  ``agent-run-v1`` ``ticks.jsonl`` per (scenario, seed, tenant); the battery
  ``agent_runs_dir`` seam already consumes that layout.

Wiring both into one shared map/engine is out of scope today: neither ``ahsim``
nor the Python agent exposes the lab ``arena-hero-sim`` world spec, and the lab
reference engine implements only the harvest/deposit slice (no combat, spawn,
beacon, or win/loss), so no shared interactive simulator exists yet.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from arena_hero_bench.competitive_eval import EvolveSide
from arena_hero_bench.kpi_differential import (
    KpiDimension,
    KpiReport,
    build_kpi_differential_run,
)
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

HEAD_TO_HEAD_SCHEMA: Final = "arena.bench.head-to-head.v1"
HEAD_TO_HEAD_REPORT_SCHEMA: Final = "arena.bench.head-to-head-report.v1"
GENERATOR_VERSION: Final = "0.1.0"
EVIDENCE_KINDS: Final = frozenset({"sanitized_fixture", "production"})
_ALLOWED_PROTOCOLS: Final = frozenset({"agent-run-v1"})
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class HeadToHeadError(ValueError):
    """Base error for the head-to-head outcome comparator."""


class HeadToHeadManifestError(HeadToHeadError):
    """A head-to-head manifest is invalid or unsupported."""


def _strict_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HeadToHeadManifestError(f"{field_name} must be an integer")
    return value


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HeadToHeadManifestError(f"{field_name} must be a non-empty string")
    return value


def _strict_object(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise HeadToHeadManifestError(f"{field_name} must be an object")
    return value


def _strict_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise HeadToHeadManifestError(f"{field_name} must be an array")
    return value


def _identifier(value: object, field_name: str) -> str:
    text = _strict_str(value, field_name)
    if not _IDENTIFIER.fullmatch(text):
        raise HeadToHeadManifestError(f"{field_name} must be a lowercase portable identifier")
    return text


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    narrowed = to_json_value(value)
    if not isinstance(narrowed, dict):
        raise HeadToHeadError("expected a JSON object")
    return narrowed


@dataclass(frozen=True, slots=True)
class HeadToHeadScenario:
    """One scenario of a head-to-head match: the evolve baseline corpus."""

    scenario_id: str
    evolve: EvolveSide


@dataclass(frozen=True, slots=True)
class HeadToHeadContestant:
    """One Python-agent contestant with per-contestant terminal snapshots."""

    contestant_id: str
    version: str
    protocol: str
    records: Path
    observation_snapshots: Path


@dataclass(frozen=True, slots=True)
class HeadToHeadManifest:
    """A fully validated head-to-head match manifest."""

    schema_version: str
    match_id: str
    dataset_id: str
    tenant_id: str
    evidence_kind: str
    scenario: HeadToHeadScenario
    seed_id: str
    contestants: tuple[HeadToHeadContestant, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema_version", _strict_str(self.schema_version, "schema_version")
        )
        if self.schema_version != HEAD_TO_HEAD_SCHEMA:
            raise HeadToHeadManifestError(f"unsupported schemaVersion {self.schema_version!r}")
        object.__setattr__(self, "match_id", _identifier(self.match_id, "match_id"))
        object.__setattr__(self, "dataset_id", _identifier(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "tenant_id", _identifier(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "evidence_kind", _strict_str(self.evidence_kind, "evidence_kind"))
        if self.evidence_kind not in EVIDENCE_KINDS:
            raise HeadToHeadManifestError(f"unsupported evidence_kind {self.evidence_kind!r}")
        object.__setattr__(self, "seed_id", _identifier(self.seed_id, "seed_id"))
        if not self.contestants:
            raise HeadToHeadManifestError("manifest must declare at least one contestant")
        keys = [(contestant.contestant_id, contestant.version) for contestant in self.contestants]
        if len(keys) != len(set(keys)):
            raise HeadToHeadManifestError("contestant (id, version) pairs must be unique")
        for contestant in self.contestants:
            if contestant.protocol not in _ALLOWED_PROTOCOLS:
                raise HeadToHeadManifestError(
                    f"unsupported contestant protocol {contestant.protocol!r}"
                )


def load_head_to_head_manifest(manifest_path: str | Path) -> HeadToHeadManifest:
    """Parse and validate one head-to-head match manifest."""
    path = Path(manifest_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HeadToHeadManifestError(
            f"cannot read head-to-head manifest {path.name}: {exc}"
        ) from exc
    manifest = _strict_object(raw, "head-to-head manifest")
    scenario_raw = _strict_object(manifest.get("scenario"), "scenario")
    scenario_id = _identifier(scenario_raw.get("id"), "scenario.id")
    evolve_raw = _strict_object(scenario_raw.get("evolve"), "scenario.evolve")
    decision_trace = evolve_raw.get("decision_trace")
    evolve = EvolveSide(
        manifest=Path(_strict_str(evolve_raw.get("manifest"), "scenario.evolve.manifest")),
        data_dir=Path(_strict_str(evolve_raw.get("data_dir"), "scenario.evolve.data_dir")),
        decision_trace=(
            None
            if decision_trace is None
            else Path(_strict_str(decision_trace, "scenario.evolve.decision_trace"))
        ),
    )
    contestants_raw = _strict_list(manifest.get("contestants"), "contestants")
    contestants: list[HeadToHeadContestant] = []
    for index, item in enumerate(contestants_raw):
        contestant = _strict_object(item, f"contestants[{index}]")
        contestants.append(
            HeadToHeadContestant(
                contestant_id=_identifier(contestant.get("id"), f"contestants[{index}].id"),
                version=_strict_str(contestant.get("version"), f"contestants[{index}].version"),
                protocol=_strict_str(contestant.get("protocol"), f"contestants[{index}].protocol"),
                records=Path(
                    _strict_str(contestant.get("records"), f"contestants[{index}].records")
                ),
                observation_snapshots=Path(
                    _strict_str(
                        contestant.get("observation_snapshots"),
                        f"contestants[{index}].observation_snapshots",
                    )
                ),
            )
        )
    return HeadToHeadManifest(
        schema_version=_strict_str(manifest.get("schemaVersion"), "schemaVersion"),
        match_id=_strict_str(manifest.get("match_id"), "match_id"),
        dataset_id=_strict_str(manifest.get("dataset_id"), "dataset_id"),
        tenant_id=_strict_str(manifest.get("tenant_id"), "tenant_id"),
        evidence_kind=_strict_str(manifest.get("evidence_kind"), "evidence_kind"),
        scenario=HeadToHeadScenario(scenario_id=scenario_id, evolve=evolve),
        seed_id=_strict_str(manifest.get("seed"), "seed"),
        contestants=tuple(contestants),
    )


@dataclass(frozen=True, slots=True)
class TerminalMetrics:
    """Terminal outcome of one side, read from the KPI differential statistics."""

    final_resources: int | None
    resource_growth: int | None
    population_final: int | None
    unit_count_final: int | None
    enemy_count_final: int | None
    cargo_final: int | None
    core_state: str | None
    core_hp: int | None
    survival_alive: bool
    last_tick: int | None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "final_resources": self.final_resources,
            "resource_growth": self.resource_growth,
            "population_final": self.population_final,
            "unit_count_final": self.unit_count_final,
            "enemy_count_final": self.enemy_count_final,
            "cargo_final": self.cargo_final,
            "core_state": self.core_state,
            "core_hp": self.core_hp,
            "survival_alive": self.survival_alive,
            "last_tick": self.last_tick,
        }


@dataclass(frozen=True, slots=True)
class MetricVerdict:
    """One per-metric win/loss/tie between the evolve baseline and a contestant."""

    metric: str
    evolve_value: JsonValue
    python_value: JsonValue
    winner: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "metric": self.metric,
            "evolve": self.evolve_value,
            "python_agent": self.python_value,
            "winner": self.winner,
        }


@dataclass(frozen=True, slots=True)
class ContestantVerdict:
    """The terminal outcome and aggregate verdict of one contestant."""

    contestant_id: str
    version: str
    scenario_id: str
    seed_id: str
    evolve_terminal: TerminalMetrics
    python_terminal: TerminalMetrics
    verdicts: tuple[MetricVerdict, ...]
    aggregate_winner: str
    evolve_metric_wins: int
    python_metric_wins: int
    ties: int

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "contestant": self.contestant_id,
            "version": self.version,
            "scenario": self.scenario_id,
            "seed": self.seed_id,
            "aggregate_winner": self.aggregate_winner,
            "evolve_metric_wins": self.evolve_metric_wins,
            "python_metric_wins": self.python_metric_wins,
            "ties": self.ties,
            "evolve_terminal": self.evolve_terminal.to_json(),
            "python_terminal": self.python_terminal.to_json(),
            "verdicts": [verdict.to_json() for verdict in self.verdicts],
        }


@dataclass(frozen=True, slots=True)
class HeadToHeadReport:
    """Deterministic, content-addressed head-to-head terminal outcome report."""

    schema_version: str
    generator_version: str
    match_id: str
    dataset_id: str
    tenant_id: str
    evidence_kind: str
    scenario_id: str
    seed_id: str
    contestants: tuple[ContestantVerdict, ...]
    artifact: Mapping[str, JsonValue]
    artifact_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        return {**dict(self.artifact), "artifact_sha256": self.artifact_sha256}


def _statistic(report: KpiReport, dimension: KpiDimension, side: str) -> dict[str, JsonValue]:
    for result in report.dimensions:
        if result.dimension == dimension:
            payload = result.evolve if side == "evolve" else result.python_agent
            if not isinstance(payload, dict):
                return {}
            statistic = payload.get("statistic")
            return statistic if isinstance(statistic, dict) else {}
    return {}


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _terminal_metrics(report: KpiReport, side: str) -> TerminalMetrics:
    growth = _statistic(report, KpiDimension.RESOURCE_GROWTH, side)
    population = _statistic(report, KpiDimension.POPULATION_FORCES, side)
    survival = _statistic(report, KpiDimension.SURVIVAL_TERMINAL, side)
    delivery = _statistic(report, KpiDimension.COLLECTION_DELIVERY, side)
    core_hp = _as_int(survival.get("core_hp"))
    core_state_raw = survival.get("core_state")
    return TerminalMetrics(
        final_resources=_as_int(growth.get("final")),
        resource_growth=_as_int(growth.get("growth")),
        population_final=_as_int(population.get("population_final")),
        unit_count_final=_as_int(population.get("unit_count_final")),
        enemy_count_final=_as_int(population.get("enemy_count_final")),
        cargo_final=_as_int(delivery.get("cargo_final")),
        core_state=core_state_raw if isinstance(core_state_raw, str) else None,
        core_hp=core_hp,
        survival_alive=core_hp is not None and core_hp > 0,
        last_tick=_as_int(survival.get("last_tick")),
    )


def _compare_terminal(
    evolve: TerminalMetrics, python: TerminalMetrics
) -> tuple[tuple[MetricVerdict, ...], str, int, int, int]:
    """Compare terminal metrics and return verdicts plus the aggregate winner.

    Survival dominates: a side whose core is dead loses regardless of resource
    totals. Among two surviving sides, the side winning more terminal resource,
    population, and cargo metrics wins; otherwise the match is a tie.
    """
    if python.survival_alive and not evolve.survival_alive:
        survival_winner = "python_agent"
    elif evolve.survival_alive and not python.survival_alive:
        survival_winner = "evolve"
    else:
        survival_winner = "tie"
    verdicts: list[MetricVerdict] = [
        MetricVerdict("survival", evolve.survival_alive, python.survival_alive, survival_winner)
    ]

    evolve_wins = 0
    python_wins = 0
    ties = 0

    def record(metric: str, evolve_value: int | None, python_value: int | None) -> None:
        nonlocal evolve_wins, python_wins, ties
        if evolve_value is None or python_value is None or evolve_value == python_value:
            winner = "tie"
        elif python_value > evolve_value:
            winner = "python_agent"
        else:
            winner = "evolve"
        if winner == "python_agent":
            python_wins += 1
        elif winner == "evolve":
            evolve_wins += 1
        else:
            ties += 1
        verdicts.append(MetricVerdict(metric, evolve_value, python_value, winner))

    record("final_resources", evolve.final_resources, python.final_resources)
    record("resource_growth", evolve.resource_growth, python.resource_growth)
    record("population_final", evolve.population_final, python.population_final)
    record("unit_count_final", evolve.unit_count_final, python.unit_count_final)
    record("cargo_final", evolve.cargo_final, python.cargo_final)

    if survival_winner != "tie":
        aggregate = survival_winner
    elif python_wins != evolve_wins:
        aggregate = "python_agent" if python_wins > evolve_wins else "evolve"
    else:
        aggregate = "tie"
    return tuple(verdicts), aggregate, evolve_wins, python_wins, ties


def run_head_to_head_from_manifest(manifest_path: str | Path) -> HeadToHeadReport:
    """Run one head-to-head match and return the terminal outcome report."""
    manifest = load_head_to_head_manifest(manifest_path)
    base = Path(manifest_path).resolve().parent
    scenario = manifest.scenario
    evolve = scenario.evolve
    evolve_manifest = base / evolve.manifest
    evolve_data_dir = base / evolve.data_dir
    decision_trace = None if evolve.decision_trace is None else base / evolve.decision_trace

    contestants: list[ContestantVerdict] = []
    for contestant in manifest.contestants:
        report = build_kpi_differential_run(
            evolve_manifest_path=evolve_manifest,
            evolve_data_dir=evolve_data_dir,
            py_records_path=base / contestant.records,
            dataset_id=manifest.dataset_id,
            tenant_id=manifest.tenant_id,
            evolve_decision_trace=decision_trace,
            py_observation_snapshots=base / contestant.observation_snapshots,
            evidence_kind=manifest.evidence_kind,
        )
        evolve_terminal = _terminal_metrics(report, "evolve")
        python_terminal = _terminal_metrics(report, "python_agent")
        verdicts, aggregate, evolve_wins, python_wins, ties = _compare_terminal(
            evolve_terminal, python_terminal
        )
        contestants.append(
            ContestantVerdict(
                contestant_id=contestant.contestant_id,
                version=contestant.version,
                scenario_id=scenario.scenario_id,
                seed_id=manifest.seed_id,
                evolve_terminal=evolve_terminal,
                python_terminal=python_terminal,
                verdicts=verdicts,
                aggregate_winner=aggregate,
                evolve_metric_wins=evolve_wins,
                python_metric_wins=python_wins,
                ties=ties,
            )
        )

    payload: dict[str, JsonValue] = {
        "schema_version": HEAD_TO_HEAD_REPORT_SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "match_id": manifest.match_id,
        "dataset_id": manifest.dataset_id,
        "tenant_id": manifest.tenant_id,
        "evidence_kind": manifest.evidence_kind,
        "scenario": scenario.scenario_id,
        "seed": manifest.seed_id,
        "contestants": [contestant.to_json() for contestant in contestants],
    }
    artifact = _json_object(payload)
    return HeadToHeadReport(
        schema_version=HEAD_TO_HEAD_REPORT_SCHEMA,
        generator_version=GENERATOR_VERSION,
        match_id=manifest.match_id,
        dataset_id=manifest.dataset_id,
        tenant_id=manifest.tenant_id,
        evidence_kind=manifest.evidence_kind,
        scenario_id=scenario.scenario_id,
        seed_id=manifest.seed_id,
        contestants=tuple(contestants),
        artifact=artifact,
        artifact_sha256=content_sha256(artifact),
    )


__all__ = [
    "GENERATOR_VERSION",
    "HEAD_TO_HEAD_REPORT_SCHEMA",
    "HEAD_TO_HEAD_SCHEMA",
    "ContestantVerdict",
    "HeadToHeadContestant",
    "HeadToHeadError",
    "HeadToHeadManifest",
    "HeadToHeadManifestError",
    "HeadToHeadReport",
    "HeadToHeadScenario",
    "MetricVerdict",
    "TerminalMetrics",
    "load_head_to_head_manifest",
    "run_head_to_head_from_manifest",
]
