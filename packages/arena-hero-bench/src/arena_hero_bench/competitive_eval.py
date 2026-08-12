"""Competitive evaluation battery (P6-7): scenario x seed x contestant orchestration.

The battery runs every (scenario, seed, contestant) cell through the versioned
offline agent importer and the P6-3 KPI differential classifier, then emits one
deterministic, content-addressed report with per-cell digests, aggregate stats,
and a presentation-level contestant ranking. It never imports the agent
package: the Python-agent side is always consumed from committed
``agent-run-v1`` records (and optional observation snapshots) through
:func:`arena_hero_bench.agent_runtime.import_agent_run` (via
:func:`arena_hero_bench.kpi_differential.build_kpi_differential_run`).

Trust and attestation notes
---------------------------
- A cell is a KPI differential between the scenario's evolve corpus and one
  contestant run; every dimension receives exactly one of ``MATCH`` /
  ``MISMATCH`` / ``EXPECTED_UNKNOWN`` / ``INCONCLUSIVE`` and a clean battery
  always reports ``unclassified_count == 0``.
- The battery is fail-closed: a corrupt manifest, a missing cell record, a
  cell classification error, or any unclassified dimension fails the whole
  battery with a classified issue. Cells that fail are still reported so the
  failure is diagnosable.
- The report is deterministic and content-addressed: identical inputs produce
  the same ``artifact_sha256`` and input order is fixed by the manifest
  (scenarios -> seeds -> contestants). No wall-clock timestamps enter the
  artifact.
- ``run_battery_from_manifest`` accepts a private ``cell_factory`` seam for
  reverse-validation tests; any report that used injected cells is marked
  ``injected_cells=true`` and ``attested=false`` and is never attestation-grade.

Live-agent seam
---------------
The manifest binds committed records paths by default. Optionally, the
battery consumes live offline agent runs produced by the external
``arena-hero-agent`` CLI through an external uv environment (no dependency on
the agent package or SDK): ``agent_runs_dir`` (argument, manifest field, or
``ARENA_AGENT_RUNS_DIR``) points at a batch output directory laid out as
``<runs_dir>/<contestant_id>/<scenario_id>/<seed_id>/<tenant_id>/
ticks.jsonl``, and :func:`map_agent_runs_dir` maps each run to its cell
records path. A missing directory, an incomplete/extra cell, or a run whose
tenant/schema does not match the battery fails the whole battery closed --
it never silently falls back to committed fixtures.

Ranking semantics
-----------------
``ranking`` is a presentation-level aggregation over the per-cell content
digests (score = total MATCH across cells, ties broken by fewer MISMATCH then
contestant id) and is never authoritative match rank: per-match rank semantics
remain the external report.v3 contract (see ``BLOCKED.md``).
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from arena_hero_bench.agent_runtime import AGENT_RECORD_SCHEMA_VERSION
from arena_hero_bench.differential import DifferentialError, DifferentialStatus
from arena_hero_bench.kpi_differential import (
    KpiDimension,
    KpiReport,
    build_kpi_differential_run,
)
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

COMPETITIVE_EVAL_SCHEMA: Final = "arena.bench.competitive-eval.v1"
BATTERY_REPORT_SCHEMA: Final = "arena.bench.competitive-eval-report.v1"
BATTERY_GENERATOR_VERSION: Final = "0.1.0"
RANKING_KIND: Final = "aggregate_match_count"
EVIDENCE_KINDS: Final = frozenset({"sanitized_fixture", "production"})
_ALLOWED_UNKNOWN_SIDES: Final = frozenset({"evolve", "python_agent"})
AGENT_RUNS_DIR_ENV: Final = "ARENA_AGENT_RUNS_DIR"
AGENT_RUN_RECORDS_FILE: Final = "ticks.jsonl"

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_CELL_STATUS_OK = "ok"
_CELL_STATUS_ERROR = "error"


class CompetitiveEvalError(ValueError):
    """Base error for the competitive evaluation battery."""


class BatteryManifestError(CompetitiveEvalError):
    """A battery run manifest is invalid or unsupported."""


class AgentRunsError(CompetitiveEvalError):
    """The live-agent runs directory cannot be resolved into cell records."""


class BatteryStatus(StrEnum):
    """Overall result of one competitive evaluation battery."""

    PASS = "pass"
    FAIL = "fail"


class BatteryIssueKind(StrEnum):
    """Fail-closed classification for every battery anomaly."""

    CELL_ERROR = "cell_error"
    UNCLASSIFIED = "unclassified"


def _strict_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BatteryManifestError(f"{field_name} must be an integer")
    return value


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BatteryManifestError(f"{field_name} must be a non-empty string")
    return value.strip()


def _strict_optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _strict_str(value, field_name)


def _strict_object(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BatteryManifestError(f"{field_name} must be an object")
    return value


def _strict_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise BatteryManifestError(f"{field_name} must be an array")
    return value


def _identifier(value: object, field_name: str) -> str:
    normalized = _strict_str(value, field_name)
    if not _IDENTIFIER.fullmatch(normalized):
        raise BatteryManifestError(f"{field_name} must be a lowercase portable identifier")
    return normalized


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    narrowed = to_json_value(value)
    if not isinstance(narrowed, dict):
        raise BatteryManifestError("expected a JSON object")
    return narrowed


@dataclass(frozen=True, slots=True)
class EvolveSide:
    """The evolve-baseline corpus of one battery scenario."""

    manifest: Path
    data_dir: Path
    decision_trace: Path | None = None


@dataclass(frozen=True, slots=True)
class BatteryScenario:
    """One scenario of a battery: an evolve corpus and optional snapshots."""

    scenario_id: str
    evolve: EvolveSide
    observation_snapshots: Path | None = None


@dataclass(frozen=True, slots=True)
class BatteryContestant:
    """One contestant of a battery with per-scenario, per-seed record paths."""

    contestant_id: str
    version: str
    protocol: str
    records: Mapping[str, Mapping[str, Path]]


@dataclass(frozen=True, slots=True)
class BatteryCellSpec:
    """One (scenario, seed, contestant) cell of a battery."""

    scenario: BatteryScenario
    seed_id: str
    contestant: BatteryContestant
    records_path: Path


@dataclass(frozen=True, slots=True)
class BatteryManifest:
    """A fully validated competitive evaluation battery."""

    schema_version: str
    battery_id: str
    dataset_id: str
    tenant_id: str
    evidence_kind: str
    expected_unknown: Mapping[KpiDimension, str]
    scenarios: tuple[BatteryScenario, ...]
    seeds: tuple[str, ...]
    contestants: tuple[BatteryContestant, ...]
    agent_runs_dir: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema_version", _strict_str(self.schema_version, "schema_version")
        )
        if self.schema_version != COMPETITIVE_EVAL_SCHEMA:
            raise BatteryManifestError(
                f"unsupported battery manifest schemaVersion {self.schema_version!r}"
            )
        object.__setattr__(self, "battery_id", _identifier(self.battery_id, "battery_id"))
        object.__setattr__(self, "dataset_id", _identifier(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "tenant_id", _identifier(self.tenant_id, "tenant_id"))
        if self.evidence_kind not in EVIDENCE_KINDS:
            raise BatteryManifestError(f"unsupported evidence_kind {self.evidence_kind!r}")
        object.__setattr__(
            self, "agent_runs_dir", _strict_optional_str(self.agent_runs_dir, "agent_runs_dir")
        )
        for dimension, side in self.expected_unknown.items():
            if not isinstance(dimension, KpiDimension):
                raise BatteryManifestError(f"unknown dimension in expected_unknown: {dimension!r}")
            if side not in _ALLOWED_UNKNOWN_SIDES:
                raise BatteryManifestError(
                    f"expected_unknown[{dimension}] has invalid side {side!r}"
                )
        if not self.scenarios:
            raise BatteryManifestError("battery manifest must declare at least one scenario")
        if not self.seeds:
            raise BatteryManifestError("battery manifest must declare at least one seed")
        if not self.contestants:
            raise BatteryManifestError("battery manifest must declare at least one contestant")
        scenario_ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise BatteryManifestError("battery scenario ids must be unique")
        if len(self.seeds) != len(set(self.seeds)):
            raise BatteryManifestError("battery seed ids must be unique")
        keys = [(contestant.contestant_id, contestant.version) for contestant in self.contestants]
        if len(keys) != len(set(keys)):
            raise BatteryManifestError("contestant (id, version) pairs must be unique")
        for contestant in self.contestants:
            if set(contestant.records) != set(scenario_ids):
                raise BatteryManifestError(
                    f"contestant {contestant.contestant_id} records must cover exactly "
                    f"the declared scenarios"
                )
            for scenario_id, by_seed in contestant.records.items():
                if set(by_seed) != set(self.seeds):
                    raise BatteryManifestError(
                        f"contestant {contestant.contestant_id} records for scenario "
                        f"{scenario_id!r} must cover exactly the declared seeds"
                    )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": self.schema_version,
            "battery_id": self.battery_id,
            "dataset_id": self.dataset_id,
            "tenant_id": self.tenant_id,
            "evidence_kind": self.evidence_kind,
            "agent_runs_dir": self.agent_runs_dir,
            "expected_unknown": {
                dimension.value: side for dimension, side in self.expected_unknown.items()
            },
            "scenarios": [
                {
                    "id": scenario.scenario_id,
                    "evolve": {
                        "manifest": str(scenario.evolve.manifest),
                        "data_dir": str(scenario.evolve.data_dir),
                        "decision_trace": (
                            None
                            if scenario.evolve.decision_trace is None
                            else str(scenario.evolve.decision_trace)
                        ),
                    },
                    "observation_snapshots": (
                        None
                        if scenario.observation_snapshots is None
                        else str(scenario.observation_snapshots)
                    ),
                }
                for scenario in self.scenarios
            ],
            "seeds": list(self.seeds),
            "contestants": [
                {
                    "id": contestant.contestant_id,
                    "version": contestant.version,
                    "protocol": contestant.protocol,
                    "records": {
                        scenario_id: {seed: str(path) for seed, path in by_seed.items()}
                        for scenario_id, by_seed in contestant.records.items()
                    },
                }
                for contestant in self.contestants
            ],
        }


@dataclass(frozen=True, slots=True)
class BatteryIssue:
    """One classified anomaly with the cell it belongs to."""

    kind: BatteryIssueKind
    scenario_id: str
    seed_id: str
    contestant_id: str
    detail: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "scenario": self.scenario_id,
            "seed": self.seed_id,
            "contestant": self.contestant_id,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class BatteryCellOutcome:
    """The result of one battery cell."""

    scenario_id: str
    seed_id: str
    contestant_id: str
    contestant_version: str
    status: str
    kpi_artifact_sha256: str | None
    error: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "scenario": self.scenario_id,
            "seed": self.seed_id,
            "contestant": self.contestant_id,
            "contestant_version": self.contestant_version,
            "status": self.status,
            "kpi_artifact_sha256": self.kpi_artifact_sha256,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ContestantAggregate:
    """Per-scenario, per-contestant dimension counts across seeds."""

    samples: int
    dimensions: Mapping[KpiDimension, Mapping[DifferentialStatus, int]]

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "samples": self.samples,
            "dimensions": {
                dimension.value: {
                    status.value: counts.get(status, 0) for status in DifferentialStatus
                }
                for dimension, counts in self.dimensions.items()
            },
        }


@dataclass(frozen=True, slots=True)
class RankedContestant:
    """One contestant ranked by a documented aggregate metric."""

    contestant_id: str
    version: str
    score: int
    mismatch_count: int
    samples: int

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "contestant": self.contestant_id,
            "version": self.version,
            "score": self.score,
            "mismatch_count": self.mismatch_count,
            "samples": self.samples,
        }


@dataclass(frozen=True, slots=True)
class BatteryReport:
    """Deterministic, content-addressed competitive evaluation report."""

    schema_version: str
    generator_version: str
    battery_id: str
    dataset_id: str
    tenant_id: str
    evidence_kind: str
    status: BatteryStatus
    attested: bool
    injected_cells: bool
    ranking_kind: str
    cells: tuple[BatteryCellOutcome, ...]
    aggregates: Mapping[str, Mapping[str, ContestantAggregate]]
    ranking: tuple[RankedContestant, ...]
    counts: Mapping[DifferentialStatus, int]
    unclassified_count: int
    issues: tuple[BatteryIssue, ...]
    artifact: Mapping[str, JsonValue]
    artifact_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
            "battery_id": self.battery_id,
            "dataset_id": self.dataset_id,
            "tenant_id": self.tenant_id,
            "evidence_kind": self.evidence_kind,
            "status": self.status.value,
            "attested": self.attested,
            "injected_cells": self.injected_cells,
            "ranking_kind": self.ranking_kind,
            "cells": [cell.to_json() for cell in self.cells],
            "aggregates": {
                scenario_id: {
                    contestant_id: aggregate.to_json()
                    for contestant_id, aggregate in by_contestant.items()
                }
                for scenario_id, by_contestant in self.aggregates.items()
            },
            "ranking": [entry.to_json() for entry in self.ranking],
            "counts": {status.value: self.counts.get(status, 0) for status in DifferentialStatus},
            "unclassified_count": self.unclassified_count,
            "issues": [issue.to_json() for issue in self.issues],
            "artifact_sha256": self.artifact_sha256,
        }


BatteryCellFactory = Callable[[BatteryCellSpec, Path], Callable[[], KpiReport] | None]


def load_battery_manifest(manifest_path: str | Path) -> BatteryManifest:
    """Parse and validate one competitive evaluation battery manifest."""
    path = Path(manifest_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BatteryManifestError(f"cannot read battery manifest {path.name}: {exc}") from exc
    manifest = _strict_object(raw, "battery manifest")
    schema = _strict_str(manifest.get("schemaVersion"), "schemaVersion")
    if schema != COMPETITIVE_EVAL_SCHEMA:
        raise BatteryManifestError(f"unsupported battery manifest schemaVersion {schema!r}")
    battery_id = _identifier(manifest.get("battery_id"), "battery_id")
    dataset_id = _identifier(manifest.get("dataset_id"), "dataset_id")
    tenant_id = _identifier(manifest.get("tenant_id"), "tenant_id")
    evidence_kind = _strict_str(manifest.get("evidence_kind"), "evidence_kind")
    agent_runs_dir = _strict_optional_str(manifest.get("agent_runs_dir"), "agent_runs_dir")
    expected_unknown: dict[KpiDimension, str] = {}
    for key, side in _strict_object(
        manifest.get("expected_unknown", {}), "expected_unknown"
    ).items():
        try:
            dimension = KpiDimension(str(key))
        except ValueError as exc:
            raise BatteryManifestError(f"unknown dimension in expected_unknown: {key!r}") from exc
        side_value = _strict_str(side, f"expected_unknown[{key}]")
        if side_value not in _ALLOWED_UNKNOWN_SIDES:
            raise BatteryManifestError(f"expected_unknown[{key}] has invalid side {side_value!r}")
        expected_unknown[dimension] = side_value

    scenarios_raw = _strict_list(manifest.get("scenarios"), "scenarios")
    if not scenarios_raw:
        raise BatteryManifestError("battery manifest must declare at least one scenario")
    scenarios: list[BatteryScenario] = []
    for index, item in enumerate(scenarios_raw):
        scenario = _strict_object(item, f"scenarios[{index}]")
        scenario_id = _identifier(scenario.get("id"), f"scenarios[{index}].id")
        evolve_raw = _strict_object(scenario.get("evolve"), f"scenarios[{index}].evolve")
        evolve_manifest = Path(
            _strict_str(evolve_raw.get("manifest"), f"scenarios[{index}].evolve.manifest")
        )
        evolve_data_dir = Path(
            _strict_str(evolve_raw.get("data_dir"), f"scenarios[{index}].evolve.data_dir")
        )
        decision_trace = _strict_optional_str(
            evolve_raw.get("decision_trace"), f"scenarios[{index}].evolve.decision_trace"
        )
        snapshots = _strict_optional_str(
            scenario.get("observation_snapshots"),
            f"scenarios[{index}].observation_snapshots",
        )
        scenarios.append(
            BatteryScenario(
                scenario_id=scenario_id,
                evolve=EvolveSide(
                    manifest=evolve_manifest,
                    data_dir=evolve_data_dir,
                    decision_trace=None if decision_trace is None else Path(decision_trace),
                ),
                observation_snapshots=None if snapshots is None else Path(snapshots),
            )
        )

    seeds_raw = _strict_list(manifest.get("seeds"), "seeds")
    seeds = [_identifier(seed, "seeds") for seed in seeds_raw]
    if not seeds:
        raise BatteryManifestError("battery manifest must declare at least one seed")

    contestants_raw = _strict_list(manifest.get("contestants"), "contestants")
    if not contestants_raw:
        raise BatteryManifestError("battery manifest must declare at least one contestant")
    contestants: list[BatteryContestant] = []
    for index, item in enumerate(contestants_raw):
        contestant = _strict_object(item, f"contestants[{index}]")
        contestant_id = _identifier(contestant.get("id"), f"contestants[{index}].id")
        version = _strict_str(contestant.get("version"), f"contestants[{index}].version")
        protocol = _strict_str(contestant.get("protocol"), f"contestants[{index}].protocol")
        records_raw = _strict_object(contestant.get("records"), f"contestants[{index}].records")
        records: dict[str, dict[str, Path]] = {}
        for scenario_key_raw, by_seed_raw in records_raw.items():
            scenario_key = _identifier(scenario_key_raw, f"contestants[{index}].records key")
            by_seed = _strict_object(
                by_seed_raw, f"contestants[{index}].records[{scenario_key_raw}]"
            )
            records[scenario_key] = {
                _identifier(
                    seed_key, f"contestants[{index}].records[{scenario_key_raw}] key"
                ): Path(
                    _strict_str(
                        record_path,
                        f"contestants[{index}].records[{scenario_key_raw}][{seed_key}]",
                    )
                )
                for seed_key, record_path in by_seed.items()
            }
        contestants.append(
            BatteryContestant(
                contestant_id=contestant_id,
                version=version,
                protocol=protocol,
                records=records,
            )
        )

    return BatteryManifest(
        schema_version=COMPETITIVE_EVAL_SCHEMA,
        battery_id=battery_id,
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        evidence_kind=evidence_kind,
        agent_runs_dir=agent_runs_dir,
        expected_unknown=expected_unknown,
        scenarios=tuple(scenarios),
        seeds=tuple(seeds),
        contestants=tuple(contestants),
    )


def _cells_for(manifest: BatteryManifest, base: Path) -> list[BatteryCellSpec]:
    """Expand the battery into cells in fixed manifest order."""
    cells: list[BatteryCellSpec] = []
    for scenario in manifest.scenarios:
        for seed_id in manifest.seeds:
            for contestant in manifest.contestants:
                cells.append(
                    BatteryCellSpec(
                        scenario=scenario,
                        seed_id=seed_id,
                        contestant=contestant,
                        records_path=base / contestant.records[scenario.scenario_id][seed_id],
                    )
                )
    return cells


def _validate_live_run_identity(
    records: Path, tenant_id: str, contestant_id: str, scenario_id: str, seed_id: str
) -> None:
    """Fail closed unless a live run's records carry the battery tenant.

    The first record of every mapped ``ticks.jsonl`` must be an
    ``agent-run-v1`` JSON object whose ``schemaVersion`` and ``tenantId``
    match the battery contract, so a live run from another tenant can never
    be silently attributed to this battery.
    """
    try:
        text = records.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentRunsError(
            f"agent run records for cell {contestant_id}/{scenario_id}/{seed_id} "
            f"could not be read: {exc}"
        ) from exc
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentRunsError(
                f"agent run records for cell {contestant_id}/{scenario_id}/{seed_id} "
                f"contain corrupt JSON: {exc}"
            ) from exc
        if not isinstance(record, Mapping):
            raise AgentRunsError(
                f"agent run records for cell {contestant_id}/{scenario_id}/{seed_id} "
                "must be JSON objects"
            )
        if record.get("tenantId") != tenant_id:
            raise AgentRunsError(
                f"agent run records for cell {contestant_id}/{scenario_id}/{seed_id} "
                f"carry tenantId {record.get('tenantId')!r} which does not match "
                f"the battery tenant {tenant_id!r}"
            )
        if record.get("schemaVersion") != AGENT_RECORD_SCHEMA_VERSION:
            raise AgentRunsError(
                f"agent run records for cell {contestant_id}/{scenario_id}/{seed_id} "
                "carry an unsupported schemaVersion"
            )
        return
    raise AgentRunsError(
        f"agent run records for cell {contestant_id}/{scenario_id}/{seed_id} are empty"
    )


def map_agent_runs_dir(
    runs_dir: str | Path, manifest: BatteryManifest
) -> dict[str, dict[str, dict[str, Path]]]:
    """Map a live agent batch output directory to per-cell record paths.

    The external agent output directory uses the lab-owned layout
    ``<runs_dir>/<contestant_id>/<scenario_id>/<seed_id>/<tenant_id>/
    ticks.jsonl`` -- exactly what ``arena-hero-agent run --data-root
    <runs_dir>/<contestant_id>/<scenario_id>/<seed_id>`` writes for the
    battery tenant: one run directory per battery cell whose ``ticks.jsonl``
    holds ``agent-run-v1`` records (``schemaVersion`` / ``recordType`` /
    ``tenantId`` / ``tick`` / ``deadlineOutcome`` / ``submitResult`` ...) as
    consumed by the offline importer. The mapping is exact: every declared
    cell must have records and any extra run directory fails closed, so a
    live battery can never silently fall back to committed fixtures. Each
    run's tenant identity must match the battery tenant.
    """
    root = Path(runs_dir)
    if not root.is_dir():
        raise AgentRunsError(
            f"agent runs directory is unavailable: {root} "
            "(configure --agent-runs-dir, manifest agent_runs_dir, or "
            f"{AGENT_RUNS_DIR_ENV})"
        )
    expected = {
        (contestant.contestant_id, scenario.scenario_id, seed_id)
        for contestant in manifest.contestants
        for scenario in manifest.scenarios
        for seed_id in manifest.seeds
    }
    mapped: dict[str, dict[str, dict[str, Path]]] = {}
    for contestant_id, scenario_id, seed_id in sorted(expected):
        cell_dir = root / contestant_id / scenario_id / seed_id
        records = cell_dir / manifest.tenant_id / AGENT_RUN_RECORDS_FILE
        if not records.is_file():
            raise AgentRunsError(
                f"agent run records missing for cell "
                f"{contestant_id}/{scenario_id}/{seed_id} (tenant "
                f"{manifest.tenant_id}): {records}"
            )
        _validate_live_run_identity(
            records, manifest.tenant_id, contestant_id, scenario_id, seed_id
        )
        mapped.setdefault(contestant_id, {}).setdefault(scenario_id, {})[seed_id] = records
    unexpected = sorted(
        (contestant.name, scenario.name, seed.name)
        for contestant in root.iterdir()
        if contestant.is_dir()
        for scenario in contestant.iterdir()
        if scenario.is_dir()
        for seed in scenario.iterdir()
        if seed.is_dir()
    )
    extra = [cell for cell in unexpected if cell not in expected]
    if extra:
        raise AgentRunsError(
            "agent runs directory contains runs not declared by the battery "
            "manifest: " + ", ".join("/".join(cell) for cell in extra)
        )
    return mapped


def _resolve_agent_runs_dir(
    manifest: BatteryManifest, base: Path, explicit: str | Path | None
) -> Path | None:
    """Resolve the live-agent runs directory: argument > manifest > env.

    Returns None when no live seam is configured, in which case the battery
    consumes the committed manifest record paths. A relative manifest or
    environment path is resolved against the manifest directory.
    """
    if explicit is not None:
        raw = str(explicit)
    elif manifest.agent_runs_dir is not None:
        raw = manifest.agent_runs_dir
    else:
        raw = os.environ.get(AGENT_RUNS_DIR_ENV)
    if raw is None or not raw.strip():
        return None
    path = Path(raw)
    return path if path.is_absolute() else base / path


def _default_cell(
    cell: BatteryCellSpec,
    base: Path,
    *,
    dataset_id: str,
    tenant_id: str,
    evidence_kind: str,
    expected_unknown: Mapping[KpiDimension, str],
) -> Callable[[], KpiReport]:
    """Build the real cell runner: one KPI differential for the cell."""
    scenario = cell.scenario
    evolve = scenario.evolve
    manifest_path = base / evolve.manifest
    data_dir = base / evolve.data_dir
    decision_trace = None if evolve.decision_trace is None else base / evolve.decision_trace
    snapshots = (
        None if scenario.observation_snapshots is None else base / scenario.observation_snapshots
    )
    records_path = cell.records_path

    def run() -> KpiReport:
        return build_kpi_differential_run(
            evolve_manifest_path=manifest_path,
            evolve_data_dir=data_dir,
            py_records_path=records_path,
            dataset_id=dataset_id,
            tenant_id=tenant_id,
            evolve_decision_trace=decision_trace,
            py_observation_snapshots=snapshots,
            evidence_kind=evidence_kind,
            expected_unknown=expected_unknown,
        )

    return run


def run_battery_from_manifest(
    manifest_path: str | Path,
    *,
    agent_runs_dir: str | Path | None = None,
    cell_factory: BatteryCellFactory | None = None,
) -> BatteryReport:
    """Run a competitive evaluation battery and return a fail-closed report.

    ``agent_runs_dir`` optionally points at an external agent batch output
    directory laid out as ``<runs_dir>/<contestant_id>/<scenario_id>/
    <seed_id>/<tenant_id>/ticks.jsonl`` (see :func:`map_agent_runs_dir`).
    When provided
    -- or configured through the manifest ``agent_runs_dir`` field or the
    ``ARENA_AGENT_RUNS_DIR`` environment variable -- every cell's records are
    resolved from that live directory instead of the committed manifest
    paths. A missing or malformed live directory fails the whole battery
    closed and never silently falls back to the committed fixtures.

    ``cell_factory`` is a private reverse-validation seam. When it returns a
    cell runner for a battery cell, that injected runner is used instead of the
    real one and the report is marked ``injected_cells=true`` /
    ``attested=false``.
    """
    path = Path(manifest_path)
    manifest = load_battery_manifest(path)
    base = path.parent
    resolved_runs_dir = _resolve_agent_runs_dir(manifest, base, agent_runs_dir)
    cells = _cells_for(manifest, base)
    if resolved_runs_dir is not None:
        mapped = map_agent_runs_dir(resolved_runs_dir, manifest)
        cells = [
            BatteryCellSpec(
                scenario=cell.scenario,
                seed_id=cell.seed_id,
                contestant=cell.contestant,
                records_path=mapped[cell.contestant.contestant_id][cell.scenario.scenario_id][
                    cell.seed_id
                ],
            )
            for cell in cells
        ]

    outcomes: list[BatteryCellOutcome] = []
    issues: list[BatteryIssue] = []
    injected_cells = False
    total_counts: Counter[DifferentialStatus] = Counter()
    total_unclassified = 0
    per_cell_reports: list[tuple[BatteryCellSpec, KpiReport]] = []

    for cell in cells:
        injected = None if cell_factory is None else cell_factory(cell, base)
        if injected is not None:
            runner = injected
            injected_cells = True
        else:
            runner = _default_cell(
                cell,
                base,
                dataset_id=manifest.dataset_id,
                tenant_id=manifest.tenant_id,
                evidence_kind=manifest.evidence_kind,
                expected_unknown=manifest.expected_unknown,
            )
        try:
            report = runner()
        except (DifferentialError, OSError, ValueError) as exc:
            outcomes.append(
                BatteryCellOutcome(
                    scenario_id=cell.scenario.scenario_id,
                    seed_id=cell.seed_id,
                    contestant_id=cell.contestant.contestant_id,
                    contestant_version=cell.contestant.version,
                    status=_CELL_STATUS_ERROR,
                    kpi_artifact_sha256=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            issues.append(
                BatteryIssue(
                    kind=BatteryIssueKind.CELL_ERROR,
                    scenario_id=cell.scenario.scenario_id,
                    seed_id=cell.seed_id,
                    contestant_id=cell.contestant.contestant_id,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        outcomes.append(
            BatteryCellOutcome(
                scenario_id=cell.scenario.scenario_id,
                seed_id=cell.seed_id,
                contestant_id=cell.contestant.contestant_id,
                contestant_version=cell.contestant.version,
                status=_CELL_STATUS_OK,
                kpi_artifact_sha256=report.artifact_sha256,
            )
        )
        per_cell_reports.append((cell, report))
        for status in DifferentialStatus:
            total_counts[status] += report.counts.get(status, 0)
        total_unclassified += report.unclassified_count
        if report.unclassified_count > 0:
            issues.append(
                BatteryIssue(
                    kind=BatteryIssueKind.UNCLASSIFIED,
                    scenario_id=cell.scenario.scenario_id,
                    seed_id=cell.seed_id,
                    contestant_id=cell.contestant.contestant_id,
                    detail=(
                        f"KPI differential left {report.unclassified_count} "
                        "dimension(s) unclassified"
                    ),
                )
            )

    aggregates = _aggregate(manifest, per_cell_reports)
    ranking = _rank(manifest, per_cell_reports)
    status = (
        BatteryStatus.PASS
        if not issues and total_unclassified == 0 and len(per_cell_reports) == len(cells)
        else BatteryStatus.FAIL
    )
    counts = {status: total_counts.get(status, 0) for status in DifferentialStatus}
    payload = {
        "schema_version": BATTERY_REPORT_SCHEMA,
        "generator_version": BATTERY_GENERATOR_VERSION,
        "battery_id": manifest.battery_id,
        "dataset_id": manifest.dataset_id,
        "tenant_id": manifest.tenant_id,
        "evidence_kind": manifest.evidence_kind,
        "status": status.value,
        "attested": status is BatteryStatus.PASS and not injected_cells,
        "injected_cells": injected_cells,
        "ranking_kind": RANKING_KIND,
        "cells": [outcome.to_json() for outcome in outcomes],
        "aggregates": {
            scenario_id: {
                contestant_id: aggregate.to_json()
                for contestant_id, aggregate in by_contestant.items()
            }
            for scenario_id, by_contestant in aggregates.items()
        },
        "ranking": [entry.to_json() for entry in ranking],
        "counts": counts,
        "unclassified_count": total_unclassified,
        "issues": [issue.to_json() for issue in issues],
    }
    artifact = _json_object(payload)
    artifact_sha256 = content_sha256(artifact)
    return BatteryReport(
        schema_version=BATTERY_REPORT_SCHEMA,
        generator_version=BATTERY_GENERATOR_VERSION,
        battery_id=manifest.battery_id,
        dataset_id=manifest.dataset_id,
        tenant_id=manifest.tenant_id,
        evidence_kind=manifest.evidence_kind,
        status=status,
        attested=status is BatteryStatus.PASS and not injected_cells,
        injected_cells=injected_cells,
        ranking_kind=RANKING_KIND,
        cells=tuple(outcomes),
        aggregates=aggregates,
        ranking=tuple(ranking),
        counts=counts,
        unclassified_count=total_unclassified,
        issues=tuple(issues),
        artifact=artifact,
        artifact_sha256=artifact_sha256,
    )


def _aggregate(
    manifest: BatteryManifest,
    reports: Sequence[tuple[BatteryCellSpec, KpiReport]],
) -> dict[str, dict[str, ContestantAggregate]]:
    """Per-scenario, per-contestant dimension status counts across seeds.

    The full scenario x contestant matrix is initialized with zero samples so a
    contestant whose cells all failed still appears in the report (fail-closed,
    diagnosable) instead of being silently dropped.
    """
    by_key: dict[tuple[str, str], dict[KpiDimension, Counter[DifferentialStatus]]] = {
        (scenario.scenario_id, contestant.contestant_id): {
            dimension: Counter() for dimension in KpiDimension
        }
        for scenario in manifest.scenarios
        for contestant in manifest.contestants
    }
    samples: dict[tuple[str, str], int] = {
        (scenario.scenario_id, contestant.contestant_id): 0
        for scenario in manifest.scenarios
        for contestant in manifest.contestants
    }
    for cell, report in reports:
        key = (cell.scenario.scenario_id, cell.contestant.contestant_id)
        dimensions = by_key[key]
        samples[key] += 1
        for dimension in KpiDimension:
            for result in report.dimensions:
                if result.dimension is dimension:
                    dimensions[dimension][result.status] += 1
                    break
    result: dict[str, dict[str, ContestantAggregate]] = {}
    for (scenario_id, contestant_id), dimensions in by_key.items():
        by_scenario = result.setdefault(scenario_id, {})
        by_scenario[contestant_id] = ContestantAggregate(
            samples=samples[(scenario_id, contestant_id)],
            dimensions={
                dimension: {status: counter.get(status, 0) for status in DifferentialStatus}
                for dimension, counter in dimensions.items()
            },
        )
    return result


def _rank(
    manifest: BatteryManifest,
    reports: Sequence[tuple[BatteryCellSpec, KpiReport]],
) -> list[RankedContestant]:
    """Deterministic presentation-level ranking across all cells."""
    totals: dict[str, dict[str, int]] = {
        contestant.contestant_id: {"match": 0, "mismatch": 0} for contestant in manifest.contestants
    }
    samples: dict[str, int] = {contestant.contestant_id: 0 for contestant in manifest.contestants}
    for cell, report in reports:
        key = cell.contestant.contestant_id
        totals[key]["match"] += report.counts.get(DifferentialStatus.MATCH, 0)
        totals[key]["mismatch"] += report.counts.get(DifferentialStatus.MISMATCH, 0)
        samples[key] += 1
    ranked = sorted(
        manifest.contestants,
        key=lambda contestant: (
            -totals[contestant.contestant_id]["match"],
            totals[contestant.contestant_id]["mismatch"],
            contestant.contestant_id,
        ),
    )
    return [
        RankedContestant(
            contestant_id=contestant.contestant_id,
            version=contestant.version,
            score=totals[contestant.contestant_id]["match"],
            mismatch_count=totals[contestant.contestant_id]["mismatch"],
            samples=samples[contestant.contestant_id],
        )
        for contestant in ranked
    ]


__all__ = [
    "AGENT_RUNS_DIR_ENV",
    "AGENT_RUN_RECORDS_FILE",
    "BATTERY_GENERATOR_VERSION",
    "BATTERY_REPORT_SCHEMA",
    "COMPETITIVE_EVAL_SCHEMA",
    "RANKING_KIND",
    "AgentRunsError",
    "BatteryCellOutcome",
    "BatteryCellSpec",
    "BatteryContestant",
    "BatteryIssue",
    "BatteryIssueKind",
    "BatteryManifest",
    "BatteryManifestError",
    "BatteryReport",
    "BatteryScenario",
    "BatteryStatus",
    "CompetitiveEvalError",
    "ContestantAggregate",
    "EvolveSide",
    "RankedContestant",
    "load_battery_manifest",
    "map_agent_runs_dir",
    "run_battery_from_manifest",
]
