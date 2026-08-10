"""Backend registration, capability negotiation, and contract validation."""

from __future__ import annotations

from collections.abc import Iterable

from arena_hero_sim.backend import SimulatorBackend
from arena_hero_sim.contracts import SimulationRequest, SimulationResult


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


class BackendRegistry:
    """In-memory registry with deterministic lookup and fail-closed negotiation."""

    def __init__(self) -> None:
        self._backends: dict[str, SimulatorBackend] = {}

    def register(self, backend: SimulatorBackend) -> None:
        backend_id = backend.descriptor.backend_id
        if backend_id in self._backends:
            raise DuplicateBackendError(f"backend already registered: {backend_id}")
        self._backends[backend_id] = backend

    def get(self, backend_id: str) -> SimulatorBackend:
        try:
            return self._backends[backend_id]
        except KeyError as error:
            raise UnknownBackendError(f"unknown backend: {backend_id}") from error

    def backend_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))

    def negotiate(self, request: SimulationRequest) -> SimulatorBackend:
        backend = self.get(request.config.backend_id)
        descriptor = backend.descriptor
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
        return backend

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        backend = self.negotiate(request)
        result = backend.simulate(request)
        self._validate_result(request, result)
        return result

    def simulate_batch(self, requests: Iterable[SimulationRequest]) -> tuple[SimulationResult, ...]:
        batch = tuple(requests)
        if not batch:
            return ()
        backend = self.negotiate(batch[0])
        backend_id = batch[0].config.backend_id
        for request in batch[1:]:
            if request.config.backend_id != backend_id:
                raise BackendRegistryError("one batch cannot mix backend ids")
            self.negotiate(request)
        capabilities = backend.descriptor.capabilities
        chunk_size = capabilities.max_batch_size if capabilities.supports_batch else 1
        results: list[SimulationResult] = []
        for offset in range(0, len(batch), chunk_size):
            chunk = batch[offset : offset + chunk_size]
            chunk_results = (
                backend.simulate_batch(chunk)
                if capabilities.supports_batch
                else tuple(backend.simulate(request) for request in chunk)
            )
            if len(chunk_results) != len(chunk):
                raise BackendContractError("backend returned a different batch cardinality")
            for request, result in zip(chunk, chunk_results, strict=True):
                self._validate_result(request, result)
            results.extend(chunk_results)
        return tuple(results)

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
