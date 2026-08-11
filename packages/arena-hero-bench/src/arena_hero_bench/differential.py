"""TS/Python replay differential corpus and classifier (P6-2).

The classifier compares one TS-legacy replay side against one Python-agent
replay side for a single run and classifies every comparison into exactly one
of four statuses: ``MATCH``, ``MISMATCH``, ``EXPECTED_UNKNOWN``, or
``INCONCLUSIVE``. The report is deterministic and content-addressed: the same
inputs always produce the same artifact digest, and reordering inputs does not
change it.

Classification semantics
------------------------
- ``MATCH``: both sides captured the dimension for the tick and their canonical
  forms are equal.
- ``MISMATCH``: both sides captured the dimension and their canonical forms
  differ (or a tick is present on only one side).
- ``EXPECTED_UNKNOWN``: the side's *protocol contract* does not capture the
  dimension (declared in the run manifest, not inferred). Missing evidence on
  both sides is also expected-unknown. This is never reported as a match.
- ``INCONCLUSIVE``: the protocol captures the dimension but this particular
  record lacks the field, so agreement cannot be confirmed. This is never
  reported as a match.

The TS-legacy side is derived from the committed ``differential-record-v1``
fixtures (raw tick snapshots plus the dataset manifest). The Python side is the
versioned offline agent run JSONL consumed through :func:`import_agent_run`, so
all fail-closed parsing, tenant checks, and torn-tail handling are inherited
from that importer.

The corpus does not embed, import, or reimplement the legacy TypeScript
runtime; it only consumes committed fixture data and the versioned wire record
contract.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from arena_hero_bench.agent_runtime import AgentTickRecord, import_agent_run
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

REPLAY_DIFFERENTIAL_SCHEMA: Final = "arena.bench.replay-differential.v1"
DIFFERENTIAL_SCHEMA_VERSION: Final = "v1"
TS_LEGACY_PROTOCOL: Final = "differential-record-v1"
PY_AGENT_PROTOCOL: Final = "agent-run-v1"
GENERATOR_VERSION: Final = "0.1.0"

_TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DATASET_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SEGMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PREFIX = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_MAP_MODES: Final = frozenset({"disabled", "frozen", "controlled"})
_OBJECT_KINDS: Final = frozenset({"CORE", "UNIT", "OBSTACLE", "RESOURCE"})


class DifferentialError(ValueError):
    """The differential corpus, canonical form, or classification is invalid."""


class DifferentialStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    EXPECTED_UNKNOWN = "EXPECTED_UNKNOWN"
    INCONCLUSIVE = "INCONCLUSIVE"


class DifferentialDimension(StrEnum):
    TICK_IDENTITY = "tick_identity"
    WORLD_STATE_DIGEST = "world_state_digest"
    DECISION_ACTION_CANONICAL = "decision_action_canonical"
    RECORD_ORDERING = "record_ordering"
    TERMINAL_METRICS = "terminal_metrics"


def _strict_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DifferentialError(f"{field_name} must be an integer")
    return value


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise DifferentialError(f"{field_name} must be a non-empty string")
    return value


def _strict_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise DifferentialError(f"{field_name} must be a boolean")
    return value


def _strict_optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _strict_int(value, field_name)


def _strict_optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _strict_str(value, field_name)


def _strict_object(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DifferentialError(f"{field_name} must be an object")
    return value


def _strict_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise DifferentialError(f"{field_name} must be an array")
    return value


def _position(value: object, field_name: str) -> list[int]:
    items = _strict_list(value, field_name)
    if len(items) != 2:
        raise DifferentialError(f"{field_name} must be a [x, y] position")
    return [_strict_int(item, field_name) for item in items]


def _lower_identifier(value: object, field_name: str) -> str:
    text = _strict_str(value, field_name)
    lowered = text.lower()
    if not _IDENTIFIER.fullmatch(lowered):
        raise DifferentialError(f"{field_name} is not a portable identifier")
    return lowered


def _sha256_ref(value: object, field_name: str) -> str:
    text = _strict_str(value, field_name)
    if not _SHA256_PREFIX.fullmatch(text):
        raise DifferentialError(f"{field_name} must be a sha256: reference")
    return text


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    """Narrow an ordinary mapping into a JSON object value."""
    narrowed = to_json_value(value)
    if not isinstance(narrowed, dict):
        raise DifferentialError("expected a JSON object")
    return narrowed


@dataclass(frozen=True, slots=True)
class TsLegacyCanonicalRecord:
    """One TS-legacy tick canonicalized from a committed differential fixture."""

    tick: int
    segment_id: str
    dataset_id: str
    map_mode: str
    config_hash: str
    input_sha256: str
    world_state: Mapping[str, JsonValue]
    decision: Mapping[str, JsonValue] | None = None
    metrics: Mapping[str, JsonValue] | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "tick": self.tick,
            "segment_id": self.segment_id,
            "dataset_id": self.dataset_id,
            "map_mode": self.map_mode,
            "config_hash": self.config_hash,
            "input_sha256": self.input_sha256,
            "world_state": dict(self.world_state),
            "decision": None if self.decision is None else dict(self.decision),
            "metrics": None if self.metrics is None else dict(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class PyAgentCanonicalRecord:
    """One Python-agent tick canonicalized from versioned offline JSONL."""

    tick: int
    tenant_id: str
    decision: Mapping[str, JsonValue]
    world_state: Mapping[str, JsonValue] | None = None
    metrics: Mapping[str, JsonValue] | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "tick": self.tick,
            "tenant_id": self.tenant_id,
            "decision": dict(self.decision),
            "world_state": None if self.world_state is None else dict(self.world_state),
            "metrics": None if self.metrics is None else dict(self.metrics),
        }


def canonicalize_ts_legacy_record(
    raw: Mapping[str, object],
    *,
    tick: int,
    segment_id: str,
    dataset_id: str,
    config_hash: str,
    map_mode: str,
    input_sha256: str,
) -> TsLegacyCanonicalRecord:
    """Project one raw ``differential-record-v1`` input snapshot into the canonical form.

    The projection follows the frozen legacy canonicalization rules: positions
    are ``[x, y]`` integer arrays, identifiers are lower-cased, units and
    enemies are sorted by id, cells are sorted, ``null`` means absent, and the
    key set of ``world_state`` is fixed so the digest is stable. Events are
    input history, not current state, and are intentionally excluded.
    """
    tick_value = _strict_int(tick, "tick")
    if tick_value < 0:
        raise DifferentialError("tick must be non-negative")
    segment = _strict_str(segment_id, "segment_id")
    if not _SEGMENT_ID.fullmatch(segment):
        raise DifferentialError("segment_id is not a portable identifier")
    dataset = _strict_str(dataset_id, "dataset_id")
    if not _DATASET_ID.fullmatch(dataset):
        raise DifferentialError("dataset_id is not a portable identifier")
    config = _sha256_ref(config_hash, "config_hash")
    if map_mode not in _MAP_MODES:
        raise DifferentialError(f"unsupported map_mode {map_mode!r}")
    input_ref = _sha256_ref(input_sha256, "input_sha256")

    status = _strict_str(raw.get("status"), "status")
    respawn_at_tick = _strict_optional_int(raw.get("respawn_at_tick"), "respawn_at_tick")
    resources = _strict_int(raw.get("resources"), "resources")
    population = _strict_int(raw.get("population"), "population")
    population_tier = _strict_int(raw.get("population_tier"), "population_tier")
    upkeep_next_tick = _strict_int(raw.get("upkeep_next_tick"), "upkeep_next_tick")

    beacon_raw = _strict_object(raw.get("champion_beacon"), "champion_beacon")
    beacon = _json_object(
        {
            "position": _position(beacon_raw.get("position"), "champion_beacon.position"),
            "status": _strict_optional_str(beacon_raw.get("status"), "champion_beacon.status"),
            "carrier_id": _strict_optional_str(
                beacon_raw.get("carrier_id"), "champion_beacon.carrier_id"
            ),
        }
    )

    objects = _strict_list(raw.get("objects"), "objects")
    cores: list[Mapping[str, object]] = []
    units: list[dict[str, JsonValue]] = []
    enemies: list[dict[str, JsonValue]] = []
    obstacle_cells: list[list[int]] = []
    resource_cells: list[list[int]] = []
    for index, item in enumerate(objects):
        obj = _strict_object(item, f"objects[{index}]")
        kind = _strict_str(obj.get("kind"), f"objects[{index}].kind")
        if kind not in _OBJECT_KINDS:
            raise DifferentialError(f"objects[{index}] has unsupported kind {kind!r}")
        if kind == "CORE":
            cores.append(obj)
        elif kind == "UNIT":
            unit_id = _lower_identifier(obj.get("id"), f"objects[{index}].id")
            position = _position(obj.get("position"), f"objects[{index}].position")
            unit_type = _strict_str(obj.get("unit_type"), f"objects[{index}].unit_type")
            if _strict_bool(obj.get("controlled"), f"objects[{index}].controlled"):
                units.append(
                    _json_object(
                        {
                            "id": unit_id,
                            "position": position,
                            "hp": _strict_int(obj.get("hp"), f"objects[{index}].hp"),
                            "cargo": _strict_optional_int(
                                obj.get("cargo"), f"objects[{index}].cargo"
                            ),
                            "unit_type": unit_type,
                        }
                    )
                )
            else:
                enemies.append(
                    _json_object(
                        {
                            "id": unit_id,
                            "position": position,
                            "unit_type": unit_type,
                        }
                    )
                )
        elif kind == "OBSTACLE":
            for cell in _strict_list(obj.get("positions"), f"objects[{index}].positions"):
                obstacle_cells.append(_position(cell, f"objects[{index}].positions"))
        elif kind == "RESOURCE":
            for cell in _strict_list(obj.get("positions"), f"objects[{index}].positions"):
                resource_cells.append(_position(cell, f"objects[{index}].positions"))

    if not cores:
        raise DifferentialError("raw snapshot has no CORE object")
    if len(cores) > 1:
        raise DifferentialError("raw snapshot has more than one CORE object")
    core_raw = cores[0]
    core = _json_object(
        {
            "id": _lower_identifier(core_raw.get("id"), "core.id"),
            "position": _position(core_raw.get("position"), "core.position"),
            "hp": _strict_int(core_raw.get("hp"), "core.hp"),
            "shield": _strict_int(core_raw.get("shield"), "core.shield"),
            "state": _strict_str(core_raw.get("state"), "core.state"),
        }
    )

    units_sorted = sorted(units, key=lambda item: str(item["id"]))
    enemies_sorted = sorted(enemies, key=lambda item: str(item["id"]))
    obstacle_sorted = sorted(obstacle_cells)
    resource_sorted = sorted(resource_cells)

    world_state = _json_object(
        {
            "status": status,
            "respawn_at_tick": respawn_at_tick,
            "resources": resources,
            "population": population,
            "population_tier": population_tier,
            "upkeep_next_tick": upkeep_next_tick,
            "core": core,
            "units": units_sorted,
            "enemies": enemies_sorted,
            "resource_cells": resource_sorted,
            "obstacle_cells": obstacle_sorted,
            "beacon": beacon,
        }
    )
    return TsLegacyCanonicalRecord(
        tick=tick_value,
        segment_id=segment,
        dataset_id=dataset,
        map_mode=map_mode,
        config_hash=config,
        input_sha256=input_ref,
        world_state=world_state,
    )


def world_state_digest(world_state: Mapping[str, JsonValue]) -> str:
    """Canonical SHA-256 of a canonical world state projection."""
    return content_sha256(to_json_value(world_state))


def canonicalize_py_agent_record(record: AgentTickRecord) -> PyAgentCanonicalRecord:
    """Canonicalize one validated offline agent tick record."""
    decision: dict[str, JsonValue] = {
        "decision_id": record.decision_id,
        "deadline_outcome": record.deadline_outcome,
        "submit_result": record.submit_result,
        "submit_error": record.submit_error,
    }
    return PyAgentCanonicalRecord(
        tick=record.tick,
        tenant_id=record.tenant_id,
        decision=decision,
    )


def py_agent_loop_metrics(
    loop: object,
) -> Mapping[str, JsonValue] | None:
    """Canonical terminal metrics from the offline agent loop summary."""
    from arena_hero_bench.agent_runtime import AgentLoopRecord

    if not isinstance(loop, AgentLoopRecord):
        return None
    return {
        "last_tick": loop.last_tick,
        "ticks_processed": loop.ticks_processed,
        "stopped_reason": loop.stopped_reason,
        "outcome_count": loop.outcome_count,
    }


@dataclass(frozen=True, slots=True)
class DifferentialOutcome:
    """One classified (tick, dimension) comparison."""

    tick: int | None
    dimension: DifferentialDimension
    status: DifferentialStatus
    reason: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "tick": self.tick,
            "dimension": self.dimension.value,
            "status": self.status.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class DifferentialReport:
    """Deterministic, content-addressed differential report for one run."""

    schema_version: str
    dataset_id: str
    tenant_id: str
    ts_protocol: str
    py_protocol: str
    outcomes: tuple[DifferentialOutcome, ...]
    counts: Mapping[DifferentialStatus, int]
    unclassified_count: int
    artifact: Mapping[str, JsonValue]
    artifact_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "tenant_id": self.tenant_id,
            "protocols": {"ts_legacy": self.ts_protocol, "python_agent": self.py_protocol},
            "outcomes": [outcome.to_json() for outcome in self.outcomes],
            "counts": {status.value: self.counts.get(status, 0) for status in DifferentialStatus},
            "unclassified_count": self.unclassified_count,
            "artifact_sha256": self.artifact_sha256,
        }


DEFAULT_EXPECTED_UNKNOWN: Final = {
    DifferentialDimension.WORLD_STATE_DIGEST: "python_agent",
    DifferentialDimension.DECISION_ACTION_CANONICAL: "ts_legacy",
    DifferentialDimension.TERMINAL_METRICS: "ts_legacy",
}


def _ordered_tick_sequence(
    records: Sequence[TsLegacyCanonicalRecord | PyAgentCanonicalRecord],
) -> list[int]:
    ticks = [record.tick for record in records]
    if len(set(ticks)) != len(ticks):
        raise DifferentialError("duplicate tick in a replay side")
    return sorted(ticks)


def _canonical_decision_form(
    record: TsLegacyCanonicalRecord | PyAgentCanonicalRecord,
) -> Mapping[str, JsonValue] | None:
    return record.decision


def _world_state_form(
    record: TsLegacyCanonicalRecord | PyAgentCanonicalRecord,
) -> Mapping[str, JsonValue] | None:
    return record.world_state


def classify_differential_run(
    *,
    ts_records: Sequence[TsLegacyCanonicalRecord],
    py_records: Sequence[PyAgentCanonicalRecord],
    dataset_id: str,
    tenant_id: str,
    ts_protocol: str = TS_LEGACY_PROTOCOL,
    py_protocol: str = PY_AGENT_PROTOCOL,
    ts_metrics: Mapping[str, JsonValue] | None = None,
    py_metrics: Mapping[str, JsonValue] | None = None,
    expected_unknown: Mapping[DifferentialDimension, str] = DEFAULT_EXPECTED_UNKNOWN,
) -> DifferentialReport:
    """Classify every comparison between one TS-legacy side and one Python side.

    Every tick present on either side receives one outcome for each of
    ``tick_identity``, ``world_state_digest``, and
    ``decision_action_canonical``; the run additionally receives one outcome for
    ``record_ordering`` and one for ``terminal_metrics``. Input order does not
    matter: sides are aligned by tick and the report is emitted in tick order.
    """
    if not _DATASET_ID.fullmatch(dataset_id):
        raise DifferentialError("dataset_id is not a portable identifier")
    if not _TENANT_ID.fullmatch(tenant_id):
        raise DifferentialError("tenant_id is not a portable identifier")
    ts_ticks = _ordered_tick_sequence(ts_records)
    py_ticks = _ordered_tick_sequence(py_records)
    if not ts_ticks:
        raise DifferentialError("ts-legacy replay side is empty")
    if not py_ticks:
        raise DifferentialError("python-agent replay side is empty")

    ts_by_tick = {record.tick: record for record in ts_records}
    py_by_tick = {record.tick: record for record in py_records}
    all_ticks = sorted(set(ts_ticks) | set(py_ticks))

    outcomes: list[DifferentialOutcome] = []
    for tick in all_ticks:
        ts_record = ts_by_tick.get(tick)
        py_record = py_by_tick.get(tick)

        if ts_record is not None and py_record is not None:
            outcomes.append(
                DifferentialOutcome(
                    tick,
                    DifferentialDimension.TICK_IDENTITY,
                    DifferentialStatus.MATCH,
                    "tick present on both sides",
                )
            )
        elif ts_record is not None:
            outcomes.append(
                DifferentialOutcome(
                    tick,
                    DifferentialDimension.TICK_IDENTITY,
                    DifferentialStatus.MISMATCH,
                    "tick present only on the ts-legacy side",
                )
            )
        else:
            outcomes.append(
                DifferentialOutcome(
                    tick,
                    DifferentialDimension.TICK_IDENTITY,
                    DifferentialStatus.MISMATCH,
                    "tick present only on the python-agent side",
                )
            )

        outcomes.append(_classify_world_state(tick, ts_record, py_record, expected_unknown))
        outcomes.append(_classify_decision_action(tick, ts_record, py_record, expected_unknown))

    outcomes.append(_classify_record_ordering(ts_ticks, py_ticks))
    outcomes.append(_classify_terminal_metrics(ts_metrics, py_metrics, expected_unknown))

    covered: set[tuple[int | None, DifferentialDimension]] = {
        (outcome.tick, outcome.dimension) for outcome in outcomes
    }
    expected_cells: set[tuple[int | None, DifferentialDimension]] = {
        (tick, dimension)
        for tick in all_ticks
        for dimension in (
            DifferentialDimension.TICK_IDENTITY,
            DifferentialDimension.WORLD_STATE_DIGEST,
            DifferentialDimension.DECISION_ACTION_CANONICAL,
        )
    }
    expected_cells.add((None, DifferentialDimension.RECORD_ORDERING))
    expected_cells.add((None, DifferentialDimension.TERMINAL_METRICS))
    unclassified = expected_cells - covered
    if unclassified:
        raise DifferentialError(
            "classifier left comparisons unclassified: "
            + ", ".join(sorted(f"{tick!r}:{dimension.value}" for tick, dimension in unclassified))
        )

    ordered = tuple(
        sorted(
            outcomes,
            key=lambda item: (item.tick is not None, item.tick or -1, item.dimension.value),
        )
    )
    counts = {status: 0 for status in DifferentialStatus}
    for outcome in ordered:
        counts[outcome.status] += 1
    artifact: dict[str, JsonValue] = {
        "schema_version": REPLAY_DIFFERENTIAL_SCHEMA,
        "dataset_id": dataset_id,
        "tenant_id": tenant_id,
        "protocols": {"ts_legacy": ts_protocol, "python_agent": py_protocol},
        "outcomes": [outcome.to_json() for outcome in ordered],
        "counts": {status.value: counts[status] for status in DifferentialStatus},
        "unclassified_count": 0,
    }
    digest = content_sha256(artifact)
    return DifferentialReport(
        schema_version=REPLAY_DIFFERENTIAL_SCHEMA,
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        ts_protocol=ts_protocol,
        py_protocol=py_protocol,
        outcomes=ordered,
        counts=counts,
        unclassified_count=0,
        artifact=artifact,
        artifact_sha256=digest,
    )


def _classify_world_state(
    tick: int,
    ts_record: TsLegacyCanonicalRecord | None,
    py_record: PyAgentCanonicalRecord | None,
    expected_unknown: Mapping[DifferentialDimension, str],
) -> DifferentialOutcome:
    ts_state = _world_state_form(ts_record) if ts_record is not None else None
    py_state = _world_state_form(py_record) if py_record is not None else None
    if ts_state is None and py_state is None:
        return DifferentialOutcome(
            tick,
            DifferentialDimension.WORLD_STATE_DIGEST,
            DifferentialStatus.EXPECTED_UNKNOWN,
            "neither side captures world state",
        )
    if ts_state is None:
        return DifferentialOutcome(
            tick,
            DifferentialDimension.WORLD_STATE_DIGEST,
            DifferentialStatus.INCONCLUSIVE,
            "ts-legacy record does not capture world state for this tick",
        )
    if py_state is None:
        if expected_unknown.get(DifferentialDimension.WORLD_STATE_DIGEST) == "python_agent":
            return DifferentialOutcome(
                tick,
                DifferentialDimension.WORLD_STATE_DIGEST,
                DifferentialStatus.EXPECTED_UNKNOWN,
                "python-agent contract does not capture world state",
            )
        return DifferentialOutcome(
            tick,
            DifferentialDimension.WORLD_STATE_DIGEST,
            DifferentialStatus.INCONCLUSIVE,
            "python-agent record does not capture world state for this tick",
        )
    ts_digest = world_state_digest(ts_state)
    py_digest = world_state_digest(py_state)
    if ts_digest == py_digest:
        return DifferentialOutcome(
            tick,
            DifferentialDimension.WORLD_STATE_DIGEST,
            DifferentialStatus.MATCH,
            "world state digests agree",
        )
    return DifferentialOutcome(
        tick,
        DifferentialDimension.WORLD_STATE_DIGEST,
        DifferentialStatus.MISMATCH,
        "world state digests differ",
    )


def _classify_decision_action(
    tick: int,
    ts_record: TsLegacyCanonicalRecord | None,
    py_record: PyAgentCanonicalRecord | None,
    expected_unknown: Mapping[DifferentialDimension, str],
) -> DifferentialOutcome:
    ts_decision = _canonical_decision_form(ts_record) if ts_record is not None else None
    py_decision = _canonical_decision_form(py_record) if py_record is not None else None
    if ts_decision is None and py_decision is None:
        return DifferentialOutcome(
            tick,
            DifferentialDimension.DECISION_ACTION_CANONICAL,
            DifferentialStatus.EXPECTED_UNKNOWN,
            "neither side captures decision evidence",
        )
    if py_decision is None:
        return DifferentialOutcome(
            tick,
            DifferentialDimension.DECISION_ACTION_CANONICAL,
            DifferentialStatus.INCONCLUSIVE,
            "python-agent record does not capture decision evidence for this tick",
        )
    if ts_decision is None:
        if expected_unknown.get(DifferentialDimension.DECISION_ACTION_CANONICAL) == "ts_legacy":
            return DifferentialOutcome(
                tick,
                DifferentialDimension.DECISION_ACTION_CANONICAL,
                DifferentialStatus.EXPECTED_UNKNOWN,
                "ts-legacy fixture contract does not capture runner plan",
            )
        return DifferentialOutcome(
            tick,
            DifferentialDimension.DECISION_ACTION_CANONICAL,
            DifferentialStatus.INCONCLUSIVE,
            "ts-legacy record does not capture decision evidence for this tick",
        )
    if content_sha256(to_json_value(dict(ts_decision))) == content_sha256(
        to_json_value(dict(py_decision))
    ):
        return DifferentialOutcome(
            tick,
            DifferentialDimension.DECISION_ACTION_CANONICAL,
            DifferentialStatus.MATCH,
            "decision canonical forms agree",
        )
    return DifferentialOutcome(
        tick,
        DifferentialDimension.DECISION_ACTION_CANONICAL,
        DifferentialStatus.MISMATCH,
        "decision canonical forms differ",
    )


def _classify_record_ordering(
    ts_ticks: Sequence[int],
    py_ticks: Sequence[int],
) -> DifferentialOutcome:
    if list(ts_ticks) == list(py_ticks):
        return DifferentialOutcome(
            None,
            DifferentialDimension.RECORD_ORDERING,
            DifferentialStatus.MATCH,
            "both sides cover the same ordered tick sequence",
        )
    ts_only = sorted(set(ts_ticks) - set(py_ticks))
    py_only = sorted(set(py_ticks) - set(ts_ticks))
    return DifferentialOutcome(
        None,
        DifferentialDimension.RECORD_ORDERING,
        DifferentialStatus.MISMATCH,
        f"tick sequences differ (ts-only={ts_only}, py-only={py_only})",
    )


def _classify_terminal_metrics(
    ts_metrics: Mapping[str, JsonValue] | None,
    py_metrics: Mapping[str, JsonValue] | None,
    expected_unknown: Mapping[DifferentialDimension, str],
) -> DifferentialOutcome:
    if ts_metrics is None and py_metrics is None:
        return DifferentialOutcome(
            None,
            DifferentialDimension.TERMINAL_METRICS,
            DifferentialStatus.EXPECTED_UNKNOWN,
            "neither side captures terminal metrics",
        )
    if ts_metrics is None:
        if expected_unknown.get(DifferentialDimension.TERMINAL_METRICS) == "ts_legacy":
            return DifferentialOutcome(
                None,
                DifferentialDimension.TERMINAL_METRICS,
                DifferentialStatus.EXPECTED_UNKNOWN,
                "ts-legacy fixture contract does not capture terminal metrics",
            )
        return DifferentialOutcome(
            None,
            DifferentialDimension.TERMINAL_METRICS,
            DifferentialStatus.INCONCLUSIVE,
            "ts-legacy side does not capture terminal metrics",
        )
    if py_metrics is None:
        return DifferentialOutcome(
            None,
            DifferentialDimension.TERMINAL_METRICS,
            DifferentialStatus.INCONCLUSIVE,
            "python-agent side does not capture terminal metrics",
        )
    ts_digest = content_sha256(to_json_value(dict(ts_metrics)))
    py_digest = content_sha256(to_json_value(dict(py_metrics)))
    if ts_digest == py_digest:
        return DifferentialOutcome(
            None,
            DifferentialDimension.TERMINAL_METRICS,
            DifferentialStatus.MATCH,
            "terminal metrics agree",
        )
    return DifferentialOutcome(
        None,
        DifferentialDimension.TERMINAL_METRICS,
        DifferentialStatus.MISMATCH,
        "terminal metrics differ",
    )


def _load_manifest(
    manifest_path: str | Path,
) -> tuple[dict[str, JsonValue], list[Mapping[str, object]]]:
    path = Path(manifest_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DifferentialError(f"cannot read ts-legacy manifest {path.name}: {exc}") from exc
    manifest = _strict_object(raw, "manifest")
    if _strict_int(manifest.get("protocol_version"), "protocol_version") != 1:
        raise DifferentialError("unsupported ts-legacy manifest protocol_version")
    dataset_id = _strict_str(manifest.get("dataset_id"), "dataset_id")
    if not _DATASET_ID.fullmatch(dataset_id):
        raise DifferentialError("manifest dataset_id is not a portable identifier")
    map_mode = _strict_str(manifest.get("map_mode"), "map_mode")
    if map_mode not in _MAP_MODES:
        raise DifferentialError(f"manifest has unsupported map_mode {map_mode!r}")
    config_hash = _sha256_ref(manifest.get("config_hash"), "config_hash")
    segments = _strict_list(manifest.get("segments"), "segments")
    if not segments:
        raise DifferentialError("manifest has no segments")
    parsed_segments: list[Mapping[str, object]] = []
    for index, segment in enumerate(segments):
        seg = _strict_object(segment, f"segments[{index}]")
        segment_id = _strict_str(seg.get("segment_id"), f"segments[{index}].segment_id")
        if not _SEGMENT_ID.fullmatch(segment_id):
            raise DifferentialError(f"segments[{index}].segment_id is not portable")
        ticks = [
            _strict_int(item, f"segments[{index}].ticks")
            for item in _strict_list(seg.get("ticks"), f"segments[{index}].ticks")
        ]
        parsed_segments.append({"segment_id": segment_id, "ticks": ticks})
    inputs = _strict_object(manifest.get("inputs"), "inputs")
    meta = _json_object(
        {
            "dataset_id": dataset_id,
            "map_mode": map_mode,
            "config_hash": config_hash,
            "decision_config": to_json_value(
                _strict_object(manifest.get("decision_config"), "decision_config")
            ),
            "inputs": to_json_value(inputs),
        }
    )
    return meta, parsed_segments


def load_ts_legacy_corpus(
    manifest_path: str | Path,
    data_dir: str | Path,
) -> tuple[tuple[TsLegacyCanonicalRecord, ...], Mapping[str, JsonValue]]:
    """Load a bounded, sanitized TS-legacy differential corpus.

    Records are read in manifest segment order and validated against the
    manifest's content digests; a missing file, a torn JSON tail, or a digest
    mismatch fails closed.
    """
    meta, segments = _load_manifest(manifest_path)
    data_root = Path(data_dir)
    declared_inputs = _strict_object(meta.get("inputs"), "manifest.inputs")
    records: list[TsLegacyCanonicalRecord] = []
    for segment in segments:
        segment_id = str(segment["segment_id"])
        for tick in _strict_list(segment.get("ticks"), "segment ticks"):
            tick_value = _strict_int(tick, "tick")
            raw_path = data_root / f"{tick_value}.json"
            try:
                raw_bytes = raw_path.read_bytes()
                raw = json.loads(raw_bytes.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise DifferentialError(f"cannot read ts-legacy tick {tick_value}: {exc}") from exc
            expected = content_sha256(raw_bytes)
            input_ref = f"sha256:{expected}"
            declared = _strict_object(
                declared_inputs.get(str(tick_value)), f"manifest.inputs[{tick_value}]"
            )
            declared_sha = _sha256_ref(
                declared.get("sha256"), f"manifest.inputs[{tick_value}].sha256"
            )
            if declared_sha != input_ref:
                raise DifferentialError(
                    f"manifest input digest for tick {tick_value} does not match the fixture file"
                )
            records.append(
                canonicalize_ts_legacy_record(
                    _strict_object(raw, f"tick {tick_value}"),
                    tick=tick_value,
                    segment_id=segment_id,
                    dataset_id=str(meta["dataset_id"]),
                    config_hash=str(meta["config_hash"]),
                    map_mode=str(meta["map_mode"]),
                    input_sha256=input_ref,
                )
            )
    return tuple(records), meta


def load_py_agent_corpus(
    records_path: str | Path,
    *,
    tenant_id: str,
) -> tuple[tuple[PyAgentCanonicalRecord, ...], Mapping[str, JsonValue] | None]:
    """Load the Python-agent side through the versioned offline importer."""
    evidence = import_agent_run(records_path, tenant_id=tenant_id)
    records = tuple(canonicalize_py_agent_record(tick) for tick in evidence.ticks)
    metrics = py_agent_loop_metrics(evidence.loop)
    return records, metrics


def build_differential_run(
    *,
    ts_manifest_path: str | Path,
    ts_data_dir: str | Path,
    py_records_path: str | Path,
    dataset_id: str,
    tenant_id: str,
    ts_metrics: Mapping[str, JsonValue] | None = None,
    expected_unknown: Mapping[DifferentialDimension, str] = DEFAULT_EXPECTED_UNKNOWN,
) -> DifferentialReport:
    """Load both sides and produce one content-addressed differential report."""
    ts_records, _ = load_ts_legacy_corpus(ts_manifest_path, ts_data_dir)
    py_records, py_metrics = load_py_agent_corpus(py_records_path, tenant_id=tenant_id)
    return classify_differential_run(
        ts_records=ts_records,
        py_records=py_records,
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        ts_metrics=ts_metrics,
        py_metrics=py_metrics,
        expected_unknown=expected_unknown,
    )


def run_differential_from_manifest(manifest_path: str | Path) -> DifferentialReport:
    """Load a differential run manifest and produce the content-addressed report.

    The manifest binds the two fixture sides, the run identity, and the
    per-dimension expected-unknown contract. Paths inside the manifest are
    resolved relative to the manifest file.
    """
    path = Path(manifest_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DifferentialError(f"cannot read run manifest {path.name}: {exc}") from exc
    run = _strict_object(raw, "run manifest")
    if run.get("schemaVersion") != REPLAY_DIFFERENTIAL_SCHEMA:
        raise DifferentialError(
            f"unsupported run manifest schemaVersion {run.get('schemaVersion')!r}"
        )
    dataset_id = _strict_str(run.get("dataset_id"), "dataset_id")
    tenant_id = _strict_str(run.get("tenant_id"), "tenant_id")
    expected_unknown: dict[DifferentialDimension, str] = {}
    for key, side in _strict_object(run.get("expected_unknown"), "expected_unknown").items():
        try:
            dimension = DifferentialDimension(str(key))
        except ValueError as exc:
            raise DifferentialError(f"unknown dimension in expected_unknown: {key!r}") from exc
        if side not in ("ts_legacy", "python_agent"):
            raise DifferentialError(f"expected_unknown[{key}] has invalid side {side!r}")
        expected_unknown[dimension] = str(side)
    ts = _strict_object(run.get("ts_legacy"), "ts_legacy")
    py = _strict_object(run.get("python_agent"), "python_agent")
    base = path.parent
    return build_differential_run(
        ts_manifest_path=base / _strict_str(ts.get("manifest"), "ts_legacy.manifest"),
        ts_data_dir=base / _strict_str(ts.get("data_dir"), "ts_legacy.data_dir"),
        py_records_path=base / _strict_str(py.get("records"), "python_agent.records"),
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        expected_unknown=expected_unknown,
    )


__all__ = [
    "DEFAULT_EXPECTED_UNKNOWN",
    "DIFFERENTIAL_SCHEMA_VERSION",
    "GENERATOR_VERSION",
    "PY_AGENT_PROTOCOL",
    "REPLAY_DIFFERENTIAL_SCHEMA",
    "TS_LEGACY_PROTOCOL",
    "DifferentialDimension",
    "DifferentialError",
    "DifferentialOutcome",
    "DifferentialReport",
    "DifferentialStatus",
    "PyAgentCanonicalRecord",
    "TsLegacyCanonicalRecord",
    "build_differential_run",
    "canonicalize_py_agent_record",
    "canonicalize_ts_legacy_record",
    "classify_differential_run",
    "load_py_agent_corpus",
    "load_ts_legacy_corpus",
    "py_agent_loop_metrics",
    "run_differential_from_manifest",
    "world_state_digest",
]
