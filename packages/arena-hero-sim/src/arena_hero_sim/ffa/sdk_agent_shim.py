"""Run a third-party official-SDK agent as an FFA contestant (subprocess).

The non-evolve third-party agents (drew-z / waaiging / guide / tactic / wuwd /
massarmy) are written against the official ``arena-hero`` SDK.  This module wraps
any of them as a long-lived subprocess running in that repo's own ``.venv`` (or,
when the repo has no venv, the fork's ``arena-hero-sdk-py`` venv), so the SDK
dependency never enters the Lab environment.

Per tick the parent sends :func:`arena_hero_sim.ffa.sdk_bridge.observation_to_sdk_state`
JSON over stdin; the runner constructs a real SDK ``Turn``, calls the adapter's
``run_turn(turn)``, and prints ``turn.plan`` JSON back.  The parent maps that
back with :func:`arena_hero_sim.ffa.sdk_bridge.sdk_plan_to_ffa`.

The request/response loop is deliberately kept synchronous (write one line, read
one line) inside a single persistent I/O thread per agent: the direct pipe read
is what the v2 battery proved reliable, and extra concurrency here was the
source of a hard-to-reproduce Windows deadlock.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from .observation import Observation
from .sdk_bridge import observation_to_sdk_state, sdk_plan_to_ffa

# arena-hero-lab/packages/arena-hero-sim/src/arena_hero_sim/ffa/sdk_agent_shim.py
# parents: 0=ffa 1=arena_hero_sim 2=src 3=arena-hero-sim 4=packages 5=arena-hero-lab 6=arena
_ARENA_ROOT = Path(__file__).resolve().parents[6]

# Per-turn upper bound for a SDK agent to answer before it is killed and the
# match marked as having a failed contestant. A third-party agent that spins or
# deadlocks on a particular game state would otherwise block the whole battery.
# Production requires a decision within 15s, so the benchmark enforces 14s.
_SDK_TURN_TIMEOUT_SECONDS = 14.0

# A decision that takes longer than this is logged as slow (with tick + agent),
# so the battery log shows exactly which third-party agent is pathological.
_SLOW_DECISION_THRESHOLD_SECONDS = 1.0

_RUNNER_SOURCE: Final = r"""
import importlib.util, json, sys
from datetime import datetime, timezone

agent_repo = sys.argv[1]
adapter_path = sys.argv[2]
sys.path.insert(0, agent_repo)

from arena_hero.models import PlayerState
from arena_hero.turn import Turn
from arena_hero.actions import Accepted
from arena_hero.enums import CommandSource

spec = importlib.util.spec_from_file_location("_arena_sdk_adapter", adapter_path)
adapter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_mod)
adapter = adapter_mod.make_adapter()

# The guide agent persists ".arena_core_state.json" on every ~10th tick via a
# shared temp file.  With N concurrent matches, N guide subprocesses write the
# SAME temp path -> on Windows this races (sharing violation) -> unhandled
# OSError kills the subprocess mid-match (observed at tick ~4 under 8 workers).
# The benchmark never reads that file back, so persistence is a pure side
# effect here: no-op it to keep matches clean and byte-identical.
import sys as _sys
_guide = _sys.modules.get("arena_core_agent")
if _guide is not None and hasattr(_guide, "save_state"):
    _guide.save_state = lambda state: None

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    payload = json.loads(line)
    state = PlayerState.model_validate(payload["state"])
    def submitter(plan, key):
        return Accepted(
            accepted=True,
            tick=plan.tick,
            source=CommandSource.AGENT,
            received_at=datetime.now(timezone.utc),
        )
    turn = Turn(tick=payload["tick"], state=state, submitter=submitter)
    adapter.run_turn(turn)
    plan = turn.plan
    print(json.dumps(plan.model_dump(mode="json"), sort_keys=True), flush=True)
