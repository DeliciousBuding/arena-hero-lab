"""Run a third-party official-SDK agent as an FFA contestant (subprocess).

The four non-evolve third-party agents (drew-z / waaiging / guide / tactic) are
written against the official ``arena-hero`` SDK.  This module wraps any of them
as a long-lived subprocess running in that repo's own ``.venv`` (or, when the
repo has no venv, the fork's ``arena-hero-sdk-py`` venv), so the SDK dependency
never enters the Lab environment.

Per tick the parent sends :func:`arena_hero_sim.ffa.sdk_bridge.observation_to_sdk_state`
JSON over stdin; the runner constructs a real SDK ``Turn``, calls the adapter's
``run_turn(turn)``, and prints ``turn.plan`` JSON back.  The parent maps that
back with :func:`arena_hero_sim.ffa.sdk_bridge.sdk_plan_to_ffa`.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .observation import Observation
from .sdk_bridge import observation_to_sdk_state, sdk_plan_to_ffa

# arena-hero-lab/packages/arena-hero-sim/src/arena_hero_sim/ffa/sdk_agent_shim.py
# parents: 0=ffa 1=arena_hero_sim 2=src 3=arena-hero-sim 4=packages 5=arena-hero-lab 6=arena
_ARENA_ROOT = Path(__file__).resolve().parents[6]

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

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def decide(self, observation: Observation) -> dict[str, object]:
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
                bufsize=1,
            )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps(payload, sort_keys=True) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            stderr = "" if self._proc.stderr is None else self._proc.stderr.read()
            raise RuntimeError(f"sdk agent subprocess exited unexpectedly: {stderr.strip()}")
        return sdk_plan_to_ffa(json.loads(line))

    def close(self) -> None:
        if self._proc is None:
            return
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None


__all__ = ["AGENT_SPECS", "SdkAgentSpec", "SdkAgentStrategy", "discover_sdk_python"]
