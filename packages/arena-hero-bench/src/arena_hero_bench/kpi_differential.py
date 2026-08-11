"""Evolve/Python Agent multi-dimensional KPI differential (P6-3).

The KPI differential compares one evolve-baseline replay side against one
Python-agent replay side across six independently classified behavior
dimensions instead of only comparing a single winner:

- ``tick_alignment``: the per-run tick coverage on each side.
- ``resource_growth``: per-tick resources series plus its growth statistic.
- ``collection_delivery``: per-tick in-transit cargo (collection) and the
  deposited resource balance (delivery) plus their statistics.
- ``population_forces``: per-tick population, tier, unit and enemy counts.
- ``survival_terminal``: per-tick status/core state plus terminal loop context.
- ``decision_distribution``: per-tick decision outcome series plus the
  outcome/submission distribution statistics.

Every dimension is computed independently and classified into exactly one of
the same four statuses used by the replay differential (P6-2): ``MATCH``,
``MISMATCH``, ``EXPECTED_UNKNOWN``, or ``INCONCLUSIVE``; nothing is ever left
unclassified. The report is deterministic and content-addressed: identical
inputs always produce the same artifact digest, and reordering inputs does not
change it.

Classification semantics
------------------------
- ``MATCH``: both sides captured the dimension and their canonical series are
  equal.
- ``MISMATCH``: both sides captured the dimension and their canonical series
  differ.
- ``EXPECTED_UNKNOWN``: the side's *protocol contract* does not capture the
  dimension (declared in the run manifest, never inferred). Missing evidence
  on both sides is also expected-unknown. This is never reported as a match.
- ``INCONCLUSIVE``: the protocol captures the dimension but this particular
  side lacks the evidence, so agreement cannot be confirmed. This is never
  reported as a match.

Evidence reuse and wire-contract gaps
-------------------------------------
The evolve side reuses the committed ``differential-record-v1`` corpus and its
canonicalizer from :mod:`arena_hero_bench.differential`; the Python side reuses
the versioned offline agent run JSONL through ``import_agent_run`` (via
``load_py_agent_corpus``), so all fail-closed parsing, tenant checks, and
torn-tail handling are inherited from that importer.

The ``agent-run-v1`` contract does not carry world state and the
``differential-record-v1`` fixture contract does not carry decisions, so those
dimensions default to ``EXPECTED_UNKNOWN`` (declared by
``DEFAULT_KPI_EXPECTED_UNKNOWN``) and are never reported as matches. A run
manifest may optionally bind *sanitized companion fixtures* -- an evolve
decision trace and Python observation snapshots -- whose provenance is
declared through the report's ``evidence_kind`` field (the committed corpus
declares ``sanitized_fixture``). Companion fixtures must cover exactly the
side's tick set and are validated fail-closed: torn tails, corrupt lines,
unknown record types, duplicate ticks, tenant mismatches, and tick-set
mismatches are all rejected.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from arena_hero_bench.differential import (
    DifferentialError,
    DifferentialStatus,
    PyAgentCanonicalRecord,
    TsLegacyCanonicalRecord,
    load_py_agent_corpus,
    load_ts_legacy_corpus,
)
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

KPI_DIFFERENTIAL_SCHEMA: Final = "arena.bench.kpi-differential.v1"
KPI_SCHEMA_VERSION: Final = "v1"
EVOLVE_PROTOCOL: Final = "differential-record-v1"
PY_AGENT_PROTOCOL: Final = "agent-run-v1"
OBSERVATION_SCHEMA_VERSION: Final = 1
DECISION_TRACE_SCHEMA_VERSION: Final = 1
GENERATOR_VERSION: Final = "0.1.0"

SANITIZED_EVIDENCE_KIND: Final = "sanitized_fixture"
PRODUCTION_EVIDENCE_KIND: Final = "production"
_EVIDENCE_KINDS: Final = frozenset({SANITIZED_EVIDENCE_KIND, PRODUCTION_EVIDENCE_KIND})

_TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DATASET_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DEADLINE_OUTCOMES: Final = frozenset(
    {"candidate", "soft_deadline", "selection_timeout", "not_applicable", "error"}
)
_SUBMIT_RESULTS: Final = frozenset({"accepted", "rejected", "not_submitted"})


class KpiDimension(StrEnum):
    """One independently classified behavior dimension of the KPI differential."""

    TICK_ALIGNMENT = "tick_alignment"
    RESOURCE_GROWTH = "resource_growth"
    COLLECTION_DELIVERY = "collection_delivery"
    POPULATION_FORCES = "population_forces"
    SURVIVAL_TERMINAL = "survival_terminal"
    DECISION_DISTRIBUTION = "decision_distribution"


def _strict_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DifferentialError(f"{field_name} must be an integer")
    return value


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise DifferentialError(f"{field_name} must be a non-empty string")
    return value


def _strict_optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _strict_str(value, field_name)


def _strict_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise DifferentialError(f"{field_name} must be an array")
    return value


def _strict_object(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DifferentialError(f"{field_name} must be an object")
    return value


def _strict_enum(value: object, field_name: str, allowed: frozenset[str]) -> str:
    text = _strict_str(value, field_name)
    if text not in allowed:
        raise DifferentialError(f"{field_name} has an unsupported value {text!r}")
    return text


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    """Narrow an ordinary mapping into a JSON object value."""
    narrowed = to_json_value(value)
    if not isinstance(narrowed, dict):
        raise DifferentialError("expected a JSON object")
    return narrowed


def _series_tick_value(pairs: Sequence[tuple[int, Mapping[str, JsonValue]]]) -> JsonValue:
    """Canonical per-tick series form: ``[[tick, {...}], ...]`` in tick order."""
    return [[tick, to_json_value(values)] for tick, values in pairs]


@dataclass(frozen=True, slots=True)
class EvolveDecisionRecord:
    """One sanitized evolve-side decision from the companion trace fixture."""

    tick: int
    deadline_outcome: str
    submit_result: str
    submit_error: str | None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "tick": self.tick,
            "deadline_outcome": self.deadline_outcome,
            "submit_result": self.submit_result,
            "submit_error": self.submit_error,
        }


@dataclass(frozen=True, slots=True)
class PyObservationRecord:
    """One sanitized Python-agent observation snapshot."""

    tick: int
    status: str
    resources: int
    population: int
    population_tier: int
    cargo_total: int
    unit_count: int
    enemy_count: int
    core_state: str
    core_hp: int
    core_shield: int

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "tick": self.tick,
            "status": self.status,
            "resources": self.resources,
            "population": self.population,
            "population_tier": self.population_tier,
            "cargo_total": self.cargo_total,
            "unit_count": self.unit_count,
            "enemy_count": self.enemy_count,
            "core_state": self.core_state,
            "core_hp": self.core_hp,
            "core_shield": self.core_shield,
        }


@dataclass(frozen=True, slots=True)
class KpiSideEvidence:
    """Canonical evidence for one side of one KPI dimension."""

    series: JsonValue
    statistic: Mapping[str, JsonValue]

    def to_json(self) -> dict[str, JsonValue]:
        return {"series": self.series, "statistic": dict(self.statistic)}


@dataclass(frozen=True, slots=True)
class KpiDimensionResult:
    """One classified behavior dimension of the KPI differential."""

    dimension: KpiDimension
    status: DifferentialStatus
    reason: str
    evolve: JsonValue | None
    python_agent: JsonValue | None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "dimension": self.dimension.value,
            "status": self.status.value,
            "reason": self.reason,
            "evolve": self.evolve,
            "python_agent": self.python_agent,
        }


@dataclass(frozen=True, slots=True)
class KpiReport:
    """Deterministic, content-addressed KPI differential report for one run."""

    schema_version: str
    dataset_id: str
    tenant_id: str
    evolve_protocol: str
    py_protocol: str
    evidence_kind: str
    dimensions: tuple[KpiDimensionResult, ...]
    counts: Mapping[DifferentialStatus, int]
    unclassified_count: int
    artifact: Mapping[str, JsonValue]
    artifact_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "tenant_id": self.tenant_id,
            "protocols": {"evolve": self.evolve_protocol, "python_agent": self.py_protocol},
            "evidence_kind": self.evidence_kind,
            "dimensions": [dimension.to_json() for dimension in self.dimensions],
            "counts": {status.value: self.counts.get(status, 0) for status in DifferentialStatus},
            "unclassified_count": self.unclassified_count,
            "artifact_sha256": self.artifact_sha256,
        }


DEFAULT_KPI_EXPECTED_UNKNOWN: Final = {
    KpiDimension.RESOURCE_GROWTH: "python_agent",
    KpiDimension.COLLECTION_DELIVERY: "python_agent",
    KpiDimension.POPULATION_FORCES: "python_agent",
    KpiDimension.SURVIVAL_TERMINAL: "python_agent",
    KpiDimension.DECISION_DISTRIBUTION: "evolve",
}


def _load_companion_lines(path: Path, label: str) -> list[Mapping[str, object]]:
    """Read one companion JSONL side fail-closed (torn tail, corrupt lines)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DifferentialError(f"{label} could not be read: {exc}") from exc
    if not text:
        raise DifferentialError(f"{label} file is empty")
    if not text.endswith("\n"):
        raise DifferentialError(f"{label} file has a torn tail (missing final newline)")
    records: list[Mapping[str, object]] = []
    for line_number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DifferentialError(f"corrupt {label} record at line {line_number}: {exc}") from exc
        if not isinstance(data, dict):
            raise DifferentialError(f"{label} record at line {line_number} must be a JSON object")
        records.append(data)
    if not records:
        raise DifferentialError(f"{label} file contains no records")
    return records


