from dataclasses import replace

import pytest

from arena_hero_sim import (
    OPTIMIZED_BACKEND_ID,
    OPTIMIZED_ENGINE_VERSION,
    OPTIMIZED_VISIBILITY_FEATURE,
    BackendWorkloadRunner,
    OptimizedEngineBackend,
    ReferenceWorkloadRunner,
    ReplayArtifactIdentity,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    canonical_reference_scenario_registry,
    canonical_reference_workload_manifest,
    compare_workload_runs,
)
from arena_hero_sim.optimized import _StaticVisibilityCache
from arena_hero_sim.reference import (
    REFERENCE_BACKEND_ID,
    REFERENCE_ENGINE_VERSION,
    REFERENCE_PROTOCOL_VERSION,
    BoundedReplayArtifactResolver,
    ReferenceEngineBackend,
    ReplayArtifactResolutionError,
)
from arena_hero_sim.reference_workload import (
    ReferenceWorkloadError,
    verify_workload_replay_artifacts,
)


@pytest.mark.parametrize("batch_size", (1, 3, 9))
def test_optimized_backend_matches_reference_corpus_for_supported_batch_sizes(
    batch_size: int,
) -> None:
    scenarios = canonical_reference_scenario_registry()
    manifest = canonical_reference_workload_manifest()
    reference = ReferenceWorkloadRunner(scenarios).run(manifest, batch_size=batch_size)
    candidate = BackendWorkloadRunner(scenarios, OptimizedEngineBackend(scenarios.scenarios)).run(
        manifest, batch_size=batch_size
    )

    report = compare_workload_runs(reference, candidate)
    assert report.passed
    assert candidate.backend.backend_id == OPTIMIZED_BACKEND_ID
    assert candidate.backend.engine_version == OPTIMIZED_ENGINE_VERSION
    assert tuple(episode.episode_id for episode in candidate.episodes) == tuple(
        episode.episode_id for episode in reference.episodes
    )
    assert tuple(episode.final_world_sha256 for episode in candidate.episodes) == tuple(
        episode.final_world_sha256 for episode in reference.episodes
    )
    assert tuple(dict(episode.metrics) for episode in candidate.episodes) == tuple(
        dict(episode.metrics) for episode in reference.episodes
    )
    assert tuple(
        ReplayArtifactIdentity.from_artifact_refs(episode.artifact_refs).semantic_sha256
        for episode in candidate.episodes
    ) == tuple(
        ReplayArtifactIdentity.from_artifact_refs(episode.artifact_refs).semantic_sha256
        for episode in reference.episodes
    )


def test_optimized_backend_registration_is_independent_and_fail_closed() -> None:
    scenarios = canonical_reference_scenario_registry()
    backend = OptimizedEngineBackend(scenarios.scenarios)
    descriptor = backend.descriptor

    assert descriptor.backend_id == OPTIMIZED_BACKEND_ID
    assert descriptor.engine_version == OPTIMIZED_ENGINE_VERSION
    assert OPTIMIZED_VISIBILITY_FEATURE in descriptor.capabilities.features
    assert descriptor.capabilities.supports_incremental_world_hash is False

    request = next(
        canonical_reference_workload_manifest().iter_requests(
            backend_id=OPTIMIZED_BACKEND_ID,
            engine_version=OPTIMIZED_ENGINE_VERSION,
            protocol_version="arena.sim.v1",
        )
    )
    wrong_identity = replace(
        request,
        config=replace(request.config, backend_id="reference-engine"),
    )
    result = backend.simulate(wrong_identity)
    assert result.status is SimulationStatus.UNSUPPORTED
    assert result.publishable is False
    assert result.errors == ("request backend_id does not match this backend",)


def test_visibility_cache_key_isolates_every_static_geometry_dimension() -> None:
    cache = _StaticVisibilityCache()

    def visible(
        *,
        width: int = 8,
        height: int = 8,
        obstacles: frozenset[tuple[int, int]] = frozenset({(2, 1)}),
        origin: tuple[int, int] = (1, 1),
        radius: int = 2,
    ) -> frozenset[tuple[int, int]]:
        return cache.visible_from(
            width=width,
            height=height,
            obstacles=obstacles,
            origin=origin,
            radius=radius,
        )

    first = visible()
    assert visible() is first
    assert cache.entry_count == 1

    variants = (
        visible(width=9),
        visible(height=9),
        visible(obstacles=frozenset({(1, 2)})),
        visible(origin=(2, 2)),
        visible(radius=3),
    )
    assert cache.entry_count == 6
    assert variants[2] != first


