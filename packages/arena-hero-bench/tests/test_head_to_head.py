"""Head-to-head terminal outcome comparator: manifest, runner, fail-closed checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arena_hero_bench.head_to_head import (
    HeadToHeadManifestError,
    load_head_to_head_manifest,
    run_head_to_head_from_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures" / "head_to_head"
MANIFEST = FIXTURES / "run-head-to-head-v1.json"


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


def test_smoke_reports_terminal_win_and_loss() -> None:
    report = run_head_to_head_from_manifest(MANIFEST)
    by_contestant = {entry.contestant_id: entry for entry in report.contestants}
    assert set(by_contestant) == {"python-beats-evolve", "python-dies"}
    assert by_contestant["python-beats-evolve"].aggregate_winner == "python_agent"
    assert by_contestant["python-dies"].aggregate_winner == "evolve"
    assert by_contestant["python-dies"].python_terminal.survival_alive is False
    assert by_contestant["python-beats-evolve"].python_terminal.survival_alive is True


def test_report_is_deterministic() -> None:
    first = run_head_to_head_from_manifest(MANIFEST)
    second = run_head_to_head_from_manifest(MANIFEST)
    assert first.artifact_sha256 == second.artifact_sha256


def test_manifest_fails_closed_on_unknown_schema(tmp_path: Path) -> None:
    raw = _manifest_dict({"schemaVersion": "arena.bench.head-to-head.v99"})
    with pytest.raises(HeadToHeadManifestError):
        load_head_to_head_manifest(_write(tmp_path, raw))


def test_manifest_requires_observation_snapshots(tmp_path: Path) -> None:
    raw = _manifest_dict()
    del raw["contestants"][0]["observation_snapshots"]
    with pytest.raises(HeadToHeadManifestError):
        load_head_to_head_manifest(_write(tmp_path, raw))
