"""Batch state-seed replay across multiple records of one production JSONL log.

Reuses the shared parsing/replay functions from replay_state_seed.py, prints the
full transcript per index, and finishes with a stall aggregation.  Note this
script is imported as a sibling module of replay_state_seed.py inside the
scripts directory.

Usage (from the arena-hero-lab repo root):
    uv run python scripts/bench_state_seeds.py --jsonl <path> --indices 1,50,100 --ticks 300
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from replay_state_seed import (
    load_jsonl_records,
    parse_tick_state,
    run_state_seed_replay,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", required=True, type=Path, help="production JSONL log path")
    parser.add_argument(
        "--indices",
        required=True,
        help="comma-separated 0-based record indices, e.g. 1,50,100",
    )
    parser.add_argument("--ticks", type=int, default=300)
    parser.add_argument("--seed", type=int, default=0, help="simulator seed (uid/replenish rng)")
    parser.add_argument("--stall-ticks", type=int, default=100)
    args = parser.parse_args()

    try:
        indices = [int(part.strip()) for part in args.indices.split(",") if part.strip()]
    except ValueError:
        print(f"indices {args.indices!r} must be comma-separated integers", file=sys.stderr)
        return 2

    records = load_jsonl_records(args.jsonl)
    stall_flags: dict[int, bool] = {}
    for index in indices:
        if index < 0 or index >= len(records):
            print(f"index {index} out of range: {len(records)} record(s) in {args.jsonl}")
            return 2
        parsed = parse_tick_state(records[index])
        result = run_state_seed_replay(
            parsed,
            ticks=args.ticks,
            sim_seed=args.seed,
            stall_ticks=args.stall_ticks,
            record_index=index,
        )
        stall_flags[index] = result.stalled
        for line in result.lines:
            print(f"[{index}] {line}")

    stalls = [index for index, stalled in stall_flags.items() if stalled]
    print("")
    print(f"batch summary: {len(indices)} seed(s), {len(stalls)} stall(s) ({stalls or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