def test_builtin_backends_resolve_and_verify_actual_replay_bytes() -> None:
    scenarios = canonical_reference_scenario_registry()
    manifest = canonical_reference_workload_manifest()
    reference_runner = ReferenceWorkloadRunner(scenarios)
    candidate_runner = BackendWorkloadRunner(scenarios, OptimizedEngineBackend(scenarios.scenarios))

    reference = reference_runner.run(manifest, batch_size=9)
    candidate = candidate_runner.run(manifest, batch_size=9)
    reference_resolver = reference_runner.replay_artifact_resolver
    candidate_resolver = candidate_runner.replay_artifact_resolver
    assert reference_resolver is not None
    assert candidate_resolver is not None

    verify_workload_replay_artifacts(reference, reference_resolver)
    verify_workload_replay_artifacts(candidate, candidate_resolver)
    report = compare_workload_runs(
        reference,
        candidate,
        reference_replay_resolver=reference_resolver,
        candidate_replay_resolver=candidate_resolver,
    )
    assert report.passed


def test_differential_rejects_forged_replay_refs_against_candidate_resolver() -> None:
    scenarios = canonical_reference_scenario_registry()
    manifest = canonical_reference_workload_manifest()
    reference_runner = ReferenceWorkloadRunner(scenarios)
    candidate_runner = BackendWorkloadRunner(scenarios, OptimizedEngineBackend(scenarios.scenarios))
    reference = reference_runner.run(manifest, batch_size=9)
    candidate = candidate_runner.run(manifest, batch_size=9)
    reference_resolver = reference_runner.replay_artifact_resolver
    candidate_resolver = candidate_runner.replay_artifact_resolver
    assert reference_resolver is not None
    assert candidate_resolver is not None

    forged_episode = replace(
        candidate.episodes[0], artifact_refs=reference.episodes[0].artifact_refs
    )
    forged = replace(candidate, episodes=(forged_episode, *candidate.episodes[1:]))
    report = compare_workload_runs(
        reference,
        forged,
        reference_replay_resolver=reference_resolver,
        candidate_replay_resolver=candidate_resolver,
    )

    assert not report.passed
    assert any(mismatch.field == "candidate_run.replay_artifacts" for mismatch in report.mismatches)


def test_complete_workload_episode_rejects_backend_errors() -> None:
    scenarios = canonical_reference_scenario_registry()
    run = ReferenceWorkloadRunner(scenarios).run(
        canonical_reference_workload_manifest(), batch_size=9
    )

    with pytest.raises(ValueError, match="cannot contain errors"):
        replace(run.episodes[0], errors=("backend reported an error",))


def test_differential_compares_partial_episode_errors() -> None:
    scenarios = canonical_reference_scenario_registry()
    reference = ReferenceWorkloadRunner(scenarios).run(
        canonical_reference_workload_manifest(), batch_size=9
    )
    candidate_episode = replace(
        reference.episodes[0],
        status=SimulationStatus.PARTIAL,
        publishable=False,
        errors=("tick budget ended",),
    )
    candidate = replace(
        reference,
        episodes=(candidate_episode, *reference.episodes[1:]),
        publishable=False,
        issues=("episode 0 is partial",),
    )

    report = compare_workload_runs(reference, candidate)

    assert not report.passed
    assert any(mismatch.field == "errors" for mismatch in report.mismatches)


def test_bounded_replay_resolver_evicts_oldest_envelope() -> None:
    scenarios = canonical_reference_scenario_registry()
    manifest = canonical_reference_workload_manifest()
    requests = tuple(
        manifest.iter_requests(
            backend_id=REFERENCE_BACKEND_ID,
            engine_version=REFERENCE_ENGINE_VERSION,
            protocol_version=REFERENCE_PROTOCOL_VERSION,
        )
    )
    backend = ReferenceEngineBackend(scenarios.scenarios, replay_capacity=1)
    first = backend.simulate(requests[0])
    second = backend.simulate(requests[1])
    first_identity = ReplayArtifactIdentity.from_artifact_refs(first.artifact_refs)
    second_identity = ReplayArtifactIdentity.from_artifact_refs(second.artifact_refs)
    resolver = backend.replay_artifact_resolver
    assert isinstance(resolver, BoundedReplayArtifactResolver)
    assert resolver.entry_count == 1

    with pytest.raises(ReplayArtifactResolutionError, match="unknown replay"):
        resolver.resolve_replay(first_identity.envelope_sha256)
    second_replay = resolver.resolve_replay(second_identity.envelope_sha256)
    second_identity.verify(second_replay)


def test_runner_fails_closed_when_backend_claims_unresolvable_replay_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = canonical_reference_scenario_registry()
    backend = OptimizedEngineBackend(scenarios.scenarios)
    runner = BackendWorkloadRunner(scenarios, backend)
    resolver = backend.replay_artifact_resolver
    assert isinstance(resolver, BoundedReplayArtifactResolver)
    resolver._artifacts.clear()

    original_simulate_batch = backend.simulate_batch

    def execute_then_drop(
        requests: tuple[SimulationRequest, ...],
    ) -> tuple[SimulationResult, ...]:
        results = original_simulate_batch(requests)
        resolver._artifacts.clear()
        return results

    monkeypatch.setattr(backend, "simulate_batch", execute_then_drop)
    with pytest.raises(ReferenceWorkloadError, match="replay artifact verification failed"):
        runner.run(canonical_reference_workload_manifest(), batch_size=9)
