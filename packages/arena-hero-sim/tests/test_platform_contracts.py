from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from arena_hero_sim.contracts import (
    BackendCapabilities,
    RulesetRef,
    SimulationRequest,
    SimulationStatus,
    SimulatorConfig,
)
from arena_hero_sim.microbenchmark import run_contract_dispatch_microbenchmark
from arena_hero_sim.reference import ReferenceBackendPlaceholder
from arena_hero_sim.registry import (
    BackendRegistry,
    DuplicateBackendError,
    ProtocolNegotiationError,
    UnknownCapabilityError,
)

_RULES = RulesetRef("arena-hero", "fixture-v1", "a" * 64)


def request(
    *, features: frozenset[str] = frozenset(), protocol: str = "arena.sim.v1"
) -> SimulationRequest:
    return SimulationRequest(
        request_id="request-1",
        episode_id="episode-1",
        config=SimulatorConfig(
            backend_id="reference-placeholder",
            engine_version="0.1.0-placeholder",
            ruleset=_RULES,
            seed=7,
            max_ticks=100,
            protocol_version=protocol,
            requested_features=features,
        ),
        initial_state_sha256="b" * 64,
        contestant_ids=("alpha", "beta"),
    )


def registry() -> BackendRegistry:
    value = BackendRegistry()
    value.register(ReferenceBackendPlaceholder())
    return value


def test_contracts_are_immutable() -> None:
    config = request().config
    with pytest.raises(FrozenInstanceError):
        config.__setattr__("seed", 8)


def test_backend_registry_rejects_duplicate_registration() -> None:
    value = registry()
    with pytest.raises(DuplicateBackendError, match="already registered"):
        value.register(ReferenceBackendPlaceholder())


def test_backend_registry_rejects_unknown_capability() -> None:
    with pytest.raises(UnknownCapabilityError, match="incremental-world-hash"):
        registry().simulate(request(features=frozenset({"incremental-world-hash"})))


def test_backend_registry_rejects_unknown_protocol() -> None:
    with pytest.raises(ProtocolNegotiationError, match="unsupported protocol"):
        registry().simulate(request(protocol="arena.sim.v99"))


def test_reference_backend_is_explicitly_unsupported() -> None:
    result = registry().simulate(request())
    assert result.status is SimulationStatus.UNSUPPORTED
    assert result.publishable is False
    assert result.final_world_sha256 is None
    assert "does not implement game rules" in result.errors[0]


def test_batch_dispatch_preserves_request_order() -> None:
    first = request()
    second = SimulationRequest(
        request_id="request-2",
        episode_id="episode-2",
        config=SimulatorConfig(
            backend_id="reference-placeholder",
            engine_version="0.1.0-placeholder",
            ruleset=_RULES,
            seed=8,
            max_ticks=100,
            protocol_version="arena.sim.v1",
        ),
        initial_state_sha256="c" * 64,
        contestant_ids=("alpha", "beta"),
    )
    results = registry().simulate_batch((first, second))
    assert tuple(result.request_id for result in results) == ("request-1", "request-2")


def test_microbenchmark_report_is_non_production_contract_measurement() -> None:
    report = run_contract_dispatch_microbenchmark(episodes=20, repeats=2, batch_size=8)
    assert report.schema_version == "arena.sim.microbenchmark.v1"
    assert report.production_claim is False
    assert report.episodes_per_second > 0
    assert len(report.durations_ns) == 2


def test_batch_capability_invariants() -> None:
    with pytest.raises(ValueError, match="non-batch"):
        BackendCapabilities(protocol_versions=("v1",), supports_batch=False, max_batch_size=2)