def _require_companion_common(
    data: Mapping[str, object], *, label: str, schema_version: int, tenant_id: str
) -> None:
    if _strict_int(data.get("schemaVersion"), f"{label}.schemaVersion") != schema_version:
        raise DifferentialError(f"{label} has an unsupported schemaVersion")
    if data.get("tenantId") != tenant_id:
        raise DifferentialError(f"{label} tenantId does not match expected {tenant_id!r}")


def load_evolve_decision_trace(
    path: str | Path,
    *,
    tenant_id: str,
) -> tuple[EvolveDecisionRecord, ...]:
    """Load the sanitized evolve decision trace fail-closed."""
    raw_records = _load_companion_lines(Path(path), "evolve decision trace")
    parsed: list[EvolveDecisionRecord] = []
    for index, data in enumerate(raw_records):
        label = f"evolve decision trace[{index}]"
        _require_companion_common(
            data, label=label, schema_version=DECISION_TRACE_SCHEMA_VERSION, tenant_id=tenant_id
        )
        if data.get("recordType") != "decision":
            raise DifferentialError(f"{label} has an unsupported recordType")
        tick = _strict_int(data.get("tick"), f"{label}.tick")
        if tick < 0:
            raise DifferentialError(f"{label}.tick must be non-negative")
        parsed.append(
            EvolveDecisionRecord(
                tick=tick,
                deadline_outcome=_strict_enum(
                    data.get("deadlineOutcome"), f"{label}.deadlineOutcome", _DEADLINE_OUTCOMES
                ),
                submit_result=_strict_enum(
                    data.get("submitResult"), f"{label}.submitResult", _SUBMIT_RESULTS
                ),
                submit_error=_strict_optional_str(data.get("submitError"), f"{label}.submitError"),
            )
        )
    ordered = tuple(
        parsed[index] for index in sorted(range(len(parsed)), key=lambda i: parsed[i].tick)
    )
    if len({record.tick for record in ordered}) != len(ordered):
        raise DifferentialError("evolve decision trace contains a duplicate tick")
    return ordered


