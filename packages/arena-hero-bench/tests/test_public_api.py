"""Freeze the package-root benchmark API across parallel feature integration."""

from __future__ import annotations

import arena_hero_bench

_EXPECTED_PUBLIC_API = {
    "ArtifactIndexError",
    "ArtifactManifest",
    "ArtifactStatus",
    "ArtifactStore",
    "BackendProcessSpec",
    "ConfigResolver",
    "ConfigSchema",
    "ContestantManifest",
    "ContestantRegistry",
    "DistributedExecutor",
    "FilesystemArtifactStore",
    "FrozenConfig",
    "GcCandidate",
    "GcPlan",
    "LocalExecutor",
    "MEASUREMENT_PROTOCOL_SCHEMA",
    "MINIMUM_CREDIBLE_SAMPLE_NS",
    "ManifestIssue",
    "MeasurementProtocol",
    "ObjectIssue",
    "PERFORMANCE_EVIDENCE_SCHEMA",
    "PerformanceEvidence",
    "PerformanceMeasurementError",
    "ProcessExecutor",
    "ProcessExecutorError",
    "PublicEnvironment",
    "RunManifest",
    "RunStatus",
    "ShardPlan",
    "ShardResult",
    "StaleScanError",
    "StoreScan",
    "build_gc_plan",
    "measure_reference_workload",
    "merge_shards",
    "reference_engine_process_executor",
}


def test_package_root_public_api_is_complete_and_unique() -> None:
    exported = arena_hero_bench.__all__

    assert len(exported) == len(set(exported))
    assert set(exported) == _EXPECTED_PUBLIC_API
    assert all(hasattr(arena_hero_bench, name) for name in exported)
