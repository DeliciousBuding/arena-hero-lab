from dataclasses import replace

import pytest

from arena_hero_bench.orchestration import (
    ExperimentId,
    IdempotencyConflictError,
    InMemoryExecutionLedger,
    RunId,
    RunStatus,
    ShardId,
    ShardPlan,
    ShardResult,
)
from arena_hero_sim.contracts import RulesetRef, SimulationRequest, SimulatorConfig


def _request() -> SimulationRequest:
    return SimulationRequest(
        request_id="request-identity",
        episode_id="episode-identity",
        config=SimulatorConfig(
            backend_id="reference-placeholder",
            engine_version="0.1.0-placeholder",
            ruleset=RulesetRef("arena-hero", "fixture-v1", "a" * 64),
            seed=7,
            max_ticks=12,
            protocol_version="arena.sim.v1",
            deterministic=True,
            requested_features=frozenset({"feature-a", "feature-b"}),
            parameters={"mode": "strict", "profile": "baseline"},
        ),
        initial_state_sha256="b" * 64,
        input_artifact_sha256="c" * 64,
        contestant_ids=("alpha", "beta"),
        labels={"case": "identity", "source": "fixture"},
    )


def _plan(request: SimulationRequest) -> ShardPlan:
    return ShardPlan.create(
        operation_id="operation-identity",
        experiment_id=ExperimentId("experiment-identity"),
        run_id=RunId("run-identity"),
        shard_id=ShardId("shard-identity"),
        requests=(request,),
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: replace(item, input_artifact_sha256="d" * 64),
        lambda item: replace(item, config=replace(item.config, max_ticks=13)),
        lambda item: replace(item, config=replace(item.config, protocol_version="arena.sim.v2")),
        lambda item: replace(
            item,
            config=replace(item.config, requested_features=frozenset({"feature-a"})),
        ),
        lambda item: replace(
            item,
            config=replace(item.config, parameters={"mode": "strict", "profile": "changed"}),
        ),
        lambda item: replace(item, config=replace(item.config, deterministic=False)),
        lambda item: replace(item, labels={"case": "changed", "source": "fixture"}),
    ],
    ids=(
        "input-artifact",
        "max-ticks",
        "protocol-version",
        "requested-features",
        "parameters",
        "deterministic",
        "labels",
    ),
)
def test_plan_sha256_binds_every_resume_sensitive_request_field(mutate) -> None:
    baseline = _plan(_request())
    changed = _plan(mutate(_request()))

    assert changed.plan_sha256 != baseline.plan_sha256


def test_plan_identity_is_canonical_for_unordered_sets_and_mappings() -> None:
    original = _request()
    reordered = replace(
        original,
        config=replace(
            original.config,
            requested_features=frozenset({"feature-b", "feature-a"}),
            parameters={"profile": "baseline", "mode": "strict"},
        ),
        labels={"source": "fixture", "case": "identity"},
    )

    assert _plan(reordered).plan_sha256 == _plan(original).plan_sha256


def test_resume_rejects_same_operation_for_a_different_scenario_plan() -> None:
    ledger = InMemoryExecutionLedger()
    baseline = _plan(_request())
    result = ShardResult(
        run_id=baseline.run_id,
        shard_id=baseline.shard_id,
        status=RunStatus.COMPLETE,
        publishable=True,
        content_sha256="e" * 64,
        artifact_ref="sha256:fixture",
        request_ids=("request-identity",),
    )
    ledger.record(baseline.operation_id, baseline.plan_sha256, result)
    different_scenario = _plan(replace(_request(), input_artifact_sha256="f" * 64))

    with pytest.raises(IdempotencyConflictError, match="different plan"):
        ledger.resume(different_scenario.operation_id, different_scenario.plan_sha256)
