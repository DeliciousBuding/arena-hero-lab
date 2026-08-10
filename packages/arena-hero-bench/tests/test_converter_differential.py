from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from arena_hero_bench.converter import convert_file

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
WEB_APP = WORKSPACE_ROOT / "apps" / "leaderboard-web"
FIXTURE = WEB_APP / "scripts" / "input" / "results.json"
FIXED_TIME = "2026-01-01T00:00:00Z"
FIXED_SOURCE = "fixtures/benchmark-v3"


def test_python_converter_matches_typescript_oracle(tmp_path: Path) -> None:
    python_output = tmp_path / "python.json"
    typescript_output = tmp_path / "typescript.json"

    convert_file(
        FIXTURE,
        python_output,
        source_label=FIXED_SOURCE,
        converted_at=FIXED_TIME,
    )

    pnpm = shutil.which("pnpm")
    assert pnpm is not None, "pnpm is required for the TypeScript oracle"
    completed = subprocess.run(
        [
            pnpm,
            "--dir",
            str(WEB_APP),
            "exec",
            "tsx",
            "scripts/convert.mts",
            f"--source={FIXTURE}",
            f"--output={typescript_output}",
            f"--converted-at={FIXED_TIME}",
            f"--source-label={FIXED_SOURCE}",
        ],
        cwd=WORKSPACE_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    python_data = json.loads(python_output.read_text(encoding="utf-8"))
    typescript_data = json.loads(typescript_output.read_text(encoding="utf-8"))
    assert python_data == typescript_data


def test_converter_is_deterministic_for_fixed_input(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    convert_file(FIXTURE, first, source_label=FIXED_SOURCE)
    convert_file(FIXTURE, second, source_label=FIXED_SOURCE)

    assert first.read_bytes() == second.read_bytes()


def test_public_config_notes_match_fixture_values(tmp_path: Path) -> None:
    output = tmp_path / "converted.json"
    convert_file(FIXTURE, output, source_label=FIXED_SOURCE, converted_at=FIXED_TIME)
    data = json.loads(output.read_text(encoding="utf-8"))
    contestants = {item["id"]: item for item in data["contestants"]}

    assert "worker_target=8" in contestants["farmer-eco"]["configNote"]
    assert "worker_target=16" not in contestants["farmer-eco"]["configNote"]
    assert "mode=harvest" in contestants["core-mil"]["configNote"]
    assert "target=20" in contestants["core-mil"]["configNote"]
    assert "mode=control" not in contestants["core-mil"]["configNote"]