def load_py_observation_snapshots(
    path: str | Path,
    *,
    tenant_id: str,
) -> tuple[PyObservationRecord, ...]:
    """Load the sanitized Python observation snapshots fail-closed."""
    raw_records = _load_companion_lines(Path(path), "python observation snapshot")
    parsed: list[PyObservationRecord] = []
    for index, data in enumerate(raw_records):
        label = f"python observation snapshot[{index}]"
        _require_companion_common(
            data, label=label, schema_version=OBSERVATION_SCHEMA_VERSION, tenant_id=tenant_id
        )
        if data.get("recordType") != "snapshot":
            raise DifferentialError(f"{label} has an unsupported recordType")
        tick = _strict_int(data.get("tick"), f"{label}.tick")
        if tick < 0:
            raise DifferentialError(f"{label}.tick must be non-negative")
        parsed.append(
            PyObservationRecord(
                tick=tick,
                status=_strict_str(data.get("status"), f"{label}.status"),
                resources=_strict_int(data.get("resources"), f"{label}.resources"),
                population=_strict_int(data.get("population"), f"{label}.population"),
                population_tier=_strict_int(data.get("populationTier"), f"{label}.populationTier"),
                cargo_total=_strict_int(data.get("cargoTotal"), f"{label}.cargoTotal"),
                unit_count=_strict_int(data.get("unitCount"), f"{label}.unitCount"),
                enemy_count=_strict_int(data.get("enemyCount"), f"{label}.enemyCount"),
                core_state=_strict_str(data.get("coreState"), f"{label}.coreState"),
                core_hp=_strict_int(data.get("coreHp"), f"{label}.coreHp"),
                core_shield=_strict_int(data.get("coreShield"), f"{label}.coreShield"),
            )
        )
    ordered = tuple(
        parsed[index] for index in sorted(range(len(parsed)), key=lambda i: parsed[i].tick)
    )
    if len({record.tick for record in ordered}) != len(ordered):
        raise DifferentialError("python observation snapshot contains a duplicate tick")
    return ordered


