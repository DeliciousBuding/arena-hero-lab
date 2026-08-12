"""Free-for-all melee ranking: manifest, runner, fail-closed checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arena_hero_bench.melee import (
    MeleeManifestError,
    load_melee_manifest,
    run_melee_from_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures" / "melee"
MANIFEST = FIXTURES / "run-melee-v1.json"


def _write(tmp_path: Path, raw: dict[str, Any]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _manifest_dict(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if overrides:
        for key, value in overrides.items():
            raw[key] = value
    return raw


def test_smoke_ranks_contestants_by_survival_then_terminal_score() -> None:
    report = run_melee_from_manifest(MANIFEST)
    ordered = [(entry.rank, entry.contestant_id) for entry in report.placements]
    assert ordered == [(1, "melee-champion"), (2, "melee-mid"), (3, "melee-dead")]
    by_contestant = {entry.contestant_id: entry for entry in report.placements}
    assert by_contestant["melee-champion"].survival_alive is True
    assert by_contestant["melee-mid"].survival_alive is True
    assert by_contestant["melee-dead"].survival_alive is False
    assert (
        by_contestant["melee-champion"].aggregate_score
        > by_contestant["melee-mid"].aggregate_score
    )


def test_report_is_deterministic() -> None:
    first = run_melee_from_manifest(MANIFEST)
    second = run_melee_from_manifest(MANIFEST)
    assert first.artifact_sha256 == second.artifact_sha256


def test_manifest_fails_closed_on_unknown_schema(tmp_path: Path) -> None:
    raw = _manifest_dict({"schemaVersion": "arena.bench.melee.v99"})
    with pytest.raises(MeleeManifestError):
        load_melee_manifest(_write(tmp_path, raw))


def test_manifest_requires_at_least_two_contestants(tmp_path: Path) -> None:
    raw = _manifest_dict()
    raw["contestants"] = raw["contestants"][:1]
    with pytest.raises(MeleeManifestError):
        load_melee_manifest(_write(tmp_path, raw))


def test_manifest_requires_observation_snapshots(tmp_path: Path) -> None:
    raw = _manifest_dict()
    del raw["contestants"][0]["observation_snapshots"]
    with pytest.raises(MeleeManifestError):
        load_melee_manifest(_write(tmp_path, raw))
