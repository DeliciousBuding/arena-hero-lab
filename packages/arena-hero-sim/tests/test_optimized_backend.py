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
    SimulationStatus,
    canonical_reference_scenario_registry,
    canonical_reference_workload_manifest,
    compare_workload_runs,
)
from arena_hero_sim.optimized import _StaticVisibilityCache


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
