"""Command-line entry point for Arena Hero benchmark tooling."""

from __future__ import annotations

import argparse
from pathlib import Path

from arena_hero_bench.converter import convert_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arena-hero-bench")
    subparsers = parser.add_subparsers(dest="command", required=True)
    convert = subparsers.add_parser("convert", help="convert a v3 report for leaderboard-web")
    convert.add_argument("source", type=Path)
    convert.add_argument("--output", required=True, type=Path)
    convert.add_argument("--source-root", type=Path)
    convert.add_argument("--source-label")
    convert.add_argument("--converted-at")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "convert":
        output = convert_file(
            args.source,
            args.output,
            source_root=args.source_root,
            source_label=args.source_label,
            converted_at=args.converted_at,
        )
        print(
            f"[convert] {args.source} -> {args.output} "
            f"({len(output['leaderboard'])} entries, {len(output['scenarios'])} scenarios)"
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
