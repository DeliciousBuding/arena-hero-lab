from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src" / "arena_hero_research"
EXECUTION_MODULES = {
    "assignment.py",
    "conclusion.py",
    "execution.py",
    "ledger.py",
    "lifecycle.py",
    "planning.py",
    "replication.py",
    "runner.py",
}
FORBIDDEN_PREFIXES = (
    "aiohttp",
    "http.client",
    "httpx",
    "numpy",
    "pandas",
    "requests",
    "scipy",
    "socket",
    "subprocess",
    "urllib",
    "arena_hero_sim.backend",
    "arena_hero_sim.reference",
    "arena_hero_sim.registry",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_research_execution_has_no_network_heavy_science_or_concrete_simulator_imports() -> None:
    violations: list[str] = []
    for filename in sorted(EXECUTION_MODULES):
        path = SOURCE_ROOT / filename
        for module in _imports(path):
            if module.startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{filename} imports {module}")
    assert violations == []
