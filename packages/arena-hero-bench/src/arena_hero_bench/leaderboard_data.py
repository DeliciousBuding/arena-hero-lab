"""Official Python output chain for leaderboard-consumed benchmark data.

The leaderboard web app (``apps/leaderboard-web``) consumes a deterministic JSON
document derived from an ``arena.bench.report.v3`` Agent report by the legacy
TypeScript conversion ``apps/leaderboard-web/scripts/convert.mts`` (written into
``apps/leaderboard-web/src/data/bench.json``).  This module is the official
Python producer for that same structure.

``produce_leaderboard_data`` is a pure data transformation: it validates the
report fail-closed (empty or structurally incomplete reports raise instead of
yielding partial leaderboard data) and reuses the parsing/aggregation logic of
:func:`arena_hero_bench.converter.transform_report` rather than re-implementing
it.  Callers own all file IO (see :func:`arena_hero_bench.converter.convert_file`
for the file-based path).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from arena_hero_bench.converter import REPORT_SCHEMA, transform_report

#: Top-level fields of the leaderboard-consumed document (``BenchmarkData``).
LEADERBOARD_DATA_FIELDS: tuple[str, ...] = (
    "schema",
    "generatedAt",
    "convertedAt",
    "source",
    "params",
    "contestants",
    "leaderboard",
    "scenarios",
    "entryScenarioStats",
    "scenarioOrder",
)


def _require_non_empty_array(raw: Mapping[str, Any], field: str) -> list[Any]:
    value = raw.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"report field {field!r} must be a non-empty array")
    return value


def produce_leaderboard_data(
    raw: Mapping[str, Any],
    *,
    source_label: str,
    converted_at: str | None = None,
) -> dict[str, Any]:
    """Produce the leaderboard-consumed JSON structure from an Agent report.

    Args:
        raw: parsed ``arena.bench.report.v3`` report mapping.
        source_label: stable label for the report source (same semantics as
            :func:`arena_hero_bench.converter.transform_report`).
        converted_at: optional explicit conversion timestamp; defaults to the
            report's ``generatedAt``.

    Returns:
        The leaderboard web data document, structurally identical to the legacy
        TypeScript conversion output.

    Raises:
        TypeError: ``raw`` is not a mapping.
        ValueError: the report is missing, empty, or structurally incomplete in
            any section the leaderboard consumes (fail-closed).
    """
    if not isinstance(raw, Mapping):
        raise TypeError(f"report must be a mapping, got {type(raw).__name__}")
    if raw.get("schema") != REPORT_SCHEMA:
        raise ValueError(f"unexpected schema: {raw.get('schema')!r} (expected {REPORT_SCHEMA})")

    _require_non_empty_array(raw, "contestants")
    _require_non_empty_array(raw, "scenarios")
    leaderboard = raw.get("leaderboard")
    leaderboard_control = raw.get("leaderboardControl")
    if not (
        (isinstance(leaderboard, list) and bool(leaderboard))
        or (isinstance(leaderboard_control, list) and bool(leaderboard_control))
    ):
        raise ValueError(
            "report fields 'leaderboard'/'leaderboardControl' must contain at least one non-empty array"
        )

    generated_at = raw.get("generatedAt")
    if not isinstance(generated_at, str) or not generated_at:
        raise ValueError("report field 'generatedAt' must be a non-empty string")
    params = raw.get("params")
    if not isinstance(params, Mapping) or not params:
        raise ValueError("report field 'params' must be a non-empty object")

    return transform_report(raw, source_label=source_label, converted_at=converted_at)
