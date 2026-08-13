"""Free-for-all (FFA) terminal ranking for N>=2 Python-agent contestants.

The head-to-head comparator (:mod:`arena_hero_bench.head_to_head`) answers
"evolve baseline vs one Python agent: who ends better?". This module answers a
different question: given N>=2 Python-agent contestants on the same scenario and
seed, rank them in a single free-for-all table.

Ranking reuses the exact terminal extraction from
:mod:`arena_hero_bench.head_to_head` (``TerminalMetrics`` / ``_terminal_metrics``):
for every contestant we build the same fail-closed KPI differential against the
shared evolve corpus and read the ``python_agent`` terminal statistics. Each
contestant therefore keeps the same evidence contract (records plus sanitized
observation snapshots) as a head-to-head entry, but the verdict is a placement
instead of a pairwise win/loss.

Ranking key (descending): survival first (surviving cores outrank destroyed
cores), then terminal core HP as a survivability tie-breaker, then the aggregate
terminal score (sum of final resources, resource growth, final population, final
unit count, and final cargo; higher is better). Ties on the full key share a
rank (competition ranking).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from arena_hero_bench.competitive_eval import EvolveSide
from arena_hero_bench.head_to_head import TerminalMetrics, _terminal_metrics
from arena_hero_bench.kpi_differential import build_kpi_differential_run
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

MELEE_SCHEMA: Final = "arena.bench.melee.v1"
MELEE_REPORT_SCHEMA: Final = "arena.bench.melee-report.v1"
GENERATOR_VERSION: Final = "0.1.0"
EVIDENCE_KINDS: Final = frozenset({"sanitized_fixture", "production"})
_ALLOWED_PROTOCOLS: Final = frozenset({"agent-run-v1"})
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

# Terminal metrics that contribute to the aggregate placement score (higher is better).
_AGGREGATE_FIELDS: Final = (
    "final_resources",
    "resource_growth",
    "population_final",
    "unit_count_final",
    "cargo_final",
)


class MeleeError(ValueError):
    """Base error for the free-for-all ranking runner."""


class MeleeManifestError(MeleeError):
    """A melee manifest is invalid or unsupported."""


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MeleeManifestError(f"{field_name} must be a non-empty string")
    return value


def _strict_object(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MeleeManifestError(f"{field_name} must be an object")
    return value


def _strict_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise MeleeManifestError(f"{field_name} must be an array")
    return value


def _identifier(value: object, field_name: str) -> str:
    text = _strict_str(value, field_name)
    if not _IDENTIFIER.fullmatch(text):
        raise MeleeManifestError(f"{field_name} must be a lowercase portable identifier")
    return text


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    narrowed = to_json_value(value)
    if not isinstance(narrowed, dict):
        raise MeleeError("expected a JSON object")
    return narrowed


@dataclass(frozen=True, slots=True)
class MeleeScenario:
    """One scenario of a melee match: the shared evolve baseline corpus."""

    scenario_id: str
    evolve: EvolveSide


@dataclass(frozen=True, slots=True)
class MeleeContestant:
    """One Python-agent contestant with per-contestant terminal snapshots."""

    contestant_id: str
    version: str
    protocol: str
    records: Path
    observation_snapshots: Path


@dataclass(frozen=True, slots=True)
class MeleeManifest:
    """A fully validated free-for-all match manifest."""

    schema_version: str
    match_id: str
    dataset_id: str
    tenant_id: str
    evidence_kind: str
    scenario: MeleeScenario
    seed_id: str
    contestants: tuple[MeleeContestant, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema_version", _strict_str(self.schema_version, "schema_version")
        )
        if self.schema_version != MELEE_SCHEMA:
            raise MeleeManifestError(f"unsupported schemaVersion {self.schema_version!r}")
        object.__setattr__(self, "match_id", _identifier(self.match_id, "match_id"))
        object.__setattr__(self, "dataset_id", _identifier(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "tenant_id", _identifier(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "evidence_kind", _strict_str(self.evidence_kind, "evidence_kind"))
        if self.evidence_kind not in EVIDENCE_KINDS:
            raise MeleeManifestError(f"unsupported evidence_kind {self.evidence_kind!r}")
        object.__setattr__(self, "seed_id", _identifier(self.seed_id, "seed_id"))
        if len(self.contestants) < 2:
            raise MeleeManifestError("manifest must declare at least two contestants")
        keys = [(contestant.contestant_id, contestant.version) for contestant in self.contestants]
        if len(keys) != len(set(keys)):
            raise MeleeManifestError("contestant (id, version) pairs must be unique")
        for contestant in self.contestants:
            if contestant.protocol not in _ALLOWED_PROTOCOLS:
                raise MeleeManifestError(f"unsupported contestant protocol {contestant.protocol!r}")


def load_melee_manifest(manifest_path: str | Path) -> MeleeManifest:
    """Parse and validate one free-for-all match manifest."""
    path = Path(manifest_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MeleeManifestError(f"cannot read melee manifest {path.name}: {exc}") from exc
    manifest = _strict_object(raw, "melee manifest")
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
    contestants: list[MeleeContestant] = []
    for index, item in enumerate(contestants_raw):
        contestant = _strict_object(item, f"contestants[{index}]")
        contestants.append(
            MeleeContestant(
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
    return MeleeManifest(
        schema_version=_strict_str(manifest.get("schemaVersion"), "schemaVersion"),
        match_id=_strict_str(manifest.get("match_id"), "match_id"),
        dataset_id=_strict_str(manifest.get("dataset_id"), "dataset_id"),
        tenant_id=_strict_str(manifest.get("tenant_id"), "tenant_id"),
        evidence_kind=_strict_str(manifest.get("evidence_kind"), "evidence_kind"),
        scenario=MeleeScenario(scenario_id=scenario_id, evolve=evolve),
        seed_id=_strict_str(manifest.get("seed"), "seed"),
        contestants=tuple(contestants),
    )


@dataclass(frozen=True, slots=True)
class MeleePlacement:
    """One contestant's terminal placement in a free-for-all ranking."""

    rank: int
    contestant_id: str
    version: str
    survival_alive: bool
    aggregate_score: int
    terminal: TerminalMetrics

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "rank": self.rank,
            "contestant": self.contestant_id,
            "version": self.version,
            "survival_alive": self.survival_alive,
            "aggregate_score": self.aggregate_score,
            "terminal": self.terminal.to_json(),
        }


