"""Versioned importer for offline agent runtime records (P6-1).

The lab consumes the stable public JSONL records and optional health snapshot
written by the offline agent CLI instead of importing the agent package. Only
public wire fields are read; the importer fails closed on unsupported schema
versions, unknown record types, torn tails, corrupt lines, duplicate ticks,
duplicate loop summaries, empty inputs, and tenant mismatches, then converts
the records into a deterministic, content-addressed lab artifact.

The converted artifact keeps the agent's stable outcome vocabulary
(``candidate`` / ``soft_deadline`` / ``selection_timeout`` / ``not_applicable``
/ ``error`` and ``accepted`` / ``rejected`` / ``not_submitted``) under lab
snake_case keys. Provenance records only the public agent commit, the public
SDK tag, and the record schema version; it never carries local paths or
runtime-local metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

AGENT_RECORD_SCHEMA_VERSION: Final = 1
HEALTH_SCHEMA_VERSION: Final = 1

AGENT_RUN_EVIDENCE_SCHEMA: Final = "arena.bench.agent-run.v1"
AGENT_RUN_IMPORT_REPORT_SCHEMA: Final = "arena.bench.agent-run-import-report.v1"
GENERATOR_VERSION: Final = "0.2.0"

DEFAULT_AGENT_COMMIT: Final = "568ebf2"
DEFAULT_SDK_TAG: Final = "v0.3.0a1"

RECORD_TYPE_TICK: Final = "tick"
RECORD_TYPE_LOOP: Final = "loop"

_DEADLINE_OUTCOMES: Final = frozenset(
    {"candidate", "soft_deadline", "selection_timeout", "not_applicable", "error"}
)
_SUBMIT_RESULTS: Final = frozenset({"accepted", "rejected", "not_submitted"})
_STOPPED_REASONS: Final = frozenset(
    {"stream_ended", "soft_deadline", "selection_timeout", "gap", "submit_failure"}
)

_TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PUBLIC_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+=-]{0,127}$")


class AgentRuntimeImportError(ValueError):
    """The agent run records or health snapshot cannot be imported."""


def _public_identifier(value: str, field_name: str) -> str:
    if not _PUBLIC_TEXT.fullmatch(value):
        raise AgentRuntimeImportError(
            f"{field_name} must be a portable identifier without paths or spaces"
        )
    return value


def _strict_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentRuntimeImportError(f"{field_name} must be an integer")
    return value


def _strict_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise AgentRuntimeImportError(f"{field_name} must be a boolean")
    return value


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AgentRuntimeImportError(f"{field_name} must be a non-empty string")
    return value


def _strict_enum(value: object, field_name: str, allowed: frozenset[str]) -> str:
    text = _strict_str(value, field_name)
    if text not in allowed:
        raise AgentRuntimeImportError(f"{field_name} has an unsupported value {text!r}")
    return text


def _strict_optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _strict_str(value, field_name)


@dataclass(frozen=True, slots=True)
class AgentTickRecord:
    """One validated tick record from the agent runtime records JSONL."""

    tenant_id: str
    recorded_at_ns: int
    tick: int
    decision_id: str
    deadline_outcome: str
    submit_result: str
    submit_error: str | None


@dataclass(frozen=True, slots=True)
class AgentLoopRecord:
    """One validated tick-loop summary record from the agent runtime records JSONL."""

    tenant_id: str
    recorded_at_ns: int
    last_tick: int
    ticks_processed: int
    duplicate_ticks: int
    out_of_order_ticks: int
    gap_ticks: int
    reconnect_count: int
    stopped_reason: str
    outcome_count: int


def _require_common(data: Mapping[str, object], tenant_id: str) -> None:
    schema_version = data.get("schemaVersion")
    if schema_version != AGENT_RECORD_SCHEMA_VERSION:
        raise AgentRuntimeImportError(
            "unsupported agent record schemaVersion "
            f"{schema_version!r}; expected {AGENT_RECORD_SCHEMA_VERSION}"
        )
    actual_tenant = data.get("tenantId")
    if actual_tenant != tenant_id:
        raise AgentRuntimeImportError(
            f"agent record tenantId {actual_tenant!r} does not match expected {tenant_id!r}"
        )


def _parse_tick(data: Mapping[str, object], tenant_id: str) -> AgentTickRecord:
    _require_common(data, tenant_id)
    decision_id = _strict_str(data.get("decisionId"), "decisionId")
    if not _OPAQUE_ID.fullmatch(decision_id):
        raise AgentRuntimeImportError("decisionId is not in canonical form")
    return AgentTickRecord(
        tenant_id=tenant_id,
        recorded_at_ns=_strict_int(data.get("recordedAtNs"), "recordedAtNs"),
        tick=_strict_int(data.get("tick"), "tick"),
        decision_id=decision_id,
        deadline_outcome=_strict_enum(
            data.get("deadlineOutcome"), "deadlineOutcome", _DEADLINE_OUTCOMES
        ),
        submit_result=_strict_enum(data.get("submitResult"), "submitResult", _SUBMIT_RESULTS),
        submit_error=_strict_optional_str(data.get("submitError"), "submitError"),
    )


def _parse_loop(data: Mapping[str, object], tenant_id: str) -> AgentLoopRecord:
    _require_common(data, tenant_id)
    return AgentLoopRecord(
        tenant_id=tenant_id,
        recorded_at_ns=_strict_int(data.get("recordedAtNs"), "recordedAtNs"),
        last_tick=_strict_int(data.get("lastTick"), "lastTick"),
        ticks_processed=_strict_int(data.get("ticksProcessed"), "ticksProcessed"),
        duplicate_ticks=_strict_int(data.get("duplicateTicks"), "duplicateTicks"),
        out_of_order_ticks=_strict_int(data.get("outOfOrderTicks"), "outOfOrderTicks"),
        gap_ticks=_strict_int(data.get("gapTicks"), "gapTicks"),
        reconnect_count=_strict_int(data.get("reconnectCount"), "reconnectCount"),
        stopped_reason=_strict_enum(data.get("stoppedReason"), "stoppedReason", _STOPPED_REASONS),
        outcome_count=_strict_int(data.get("outcomeCount"), "outcomeCount"),
    )


def _load_records(
    path: Path, tenant_id: str
) -> tuple[tuple[AgentTickRecord, ...], AgentLoopRecord | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentRuntimeImportError(f"agent run records could not be read: {exc}") from exc
    if not text:
        raise AgentRuntimeImportError("agent run records file is empty")
    if not text.endswith("\n"):
        raise AgentRuntimeImportError(
            "agent run records file has a torn tail (missing final newline)"
        )
    ticks: list[AgentTickRecord] = []
    seen_ticks: set[int] = set()
    loop: AgentLoopRecord | None = None
    for line_number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentRuntimeImportError(
                f"corrupt agent run record at line {line_number}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise AgentRuntimeImportError(
                f"agent run record at line {line_number} must be a JSON object"
            )
        record_type = data.get("recordType")
        if record_type == RECORD_TYPE_TICK:
            tick = _parse_tick(data, tenant_id)
            if tick.tick in seen_ticks:
                raise AgentRuntimeImportError(f"duplicate agent tick record for tick {tick.tick}")
            seen_ticks.add(tick.tick)
            ticks.append(tick)
        elif record_type == RECORD_TYPE_LOOP:
            parsed_loop = _parse_loop(data, tenant_id)
            if loop is not None:
                raise AgentRuntimeImportError(
                    "agent run records contain more than one loop summary"
                )
            loop = parsed_loop
        else:
            raise AgentRuntimeImportError(f"unknown agent run recordType {record_type!r}")
    if not ticks and loop is None:
        raise AgentRuntimeImportError("agent run records file contains no records")
    return tuple(ticks), loop


def _load_health(path: Path, tenant_id: str) -> Mapping[str, JsonValue]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentRuntimeImportError(f"agent health snapshot could not be read: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentRuntimeImportError(f"agent health snapshot is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentRuntimeImportError("agent health snapshot must be a JSON object")
    if data.get("schemaVersion") != HEALTH_SCHEMA_VERSION:
        raise AgentRuntimeImportError("agent health snapshot has an unsupported schemaVersion")
    actual_tenant = data.get("tenantId")
    if actual_tenant != tenant_id:
        raise AgentRuntimeImportError(
            f"agent health tenantId {actual_tenant!r} does not match expected {tenant_id!r}"
        )
    _strict_bool(data.get("ready"), "ready")
    _strict_bool(data.get("completed"), "completed")
    _strict_str(data.get("status"), "status")
    converted = to_json_value(data)
    if not isinstance(converted, dict):
        raise AgentRuntimeImportError("agent health snapshot is not a canonical JSON object")
    return converted


def _tick_to_json(tick: AgentTickRecord) -> dict[str, JsonValue]:
    return {
        "tick": tick.tick,
        "decision_id": tick.decision_id,
        "deadline_outcome": tick.deadline_outcome,
        "submit_result": tick.submit_result,
        "submit_error": tick.submit_error,
        "recorded_at_ns": tick.recorded_at_ns,
    }


def _loop_to_json(loop: AgentLoopRecord) -> dict[str, JsonValue]:
    return {
        "last_tick": loop.last_tick,
        "ticks_processed": loop.ticks_processed,
        "duplicate_ticks": loop.duplicate_ticks,
        "out_of_order_ticks": loop.out_of_order_ticks,
        "gap_ticks": loop.gap_ticks,
        "reconnect_count": loop.reconnect_count,
        "stopped_reason": loop.stopped_reason,
        "outcome_count": loop.outcome_count,
        "recorded_at_ns": loop.recorded_at_ns,
    }


def _loop_report(loop: AgentLoopRecord) -> dict[str, JsonValue]:
    return {
        "last_tick": loop.last_tick,
        "ticks_processed": loop.ticks_processed,
        "stopped_reason": loop.stopped_reason,
    }


def _health_report(health: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "ready": _strict_bool(health.get("ready"), "ready"),
        "completed": _strict_bool(health.get("completed"), "completed"),
        "status": _strict_str(health.get("status"), "status"),
    }


@dataclass(frozen=True, slots=True)
class AgentRunEvidence:
    """Imported agent run: parsed records plus the content-addressed artifact."""

    tenant_id: str
    ticks: tuple[AgentTickRecord, ...]
    loop: AgentLoopRecord | None
    health: Mapping[str, JsonValue] | None
    provenance: Mapping[str, str]
    content: Mapping[str, JsonValue]
    content_sha256: str
    report: Mapping[str, JsonValue]


def import_agent_run(
    records_path: str | Path,
    *,
    tenant_id: str,
    health_path: str | Path | None = None,
    agent_commit: str = DEFAULT_AGENT_COMMIT,
    sdk_tag: str = DEFAULT_SDK_TAG,
) -> AgentRunEvidence:
    """Import one offline agent run into a content-addressed lab artifact.

    The artifact content is derived only from the input records, the optional
    health snapshot, and the fixed provenance fields, so re-importing the same
    run yields the same digest. No network, API key, or agent package is used.
    """
    if not _TENANT_ID.fullmatch(tenant_id):
        raise AgentRuntimeImportError("tenant_id must be a lowercase portable identifier")
    provenance = MappingProxyType(
        {
            "agent_commit": _public_identifier(agent_commit, "agent_commit"),
            "sdk_tag": _public_identifier(sdk_tag, "sdk_tag"),
            "schema_version": str(AGENT_RECORD_SCHEMA_VERSION),
        }
    )
    ticks, loop = _load_records(Path(records_path), tenant_id)
    health = _load_health(Path(health_path), tenant_id) if health_path is not None else None
    ordered_ticks = tuple(sorted(ticks, key=lambda item: item.tick))
    content_value = to_json_value(
        {
            "schema_version": AGENT_RUN_EVIDENCE_SCHEMA,
            "tenant_id": tenant_id,
            "provenance": dict(provenance),
            "ticks": [_tick_to_json(tick) for tick in ordered_ticks],
            "loop": _loop_to_json(loop) if loop is not None else None,
            "health": dict(health) if health is not None else None,
        }
    )
    if not isinstance(content_value, dict):
        raise AssertionError("agent run evidence content is not a JSON object")
    digest = content_sha256(content_value)
    report_value = to_json_value(
        {
            "schema_version": AGENT_RUN_IMPORT_REPORT_SCHEMA,
            "tenant_id": tenant_id,
            "artifact_sha256": digest,
            "tick_count": len(ordered_ticks),
            "loop": _loop_report(loop) if loop is not None else None,
            "health": _health_report(health) if health is not None else None,
        }
    )
    if not isinstance(report_value, dict):
        raise AssertionError("agent run import report is not a JSON object")
    return AgentRunEvidence(
        tenant_id=tenant_id,
        ticks=ordered_ticks,
        loop=loop,
        health=health,
        provenance=provenance,
        content=content_value,
        content_sha256=digest,
        report=report_value,
    )


def source_build_sha256(records_path: str | Path, health_path: str | Path | None = None) -> str:
    """Content-address the raw input files used to construct an imported run.

    This is the manifest ``source_build_sha256``: a digest of the input bytes,
    not a Git commit identifier.
    """
    records_digest = hashlib.sha256(Path(records_path).read_bytes()).hexdigest()
    health_digest = (
        hashlib.sha256(Path(health_path).read_bytes()).hexdigest()
        if health_path is not None
        else None
    )
    return content_sha256({"records": records_digest, "health": health_digest})


__all__ = [
    "AGENT_RECORD_SCHEMA_VERSION",
    "AGENT_RUN_EVIDENCE_SCHEMA",
    "AGENT_RUN_IMPORT_REPORT_SCHEMA",
    "DEFAULT_AGENT_COMMIT",
    "DEFAULT_SDK_TAG",
    "GENERATOR_VERSION",
    "HEALTH_SCHEMA_VERSION",
    "AgentLoopRecord",
    "AgentRunEvidence",
    "AgentRuntimeImportError",
    "AgentTickRecord",
    "import_agent_run",
    "source_build_sha256",
]