# ---------------------------------------------------------------------------
# Evolve-side dimension evidence
# ---------------------------------------------------------------------------


def _evolve_tick_alignment(
    records: Sequence[TsLegacyCanonicalRecord],
) -> KpiSideEvidence:
    ticks = sorted(record.tick for record in records)
    return KpiSideEvidence(
        series=to_json_value(ticks),
        statistic=_json_object(
            {"tick_count": len(ticks), "first_tick": ticks[0], "last_tick": ticks[-1]}
        ),
    )


def _evolve_resource_growth(
    records: Sequence[TsLegacyCanonicalRecord],
) -> KpiSideEvidence:
    ordered = sorted(records, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    resources: list[int] = []
    for record in ordered:
        value = _strict_int(record.world_state.get("resources"), "resources")
        resources.append(value)
        pairs.append((record.tick, {"resources": value}))
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object(
            {
                "initial": resources[0],
                "final": resources[-1],
                "growth": resources[-1] - resources[0],
                "samples": len(resources),
            }
        ),
    )


def _evolve_collection_delivery(
    records: Sequence[TsLegacyCanonicalRecord],
) -> KpiSideEvidence:
    ordered = sorted(records, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    cargo_values: list[int] = []
    resources: list[int] = []
    for record in ordered:
        units = _strict_list(record.world_state.get("units"), "units")
        cargo_total = 0
        for unit in units:
            unit_obj = _strict_object(unit, "unit")
            cargo_total += _strict_int(unit_obj.get("cargo"), "unit.cargo")
        cargo_values.append(cargo_total)
        resource_value = _strict_int(record.world_state.get("resources"), "resources")
        resources.append(resource_value)
        pairs.append((record.tick, {"cargo_total": cargo_total, "resources": resource_value}))
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object(
            {
                "cargo_final": cargo_values[-1],
                "resources_final": resources[-1],
                "samples": len(cargo_values),
            }
        ),
    )


def _evolve_population_forces(
    records: Sequence[TsLegacyCanonicalRecord],
) -> KpiSideEvidence:
    ordered = sorted(records, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    populations: list[int] = []
    unit_counts: list[int] = []
    enemy_counts: list[int] = []
    for record in ordered:
        population = _strict_int(record.world_state.get("population"), "population")
        population_tier = _strict_int(record.world_state.get("population_tier"), "population_tier")
        unit_count = len(_strict_list(record.world_state.get("units"), "units"))
        enemy_count = len(_strict_list(record.world_state.get("enemies"), "enemies"))
        populations.append(population)
        unit_counts.append(unit_count)
        enemy_counts.append(enemy_count)
        pairs.append(
            (
                record.tick,
                {
                    "population": population,
                    "population_tier": population_tier,
                    "unit_count": unit_count,
                    "enemy_count": enemy_count,
                },
            )
        )
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object(
            {
                "population_final": populations[-1],
                "unit_count_final": unit_counts[-1],
                "enemy_count_final": enemy_counts[-1],
                "samples": len(populations),
            }
        ),
    )


def _evolve_survival_terminal(
    records: Sequence[TsLegacyCanonicalRecord],
) -> KpiSideEvidence:
    ordered = sorted(records, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    terminal: dict[str, JsonValue] | None = None
    for record in ordered:
        world = record.world_state
        status = _strict_str(world.get("status"), "status")
        core = _strict_object(world.get("core"), "core")
        core_state = _strict_str(core.get("state"), "core.state")
        core_hp = _strict_int(core.get("hp"), "core.hp")
        core_shield = _strict_int(core.get("shield"), "core.shield")
        values: Mapping[str, JsonValue] = {
            "status": status,
            "core_state": core_state,
            "core_hp": core_hp,
            "core_shield": core_shield,
        }
        pairs.append((record.tick, values))
        terminal = {
            "status": status,
            "core_state": core_state,
            "core_hp": core_hp,
            "core_shield": core_shield,
            "last_tick": record.tick,
        }
    assert terminal is not None
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object({**terminal, "loop": None}),
    )


