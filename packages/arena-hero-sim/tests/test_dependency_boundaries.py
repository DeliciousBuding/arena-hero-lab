from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src" / "arena_hero_sim"
FORBIDDEN_PREFIXES = (
    "arena_hero_bench",
    "arena_hero_research",
    "arena_hero_agent",
    "pandas",
    "scipy",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_simulator_has_no_reverse_or_research_dependencies() -> None:
    violations: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        for module in imported_modules(path):
            if module.startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} imports {module}")

    assert violations == []