"""


@dataclass(frozen=True, slots=True)
class SdkAgentSpec:
    """One third-party SDK agent wiring: repo + standalone adapter."""

    id: str
    repo: str
    adapter: str  # absolute-ish relative path to the adapter .py under sdk_adapters/


_ADAPTER_DIR = Path(__file__).resolve().parent / "sdk_adapters"

AGENT_SPECS: Final[dict[str, SdkAgentSpec]] = {
    "guide": SdkAgentSpec(
        id="guide",
        repo="reference/third-party/arena-hero-guide",
        adapter=str(_ADAPTER_DIR / "guide.py"),
    ),
    "drew-z": SdkAgentSpec(
        id="drew-z",
        repo="reference/third-party/arena-hero-agent",
        adapter=str(_ADAPTER_DIR / "drew_z.py"),
    ),
    "waaiging": SdkAgentSpec(
        id="waaiging",
        repo="reference/third-party/arena-hero-clone-waaiging",
        adapter=str(_ADAPTER_DIR / "waaiging.py"),
    ),
    "tactic": SdkAgentSpec(
        id="tactic",
        repo="reference/third-party/arena-hero-tactic",
        adapter=str(_ADAPTER_DIR / "tactic.py"),
    ),
    "wuwd": SdkAgentSpec(
        id="wuwd",
        repo="reference/third-party/arena-hero-agent-wuwd",
        adapter=str(_ADAPTER_DIR / "wuwd.py"),
    ),
    "massarmy": SdkAgentSpec(
        id="massarmy",
        repo="reference/third-party/arena-hero-agent-massarmy",
        adapter=str(_ADAPTER_DIR / "massarmy.py"),
    ),
}


def discover_sdk_python() -> str | None:
    """Return a python that has ``arena_hero`` importable, or None."""

    override = os.environ.get("ARENA_HERO_SDK_PYTHON")
    if override:
        return override
    for candidate in (
        _ARENA_ROOT / "arena-hero-sdk-py" / ".venv" / "Scripts" / "python.exe",
        _ARENA_ROOT / "arena-hero-sdk-py" / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


class SdkAgentStrategy:
    """A FFA contestant backed by one official-SDK third-party agent."""

    def __init__(
        self, agent_id: str = "guide", *, sdk_python: str | os.PathLike[str] | None = None
    ):
        spec = AGENT_SPECS.get(agent_id)
        if spec is None:
            raise KeyError(f"unknown SDK agent id: {agent_id!r}")
        self._agent_id = agent_id
        self._repo = str((_ARENA_ROOT / spec.repo).resolve())
        self._adapter = spec.adapter
        self._sdk_python = str(sdk_python) if sdk_python is not None else discover_sdk_python()
        self._proc: subprocess.Popen[str] | None = None
        self._stderr_lines: list[str] = []
        # Persistent request/response queue: one I/O thread per agent owns the
        # stdin write + stdout read for its whole life.  Spawning a fresh
        # thread per decision (the old design) cost ~12000 thread creations per
        # 2000-tick match; the queue keeps the same "write+read in one place"
        # discipline that avoided the v2 Windows pipe deadlock.
        self._io_queue: queue.Queue[tuple[str, dict] | None] = queue.Queue()
        self._io_thread: threading.Thread | None = None
        # Per-match decision timing, for surfacing which third-party agent is
        # pathological (slow pathfinding / spin) rather than just "the run is slow".
        self._decision_count = 0
        self._decision_total_s = 0.0
        self._decision_max_s = 0.0
        self._slow_decisions: list[tuple[int, float]] = []
        # Set once the agent is killed for exceeding the decision limit; every
        # later turn fails fast instead of re-attempting a dead subprocess.
        self._dead = False

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def decision_stats(self) -> dict[str, object]:
        """Aggregate per-agent decision timing for the match summary."""
        if self._decision_count == 0:
            return {"agent": self._agent_id, "count": 0}
        return {
            "agent": self._agent_id,
            "count": self._decision_count,
            "total_s": round(self._decision_total_s, 3),
            "avg_s": round(self._decision_total_s / self._decision_count, 4),
            "max_s": round(self._decision_max_s, 3),
            "slow_count": len(self._slow_decisions),
            "slow_tail": self._slow_decisions[-10:],
        }

    def _start_stderr_drain(self) -> None:
        """Continuously drain stderr into a bounded buffer.

        A chatty third-party agent can emit tracebacks/debug lines faster than
        the parent reads them; once the 64KB stderr pipe fills, the subprocess
        blocks on its stderr write, stops reading stdin, and the whole match
        deadlocks. Draining stderr on a background thread keeps the pipe open
        while preserving the last lines for error reporting.
        """

        def drain() -> None:
            assert self._proc is not None and self._proc.stderr is not None
            try:
                for line in self._proc.stderr:
                    self._stderr_lines.append(line)
                    if len(self._stderr_lines) > 200:
                        self._stderr_lines = self._stderr_lines[-200:]
            except (OSError, ValueError, UnicodeError):
                pass

        threading.Thread(target=drain, daemon=True).start()

    def decide(self, observation: Observation) -> dict[str, object]:
        start = time.monotonic()
        try:
            return self._decide_impl(observation)
        finally:
            elapsed = time.monotonic() - start
            self._decision_count += 1
            self._decision_total_s += elapsed
            if elapsed > self._decision_max_s:
                self._decision_max_s = elapsed
            if elapsed >= _SLOW_DECISION_THRESHOLD_SECONDS:
                self._slow_decisions.append((observation.tick, round(elapsed, 2)))
                print(
                    f"  SLOW sdk.{self._agent_id} tick={observation.tick} decision={elapsed:.2f}s",
                    flush=True,
                )

    def _decide_impl(self, observation: Observation) -> dict[str, object]:
        payload = observation_to_sdk_state(observation)
        if self._proc is None:
            if not self._sdk_python:
                raise RuntimeError(
                    "no arena_hero SDK python found; set ARENA_HERO_SDK_PYTHON or pass sdk_python="
                )
            self._proc = subprocess.Popen(
                [self._sdk_python, "-c", _RUNNER_SOURCE, self._repo, self._adapter],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                # Third-party agents can emit tracebacks in the local code page
                # (e.g. GBK on Chinese Windows); never let a stray byte kill the
                # reader.
                errors="replace",
                bufsize=1,
            )
            self._start_stderr_drain()
        if self._dead:
            raise RuntimeError(
                f"sdk agent {self._agent_id} is dead (exceeded decision limit earlier)"
            )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        # Write + read stay in the single persistent I/O thread so BOTH a
        # blocked write (the subprocess stopped reading stdin because it spun)
        # and a blocked read are caught by the same 14s decision budget.  The
        # thread is started once per agent and serves every tick through the
        # queue; only the outcome box changes per request.
        if self._io_thread is None:
            self._start_io_thread()

        payload_str = json.dumps(payload, sort_keys=True) + "\n"
        outcome: dict[str, object] = {"_event": threading.Event()}
        self._io_queue.put((payload_str, outcome))
        finished = outcome["_event"].wait(_SDK_TURN_TIMEOUT_SECONDS)
        if not finished:
            self._dead = True
            with suppress(OSError):
                self._proc.kill()
            stderr = "".join(self._stderr_lines[-20:]).strip()
            raise RuntimeError(
                f"sdk agent {self._agent_id} exceeded "
                f"{_SDK_TURN_TIMEOUT_SECONDS:.0f}s decision limit; stderr={stderr}"
            )
        if "_error" in outcome:
            raise cast(BaseException, outcome["_error"])
        return cast(dict[str, object], outcome["plan"])

    def _start_io_thread(self) -> None:
        """Serve stdin write + stdout read pairs from the request queue."""

        def loop() -> None:
            while True:
                item = self._io_queue.get()
                if item is None:
                    return
                payload_str, outcome = item
                try:
                    if self._proc is None or self._proc.stdin is None:
                        raise RuntimeError(f"sdk agent {self._agent_id} subprocess is not running")
                    self._proc.stdin.write(payload_str)
                    self._proc.stdin.flush()
                except OSError as exc:
                    # The subprocess has already exited (e.g. the agent crashed).
                    # On Windows writing to a dead pipe surfaces as OSError, not
                    # a clean readline()==''.
                    outcome["_error"] = RuntimeError(
                        f"sdk agent {self._agent_id} died mid-turn ({exc})"
                    )
                    outcome["_event"].set()
                    continue
                try:
                    if self._proc is None or self._proc.stdout is None:
                        raise RuntimeError(f"sdk agent {self._agent_id} subprocess is not running")
                    line = self._proc.stdout.readline()
                except Exception as exc:
                    outcome["_error"] = exc
                    outcome["_event"].set()
                    continue
                if not line:
                    stderr = "".join(self._stderr_lines[-20:]).strip()
                    outcome["_error"] = RuntimeError(
                        f"sdk agent subprocess exited unexpectedly: {stderr}"
                    )
                    outcome["_event"].set()
                    continue
                try:
                    outcome["plan"] = sdk_plan_to_ffa(json.loads(line))
                except Exception as exc:
                    outcome["_error"] = exc
                outcome["_event"].set()

        self._io_thread = threading.Thread(target=loop, daemon=True)
        self._io_thread.start()

    def close(self) -> None:
        stats = self.decision_stats()
        if stats.get("count"):
            print(
                f"  DECISION {self._agent_id}: count={stats['count']} "
                f"avg={stats['avg_s']}s max={stats['max_s']}s slow={stats['slow_count']}",
                flush=True,
            )
        if self._proc is None:
            return
        # Stop the persistent I/O thread before closing pipes so it never
        # touches a closed handle.
        if self._io_thread is not None:
            self._io_queue.put(None)
            self._io_thread.join(timeout=1.0)
            self._io_thread = None
        # The subprocess may have already exited, which on Windows makes closing
        # stdin raise OSError (invalid handle). Close the pipes best-effort and
        # never let cleanup itself raise.
        with suppress(OSError):
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        with suppress(OSError):
            self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                self._proc.kill()
        self._proc = None


__all__ = ["AGENT_SPECS", "SdkAgentSpec", "SdkAgentStrategy", "discover_sdk_python"]