def _evolve_decision_distribution(
    records: Sequence[TsLegacyCanonicalRecord],
    decisions: Sequence[EvolveDecisionRecord],
) -> KpiSideEvidence | None:
    if not decisions:
        return None
    ordered = sorted(decisions, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    deadline_counts: dict[str, int] = {}
    submit_counts: dict[str, int] = {}
    for record in ordered:
        deadline = record.deadline_outcome
        submit = record.submit_result
        deadline_counts[deadline] = deadline_counts.get(deadline, 0) + 1
        submit_counts[submit] = submit_counts.get(submit, 0) + 1
        pairs.append(
            (
                record.tick,
                {
                    "deadline_outcome": deadline,
                    "submit_result": submit,
                    "submit_error": record.submit_error,
                },
            )
        )
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object(
            {
                "deadline_outcome_counts": deadline_counts,
                "submit_result_counts": submit_counts,
                "samples": len(ordered),
            }
        ),
    )


def _extract_evolve(
    dimension: KpiDimension,
    records: Sequence[TsLegacyCanonicalRecord],
    decisions: Sequence[EvolveDecisionRecord],
) -> KpiSideEvidence | None:
    if dimension == KpiDimension.TICK_ALIGNMENT:
        return _evolve_tick_alignment(records)
    if dimension == KpiDimension.RESOURCE_GROWTH:
        return _evolve_resource_growth(records)
    if dimension == KpiDimension.COLLECTION_DELIVERY:
        return _evolve_collection_delivery(records)
    if dimension == KpiDimension.POPULATION_FORCES:
        return _evolve_population_forces(records)
    if dimension == KpiDimension.SURVIVAL_TERMINAL:
        return _evolve_survival_terminal(records)
    if dimension == KpiDimension.DECISION_DISTRIBUTION:
        return _evolve_decision_distribution(records, decisions)
    raise AssertionError(f"unhandled KpiDimension {dimension!r}")


# ---------------------------------------------------------------------------
# Python-agent-side dimension evidence
# ---------------------------------------------------------------------------


def _py_tick_alignment(records: Sequence[PyAgentCanonicalRecord]) -> KpiSideEvidence:
    ticks = sorted(record.tick for record in records)
    return KpiSideEvidence(
        series=to_json_value(ticks),
        statistic=_json_object(
            {"tick_count": len(ticks), "first_tick": ticks[0], "last_tick": ticks[-1]}
        ),
    )


def _py_resource_growth(
    snapshots: Sequence[PyObservationRecord],
) -> KpiSideEvidence | None:
    if not snapshots:
        return None
    ordered = sorted(snapshots, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    resources: list[int] = []
    for record in ordered:
        resources.append(record.resources)
        pairs.append((record.tick, {"resources": record.resources}))
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object(
            {
                "initial": resources[0],
                "final": resources[-1],
                "growth": resources[-1] - resources[0],
                "samples": len(resources),
            }
        ),
    )


def _py_collection_delivery(
    snapshots: Sequence[PyObservationRecord],
) -> KpiSideEvidence | None:
    if not snapshots:
        return None
    ordered = sorted(snapshots, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    cargo_values: list[int] = []
    resources: list[int] = []
    for record in ordered:
        cargo_values.append(record.cargo_total)
        resources.append(record.resources)
        pairs.append(
            (
                record.tick,
                {"cargo_total": record.cargo_total, "resources": record.resources},
            )
        )
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object(
            {
                "cargo_final": cargo_values[-1],
                "resources_final": resources[-1],
                "samples": len(cargo_values),
            }
        ),
    )


def _py_population_forces(
    snapshots: Sequence[PyObservationRecord],
) -> KpiSideEvidence | None:
    if not snapshots:
        return None
    ordered = sorted(snapshots, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    populations: list[int] = []
    unit_counts: list[int] = []
    enemy_counts: list[int] = []
    for record in ordered:
        populations.append(record.population)
        unit_counts.append(record.unit_count)
        enemy_counts.append(record.enemy_count)
        pairs.append(
            (
                record.tick,
                {
                    "population": record.population,
                    "population_tier": record.population_tier,
                    "unit_count": record.unit_count,
                    "enemy_count": record.enemy_count,
                },
            )
        )
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object(
            {
                "population_final": populations[-1],
                "unit_count_final": unit_counts[-1],
                "enemy_count_final": enemy_counts[-1],
                "samples": len(populations),
            }
        ),
    )


def _py_survival_terminal(
    snapshots: Sequence[PyObservationRecord],
    py_loop_metrics: Mapping[str, JsonValue] | None,
) -> KpiSideEvidence | None:
    if not snapshots:
        return None
    ordered = sorted(snapshots, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    terminal: dict[str, JsonValue] | None = None
    for record in ordered:
        values: Mapping[str, JsonValue] = {
            "status": record.status,
            "core_state": record.core_state,
            "core_hp": record.core_hp,
            "core_shield": record.core_shield,
        }
        pairs.append((record.tick, values))
        terminal = {
            "status": record.status,
            "core_state": record.core_state,
            "core_hp": record.core_hp,
            "core_shield": record.core_shield,
            "last_tick": record.tick,
        }
    assert terminal is not None
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object(
            {
                **terminal,
                "loop": None if py_loop_metrics is None else dict(py_loop_metrics),
            }
        ),
    )


