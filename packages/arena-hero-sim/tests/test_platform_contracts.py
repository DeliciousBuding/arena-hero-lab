from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from arena_hero_sim.backend import BackendDescriptor
from arena_hero_sim.contracts import (
    BackendCapabilities,
    RulesetRef,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorConfig,
)
from arena_hero_sim.microbenchmark import run_contract_dispatch_microbenchmark
from arena_hero_sim.reference import ReferenceBackendPlaceholder
from arena_hero_sim.registry import (
    BackendContractError,
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


class DescriptorDriftBackend:
    def __init__(self) -> None:
        self._descriptor = ReferenceBackendPlaceholder().descriptor
        self._delegate = ReferenceBackendPlaceholder()
        self._drift_after_call = False

    @property
    def descriptor(self) -> BackendDescriptor:
        return self._descriptor

    def drift(self, field: str) -> None:
        capabilities = self._descriptor.capabilities
        if field == "engine_version":
            self._descriptor = replace(self._descriptor, engine_version="0.1.1-drift")
        elif field == "features":
            self._descriptor = replace(
                self._descriptor,
                capabilities=replace(capabilities, features=frozenset({"drift"})),
            )
        elif field == "max_batch_size":
            self._descriptor = replace(
                self._descriptor,
                capabilities=replace(capabilities, max_batch_size=512),
            )
        elif field == "protocol_versions":
            self._descriptor = replace(
                self._descriptor,
                capabilities=replace(capabilities, protocol_versions=("arena.sim.v2",)),
            )
        else:
            raise AssertionError(field)

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        result = self._delegate.simulate(request)
        if self._drift_after_call:
            self.drift("features")
        return result

    def simulate_batch(
        self, requests: tuple[SimulationRequest, ...]
    ) -> tuple[SimulationResult, ...]:
        results = self._delegate.simulate_batch(requests)
        if self._drift_after_call:
            self.drift("max_batch_size")
        return results


class AlternatingDescriptorBackend(DescriptorDriftBackend):
    def __init__(self) -> None:
        super().__init__()
        self.descriptor_calls = 0
        self._base = self._descriptor
        self._alternate = replace(
            self._base,
            capabilities=replace(
                self._base.capabilities,
                features=frozenset({"contract-validation", "drift-a"}),
                max_batch_size=512,
            ),
        )

    @property
    def descriptor(self) -> BackendDescriptor:
        self.descriptor_calls += 1
        if self.descriptor_calls <= 2:
            return self._base
        return self._base if self.descriptor_calls % 2 else self._alternate


def test_registry_rejects_alternating_descriptor_instead_of_recording_last_value() -> None:
    backend = AlternatingDescriptorBackend()
    value = BackendRegistry()
    snapshot = value.register(backend)

    assert snapshot.capabilities.max_batch_size == 1024
    value.verify_backend(snapshot.backend_id)
    with pytest.raises(BackendContractError, match="descriptor changed"):
        value.verify_backend(snapshot.backend_id)


@pytest.mark.parametrize(
    "field",
    ("engine_version", "features", "max_batch_size", "protocol_versions"),
)
def test_registry_rejects_descriptor_drift_before_execution(field: str) -> None:
    backend = DescriptorDriftBackend()
    value = BackendRegistry()
    snapshot = value.register(backend)
    backend.drift(field)

    with pytest.raises(BackendContractError, match="descriptor changed"):
        value.simulate(request())
    assert snapshot.engine_version == "0.1.0-placeholder"
    assert snapshot.capabilities.max_batch_size == 1024


def test_registry_rejects_descriptor_drift_during_single_execution() -> None:
    backend = DescriptorDriftBackend()
    value = BackendRegistry()
    value.register(backend)
    backend._drift_after_call = True

    with pytest.raises(BackendContractError, match="descriptor changed"):
        value.simulate(request())


def test_registry_rejects_descriptor_drift_during_batch_execution() -> None:
    backend = DescriptorDriftBackend()
    value = BackendRegistry()
    value.register(backend)
    backend._drift_after_call = True

    with pytest.raises(BackendContractError, match="descriptor changed"):
        value.simulate_batch((request(),))
