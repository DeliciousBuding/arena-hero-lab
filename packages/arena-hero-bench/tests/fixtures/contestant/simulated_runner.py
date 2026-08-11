"""Simulated SDK entry point runner for the bench adapter smoke (P3-6).

Mirrors the CLI contract of ``arena_hero.agent.io.v1.runner`` so the bench
adapter full chain can run without the SDK installed in the bench environment:

- ``--mode ok`` prints ``status=ok digest=<sha256>`` to stdout and exits 0.
- ``--mode timeout|crash|protocol|error`` prints ``status=<mode> error=...``
  on stderr and exits 1 (the SDK runner reports a fail-closed round).
- ``--mode hang`` sleeps past the outer executor deadline so the process
  executor isolates the timeout by reaping the whole tree.
- ``--mode hard_exit`` calls ``os._exit(3)`` so the process executor observes
  a non-zero worker exit code.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

_MODES = ("ok", "timeout", "crash", "protocol", "error", "hang", "hard_exit")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="simulated_runner")
    parser.add_argument("--mode", choices=_MODES, default="ok")
    parser.add_argument("--sleep", type=float, default=5.0)
    args = parser.parse_args(argv)
    if args.mode == "hang":
        time.sleep(args.sleep)
        return 0
    if args.mode == "hard_exit":
        os._exit(3)
    if args.mode == "ok":
        digest = hashlib.sha256(b"arena.contestant.smoke").hexdigest()
        print(f"status=ok digest={digest}")
        return 0
    print(f"status={args.mode} error=simulated {args.mode} round", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