def _py_decision_distribution(
    records: Sequence[PyAgentCanonicalRecord],
) -> KpiSideEvidence:
    ordered = sorted(records, key=lambda record: record.tick)
    pairs: list[tuple[int, Mapping[str, JsonValue]]] = []
    deadline_counts: dict[str, int] = {}
    submit_counts: dict[str, int] = {}
    for record in ordered:
        deadline = _strict_str(record.decision.get("deadline_outcome"), "deadline_outcome")
        submit = _strict_str(record.decision.get("submit_result"), "submit_result")
        deadline_counts[deadline] = deadline_counts.get(deadline, 0) + 1
        submit_counts[submit] = submit_counts.get(submit, 0) + 1
        pairs.append(
            (
                record.tick,
                {
                    "deadline_outcome": deadline,
                    "submit_result": submit,
                    "submit_error": record.decision.get("submit_error"),
                },
            )
        )
    return KpiSideEvidence(
        series=_series_tick_value(pairs),
        statistic=_json_object(
            {
                "deadline_outcome_counts": deadline_counts,
                "submit_result_counts": submit_counts,
                "samples": len(ordered),
            }
        ),
    )


def _extract_py(
    dimension: KpiDimension,
    records: Sequence[PyAgentCanonicalRecord],
    snapshots: Sequence[PyObservationRecord],
    py_loop_metrics: Mapping[str, JsonValue] | None,
) -> KpiSideEvidence | None:
    if dimension == KpiDimension.TICK_ALIGNMENT:
        return _py_tick_alignment(records)
    if dimension == KpiDimension.RESOURCE_GROWTH:
        return _py_resource_growth(snapshots)
    if dimension == KpiDimension.COLLECTION_DELIVERY:
        return _py_collection_delivery(snapshots)
    if dimension == KpiDimension.POPULATION_FORCES:
        return _py_population_forces(snapshots)
    if dimension == KpiDimension.SURVIVAL_TERMINAL:
        return _py_survival_terminal(snapshots, py_loop_metrics)
    if dimension == KpiDimension.DECISION_DISTRIBUTION:
        return _py_decision_distribution(records)
    raise AssertionError(f"unhandled KpiDimension {dimension!r}")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _series_digest(evidence: KpiSideEvidence) -> str:
    return content_sha256(evidence.series)


def _classify_dimension(
    dimension: KpiDimension,
    evolve_evidence: KpiSideEvidence | None,
    py_evidence: KpiSideEvidence | None,
    expected_unknown: Mapping[KpiDimension, str],
) -> tuple[DifferentialStatus, str]:
    label = dimension.value.replace("_", " ")
    if evolve_evidence is None and py_evidence is None:
        return DifferentialStatus.EXPECTED_UNKNOWN, f"neither side captures {label}"
    if evolve_evidence is None:
        if expected_unknown.get(dimension) == "evolve":
            return (
                DifferentialStatus.EXPECTED_UNKNOWN,
                f"evolve contract does not capture {label}",
            )
        return DifferentialStatus.INCONCLUSIVE, f"evolve side lacks evidence for {label}"
    if py_evidence is None:
        if expected_unknown.get(dimension) == "python_agent":
            return (
                DifferentialStatus.EXPECTED_UNKNOWN,
                f"python-agent contract does not capture {label}",
            )
        return (
            DifferentialStatus.INCONCLUSIVE,
            f"python-agent side lacks evidence for {label}",
        )
    if _series_digest(evolve_evidence) == _series_digest(py_evidence):
        return DifferentialStatus.MATCH, f"canonical {label} series agree"
    return DifferentialStatus.MISMATCH, f"canonical {label} series differ"