@dataclass(frozen=True, slots=True)
class MeleeReport:
    """Deterministic, content-addressed free-for-all terminal ranking report."""

    schema_version: str
    generator_version: str
    match_id: str
    dataset_id: str
    tenant_id: str
    evidence_kind: str
    scenario_id: str
    seed_id: str
    placements: tuple[MeleePlacement, ...]
    artifact: Mapping[str, JsonValue]
    artifact_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        return {**dict(self.artifact), "artifact_sha256": self.artifact_sha256}


def _aggregate_score(terminal: TerminalMetrics) -> int:
    total = 0
    for field in _AGGREGATE_FIELDS:
        value = getattr(terminal, field)
        if value is not None:
            total += value
    return total


def _rank_key(terminal: TerminalMetrics) -> tuple[int, int, int]:
    alive = 1 if terminal.survival_alive else 0
    core_hp = terminal.core_hp if terminal.core_hp is not None else -1
    return (alive, core_hp, _aggregate_score(terminal))


def run_melee_from_manifest(manifest_path: str | Path) -> MeleeReport:
    """Run one free-for-all ranking and return the terminal placement report."""
    manifest = load_melee_manifest(manifest_path)
    base = Path(manifest_path).resolve().parent
    scenario = manifest.scenario
    evolve = scenario.evolve
    evolve_manifest = base / evolve.manifest
    evolve_data_dir = base / evolve.data_dir
    decision_trace = None if evolve.decision_trace is None else base / evolve.decision_trace

    entries: list[tuple[tuple[int, int, int], MeleeContestant, TerminalMetrics]] = []
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
        terminal = _terminal_metrics(report, "python_agent")
        entries.append((_rank_key(terminal), contestant, terminal))

    entries.sort(key=lambda entry: entry[0], reverse=True)
    placements: list[MeleePlacement] = []
    for index, (key, contestant, terminal) in enumerate(entries):
        rank = placements[-1].rank if index and key == entries[index - 1][0] else index + 1
        placements.append(
            MeleePlacement(
                rank=rank,
                contestant_id=contestant.contestant_id,
                version=contestant.version,
                survival_alive=terminal.survival_alive,
                aggregate_score=_aggregate_score(terminal),
                terminal=terminal,
            )
        )

    payload: dict[str, JsonValue] = {
        "schema_version": MELEE_REPORT_SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "match_id": manifest.match_id,
        "dataset_id": manifest.dataset_id,
        "tenant_id": manifest.tenant_id,
        "evidence_kind": manifest.evidence_kind,
        "scenario": scenario.scenario_id,
        "seed": manifest.seed_id,
        "placements": [placement.to_json() for placement in placements],
    }
    artifact = _json_object(payload)
    return MeleeReport(
        schema_version=MELEE_REPORT_SCHEMA,
        generator_version=GENERATOR_VERSION,
        match_id=manifest.match_id,
        dataset_id=manifest.dataset_id,
        tenant_id=manifest.tenant_id,
        evidence_kind=manifest.evidence_kind,
        scenario_id=scenario.scenario_id,
        seed_id=manifest.seed_id,
        placements=tuple(placements),
        artifact=artifact,
        artifact_sha256=content_sha256(artifact),
    )


__all__ = [
    "GENERATOR_VERSION",
    "MELEE_REPORT_SCHEMA",
    "MELEE_SCHEMA",
    "MeleeContestant",
    "MeleeError",
    "MeleeManifest",
    "MeleeManifestError",
    "MeleePlacement",
    "MeleeReport",
    "MeleeScenario",
    "load_melee_manifest",
    "run_melee_from_manifest",
]
