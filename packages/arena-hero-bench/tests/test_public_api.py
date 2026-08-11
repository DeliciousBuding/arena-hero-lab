"""Freeze the package-root benchmark API across parallel feature integration."""

from __future__ import annotations

import importlib
import inspect

import arena_hero_bench

_EXPECTED_PUBLIC_API = {
    "AGENT_RUN_EVIDENCE_SCHEMA",
    "AGENT_RUN_IMPORT_REPORT_SCHEMA",
    "AgentRunEvidence",
    "AgentRuntimeImportError",
    "COMPARATIVE_PERFORMANCE_EVIDENCE_SCHEMA",
    "ArtifactIndexError",
    "ArtifactManifest",
    "ArtifactStatus",
    "ArtifactStore",
    "BackendProcessSpec",
    "ConfigResolver",
    "ConfigSchema",
    "DEFAULT_EXPECTED_UNKNOWN",
    "DEFAULT_KPI_EXPECTED_UNKNOWN",
    "EVOLVE_PROTOCOL",
    "KPI_DIFFERENTIAL_SCHEMA",
    "KpiDimension",
    "KpiDimensionResult",
    "KpiReport",
    "KpiSideEvidence",
    "DifferentialDimension",
    "DifferentialError",
    "DifferentialOutcome",
    "DifferentialReport",
    "DifferentialStatus",
    "REPLAY_DIFFERENTIAL_SCHEMA",
    "ContestantManifest",
    "ComparativePerformanceEvidence",
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
    "REPLAY_ATTESTATION_UNATTESTED",
    "REPLAY_ATTESTATION_VERIFIED",
    "ReplayArtifactResolver",
    "RunManifest",
    "RunStatus",
    "ShardPlan",
    "ShardResult",
    "StaleScanError",
    "StoreScan",
    "build_differential_run",
    "build_gc_plan",
    "canonicalize_py_agent_record",
    "canonicalize_ts_legacy_record",
    "classify_differential_run",
    "import_agent_run",
    "PyObservationRecord",
    "build_kpi_differential_run",
    "classify_kpi_differential",
    "load_evolve_decision_trace",
    "load_py_agent_corpus",
    "load_py_observation_snapshots",
    "load_ts_legacy_corpus",
    "measure_comparative_workloads",
    "measure_reference_workload",
    "merge_shards",
    "reference_engine_process_executor",
    "run_differential_from_manifest",
    "run_kpi_differential_from_manifest",
    "world_state_digest",
}


def test_package_root_public_api_is_complete_and_unique() -> None:
    exported = arena_hero_bench.__all__

    assert len(exported) == len(set(exported))
    assert set(exported) == _EXPECTED_PUBLIC_API
    assert all(hasattr(arena_hero_bench, name) for name in exported)


def test_package_root_public_api_every_name_is_importable() -> None:
    module = importlib.import_module("arena_hero_bench")
    assert module is arena_hero_bench
    for name in arena_hero_bench.__all__:
        exported = getattr(module, name)
        assert exported is not None
        # Bind through real ``from arena_hero_bench import <name>`` semantics.
        bound = __import__("arena_hero_bench", fromlist=[name])
        assert getattr(bound, name) is exported


def test_package_root_public_api_has_no_leaks_or_omissions() -> None:
    public_attributes = {
        name
        for name, value in vars(arena_hero_bench).items()
        if not name.startswith("_") and not inspect.ismodule(value)
    }
    assert public_attributes == set(arena_hero_bench.__all__)