def classify_kpi_differential(
    *,
    evolve_records: Sequence[TsLegacyCanonicalRecord],
    py_records: Sequence[PyAgentCanonicalRecord],
    dataset_id: str,
    tenant_id: str,
    evolve_decision_trace: Sequence[EvolveDecisionRecord] = (),
    py_observation_snapshots: Sequence[PyObservationRecord] = (),
    py_loop_metrics: Mapping[str, JsonValue] | None = None,
    evolve_protocol: str = EVOLVE_PROTOCOL,
    py_protocol: str = PY_AGENT_PROTOCOL,
    evidence_kind: str = SANITIZED_EVIDENCE_KIND,
    expected_unknown: Mapping[KpiDimension, str] = DEFAULT_KPI_EXPECTED_UNKNOWN,
) -> KpiReport:
    """Classify every KPI dimension between one evolve side and one Python side.

    Every dimension receives exactly one independent classification; the report
    is emitted in dimension order and the artifact digest is content-addressed.
    Input order does not matter: both sides are aligned by tick internally.
    """
    if not _DATASET_ID.fullmatch(dataset_id):
        raise DifferentialError("dataset_id is not a portable identifier")
    if not _TENANT_ID.fullmatch(tenant_id):
        raise DifferentialError("tenant_id is not a portable identifier")
    if evidence_kind not in _EVIDENCE_KINDS:
        raise DifferentialError(f"unsupported evidence_kind {evidence_kind!r}")
    for dimension, side in expected_unknown.items():
        if not isinstance(dimension, KpiDimension):
            raise DifferentialError(f"unknown dimension in expected_unknown: {dimension!r}")
        if side not in ("evolve", "python_agent"):
            raise DifferentialError(f"expected_unknown[{dimension}] has invalid side {side!r}")

    evolve_ticks = sorted(record.tick for record in evolve_records)
    py_ticks = sorted(record.tick for record in py_records)
    if not evolve_ticks:
        raise DifferentialError("evolve replay side is empty")
    if not py_ticks:
        raise DifferentialError("python-agent replay side is empty")
    if len(evolve_ticks) != len(set(evolve_ticks)):
        raise DifferentialError("duplicate tick on the evolve replay side")
    if len(py_ticks) != len(set(py_ticks)):
        raise DifferentialError("duplicate tick on the python-agent replay side")

    evolve_trace_ticks = sorted(record.tick for record in evolve_decision_trace)
    if evolve_trace_ticks and evolve_trace_ticks != evolve_ticks:
        raise DifferentialError(
            "evolve decision trace tick set does not match the evolve replay side"
        )
    snapshot_ticks = sorted(record.tick for record in py_observation_snapshots)
    if snapshot_ticks and snapshot_ticks != py_ticks:
        raise DifferentialError(
            "python observation snapshot tick set does not match the python-agent side"
        )

    results: list[KpiDimensionResult] = []
    for dimension in KpiDimension:
        evolve_evidence = _extract_evolve(dimension, evolve_records, evolve_decision_trace)
        py_evidence = _extract_py(dimension, py_records, py_observation_snapshots, py_loop_metrics)
        status, reason = _classify_dimension(
            dimension, evolve_evidence, py_evidence, expected_unknown
        )
        results.append(
            KpiDimensionResult(
                dimension=dimension,
                status=status,
                reason=reason,
                evolve=None if evolve_evidence is None else evolve_evidence.to_json(),
                python_agent=None if py_evidence is None else py_evidence.to_json(),
            )
        )

    covered = {result.dimension for result in results}
    unclassified = set(KpiDimension) - covered
    if unclassified:
        raise DifferentialError(
            "classifier left dimensions unclassified: "
            + ", ".join(sorted(dimension.value for dimension in unclassified))
        )

    ordered = tuple(sorted(results, key=lambda result: result.dimension.value))
    counts = {status: 0 for status in DifferentialStatus}
    for result in ordered:
        counts[result.status] += 1
    artifact: dict[str, JsonValue] = {
        "schema_version": KPI_DIFFERENTIAL_SCHEMA,
        "dataset_id": dataset_id,
        "tenant_id": tenant_id,
        "protocols": {"evolve": evolve_protocol, "python_agent": py_protocol},
        "evidence_kind": evidence_kind,
        "dimensions": [result.to_json() for result in ordered],
        "counts": {status.value: counts[status] for status in DifferentialStatus},
        "unclassified_count": 0,
    }
    digest = content_sha256(artifact)
    return KpiReport(
        schema_version=KPI_DIFFERENTIAL_SCHEMA,
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        evolve_protocol=evolve_protocol,
        py_protocol=py_protocol,
        evidence_kind=evidence_kind,
        dimensions=ordered,
        counts=counts,
        unclassified_count=0,
        artifact=artifact,
        artifact_sha256=digest,
    )


