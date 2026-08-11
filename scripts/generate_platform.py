"""Generate the deterministic platform status document for the public site.

Usage:
    uv run python scripts/generate_platform.py \
        --output apps/leaderboard-web/src/data/platform.json

The document is recomputed from real code on every run: the Python agent
known-answer fixture is verified, the simulator differential is re-executed,
and the research evidence chain is recomputed and round-tripped through its
ledger. The output is deterministic and never contains competitive rankings.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from arena_hero_research.platform_status import (
    PLATFORM_STATUS_SCHEMA,
    PlatformStatusError,
    generate_platform_status,
)

DEFAULT_OUTPUT = Path("apps/leaderboard-web/src/data/platform.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate arena.platform.status.v2")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="destination for the generated platform status JSON",
    )
    parser.add_argument(
        "--agent-fixture-dir",
        type=Path,
        default=None,
        help="override the external agent fixture directory (for tests)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=9, help="simulator differential batch size"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        platform = generate_platform_status(
            args.output,
            agent_fixture_dir=args.agent_fixture_dir,
            batch_size=args.batch_size,
        )
    except PlatformStatusError as exc:
        raise SystemExit(f"platform status generation failed: {exc}") from exc
    print(
        f"[platform-status] {args.output} ({PLATFORM_STATUS_SCHEMA}, "
        f"agent={platform['agent']['status']}, "
        f"simulator={platform['simulator']['status']}, "
        f"research={platform['research']['status']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
