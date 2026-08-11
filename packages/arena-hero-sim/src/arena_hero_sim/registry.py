"""Backend registration, capability negotiation, and contract validation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from arena_hero_sim.backend import BackendDescriptor, SimulatorBackend
from arena_hero_sim.contracts import BackendCapabilities, SimulationRequest, SimulationResult


class BackendRegistryError(ValueError):
    pass


class DuplicateBackendError(BackendRegistryError):
    pass


class UnknownBackendError(BackendRegistryError):
    pass


class UnknownCapabilityError(BackendRegistryError):
    pass


class ProtocolNegotiationError(BackendRegistryError):
    pass


class BackendContractError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _RegisteredBackend:
    backend: SimulatorBackend
    descriptor: BackendDescriptor


def _snapshot_descriptor(descriptor: BackendDescriptor) -> BackendDescriptor:
    capabilities = descriptor.capabilities
    return BackendDescriptor(
        backend_id=descriptor.backend_id,
        engine_version=descriptor.engine_version,
        capabilities=BackendCapabilities(
            protocol_versions=tuple(capabilities.protocol_versions),
            features=frozenset(capabilities.features),
            execution_modes=frozenset(capabilities.execution_modes),
            max_batch_size=capabilities.max_batch_size,
            supports_batch=capabilities.supports_batch,
            supports_incremental_world_hash=capabilities.supports_incremental_world_hash,
            supports_zero_copy=capabilities.supports_zero_copy,
            interchange_formats=frozenset(capabilities.interchange_formats),
        ),
    )


class BackendRegistry:
    """In-memory registry with a frozen execution identity per backend."""

    def __init__(self) -> None:
        self._backends: dict[str, _RegisteredBackend] = {}

    def register(self, backend: SimulatorBackend) -> BackendDescriptor:
        snapshot = _snapshot_descriptor(backend.descriptor)
        backend_id = snapshot.backend_id
        if backend_id in self._backends:
            raise DuplicateBackendError(f"backend already registered: {backend_id}")
        registered = _RegisteredBackend(backend=backend, descriptor=snapshot)
        self._backends[backend_id] = registered
        try:
            self._assert_descriptor_stable(registered)
        except Exception:
            del self._backends[backend_id]
            raise
        return snapshot

    def get(self, backend_id: str) -> SimulatorBackend:
        registered = self._get_registered(backend_id)
        self._assert_descriptor_stable(registered)
        return registered.backend

    def descriptor_snapshot(self, backend_id: str) -> BackendDescriptor:
        registered = self._get_registered(backend_id)
        self._assert_descriptor_stable(registered)
        return registered.descriptor

    def verify_backend(self, backend_id: str) -> None:
        self._assert_descriptor_stable(self._get_registered(backend_id))

    def backend_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))

    def negotiate(self, request: SimulationRequest) -> SimulatorBackend:
        return self._negotiate_registered(request).backend

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        registered = self._negotiate_registered(request)
        self._assert_descriptor_stable(registered)
        result = registered.backend.simulate(request)
        self._assert_descriptor_stable(registered)
        self._validate_result(request, result)
        return result

    def simulate_batch(self, requests: Iterable[SimulationRequest]) -> tuple[SimulationResult, ...]:
        batch = tuple(requests)
        if not batch:
            return ()
        registered = self._negotiate_registered(batch[0])
        backend_id = registered.descriptor.backend_id
        for request in batch[1:]:
            if request.config.backend_id != backend_id:
                raise BackendRegistryError("one batch cannot mix backend ids")
            other = self._negotiate_registered(request)
            if other is not registered:
                raise BackendContractError("batch backend registration changed during negotiation")
        capabilities = registered.descriptor.capabilities
        chunk_size = capabilities.max_batch_size if capabilities.supports_batch else 1
        results: list[SimulationResult] = []
        for offset in range(0, len(batch), chunk_size):
            chunk = batch[offset : offset + chunk_size]
            self._assert_descriptor_stable(registered)
            chunk_results = (
                registered.backend.simulate_batch(chunk)
                if capabilities.supports_batch
                else tuple(registered.backend.simulate(request) for request in chunk)
            )
            self._assert_descriptor_stable(registered)
            if len(chunk_results) != len(chunk):
                raise BackendContractError("backend returned a different batch cardinality")
            for request, result in zip(chunk, chunk_results, strict=True):
                self._validate_result(request, result)
            results.extend(chunk_results)
        self._assert_descriptor_stable(registered)
        return tuple(results)

    def _get_registered(self, backend_id: str) -> _RegisteredBackend:
        try:
            return self._backends[backend_id]
        except KeyError as error:
            raise UnknownBackendError(f"unknown backend: {backend_id}") from error

    def _negotiate_registered(self, request: SimulationRequest) -> _RegisteredBackend:
        registered = self._get_registered(request.config.backend_id)
        self._assert_descriptor_stable(registered)
        descriptor = registered.descriptor
        if request.config.engine_version != descriptor.engine_version:
            raise ProtocolNegotiationError(
                "requested engine_version does not match the registered backend"
            )
        capabilities = descriptor.capabilities
        if request.config.protocol_version not in capabilities.protocol_versions:
            raise ProtocolNegotiationError(
                f"unsupported protocol version: {request.config.protocol_version}"
            )
        missing = capabilities.missing_features(request.config.requested_features)
        if missing:
            raise UnknownCapabilityError(f"unsupported capabilities: {', '.join(sorted(missing))}")
        return registered

    @staticmethod
    def _assert_descriptor_stable(registered: _RegisteredBackend) -> None:
        current = _snapshot_descriptor(registered.backend.descriptor)
        if current != registered.descriptor:
            raise BackendContractError(
                "backend descriptor changed after registration; execution identity is unstable"
            )

    @staticmethod
    def _validate_result(request: SimulationRequest, result: SimulationResult) -> None:
        expected = (
            request.request_id,
            request.episode_id,
            request.config.backend_id,
            request.config.engine_version,
            request.config.ruleset.rules_sha256,
            request.config.seed,
        )
        actual = (
            result.request_id,
            result.episode_id,
            result.backend_id,
            result.engine_version,
            result.rules_sha256,
            result.seed,
        )
        if actual != expected:
            raise BackendContractError("backend result identity does not match the request")