def build_kpi_differential_run(
    *,
    evolve_manifest_path: str | Path,
    evolve_data_dir: str | Path,
    py_records_path: str | Path,
    dataset_id: str,
    tenant_id: str,
    evolve_decision_trace: str | Path | None = None,
    py_observation_snapshots: str | Path | None = None,
    evidence_kind: str = SANITIZED_EVIDENCE_KIND,
    expected_unknown: Mapping[KpiDimension, str] = DEFAULT_KPI_EXPECTED_UNKNOWN,
) -> KpiReport:
    """Load both sides (plus optional companion evidence) and classify the run."""
    evolve_records, _ = load_ts_legacy_corpus(evolve_manifest_path, evolve_data_dir)
    py_records, py_metrics = load_py_agent_corpus(py_records_path, tenant_id=tenant_id)
    decisions = (
        load_evolve_decision_trace(evolve_decision_trace, tenant_id=tenant_id)
        if evolve_decision_trace is not None
        else ()
    )
    snapshots = (
        load_py_observation_snapshots(py_observation_snapshots, tenant_id=tenant_id)
        if py_observation_snapshots is not None
        else ()
    )
    return classify_kpi_differential(
        evolve_records=evolve_records,
        py_records=py_records,
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        evolve_decision_trace=decisions,
        py_observation_snapshots=snapshots,
        py_loop_metrics=py_metrics,
        evidence_kind=evidence_kind,
        expected_unknown=expected_unknown,
    )


def run_kpi_differential_from_manifest(manifest_path: str | Path) -> KpiReport:
    """Load a KPI differential run manifest and produce the report.

    The manifest binds the evolve corpus, the Python run, optional sanitized
    companion fixtures, the run identity, and the per-dimension
    expected-unknown contract. Paths inside the manifest are resolved relative
    to the manifest file.
    """
    path = Path(manifest_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DifferentialError(f"cannot read run manifest {path.name}: {exc}") from exc
    run = _strict_object(raw, "run manifest")
    if run.get("schemaVersion") != KPI_DIFFERENTIAL_SCHEMA:
        raise DifferentialError(
            f"unsupported run manifest schemaVersion {run.get('schemaVersion')!r}"
        )
    dataset_id = _strict_str(run.get("dataset_id"), "dataset_id")
    tenant_id = _strict_str(run.get("tenant_id"), "tenant_id")
    evidence_kind = _strict_str(run.get("evidence_kind"), "evidence_kind")
    expected_unknown: dict[KpiDimension, str] = {}
    for key, side in _strict_object(run.get("expected_unknown"), "expected_unknown").items():
        try:
            dimension = KpiDimension(str(key))
        except ValueError as exc:
            raise DifferentialError(f"unknown dimension in expected_unknown: {key!r}") from exc
        if side not in ("evolve", "python_agent"):
            raise DifferentialError(f"expected_unknown[{key}] has invalid side {side!r}")
        expected_unknown[dimension] = str(side)
    evolve = _strict_object(run.get("evolve"), "evolve")
    python = _strict_object(run.get("python_agent"), "python_agent")
    base = path.parent
    return build_kpi_differential_run(
        evolve_manifest_path=base / _strict_str(evolve.get("manifest"), "evolve.manifest"),
        evolve_data_dir=base / _strict_str(evolve.get("data_dir"), "evolve.data_dir"),
        py_records_path=base / _strict_str(python.get("records"), "python_agent.records"),
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        evolve_decision_trace=(
            base / _strict_str(evolve.get("decision_trace"), "evolve.decision_trace")
            if evolve.get("decision_trace") is not None
            else None
        ),
        py_observation_snapshots=(
            base
            / _strict_str(python.get("observation_snapshots"), "python_agent.observation_snapshots")
            if python.get("observation_snapshots") is not None
            else None
        ),
        evidence_kind=evidence_kind,
        expected_unknown=expected_unknown,
    )


__all__ = [
    "DECISION_TRACE_SCHEMA_VERSION",
    "DEFAULT_KPI_EXPECTED_UNKNOWN",
    "EVOLVE_PROTOCOL",
    "GENERATOR_VERSION",
    "KPI_DIFFERENTIAL_SCHEMA",
    "KPI_SCHEMA_VERSION",
    "OBSERVATION_SCHEMA_VERSION",
    "PRODUCTION_EVIDENCE_KIND",
    "PY_AGENT_PROTOCOL",
    "SANITIZED_EVIDENCE_KIND",
    "EvolveDecisionRecord",
    "KpiDimension",
    "KpiDimensionResult",
    "KpiReport",
    "KpiSideEvidence",
    "PyObservationRecord",
    "build_kpi_differential_run",
    "classify_kpi_differential",
    "load_evolve_decision_trace",
    "load_py_observation_snapshots",
    "run_kpi_differential_from_manifest",
]
