from __future__ import annotations

import pytest

from arena_hero_bench.contestant import (
    ArtifactDigestMismatchError,
    ContestantManifest,
    ContestantRegistry,
    DuplicateContestantError,
    IsolationRequirements,
    ResourceRequirements,
)
from arena_hero_sim.serialization import content_sha256


def manifest(artifact: bytes = b"contestant") -> ContestantManifest:
    return ContestantManifest(
        schema_version="arena.contestant.v1",
        contestant_id="example-agent",
        version="1.2.0",
        entry_point="example_agent:main",
        language="python",
        runtime="cpython-3.12",
        protocol_version="arena.agent.v1",
        artifact_sha256=content_sha256(artifact),
        config_schema={"type": "object", "properties": {"risk": {"type": "number"}}},
        resources=ResourceRequirements(
            cpu_cores=1.0,
            memory_mb=512,
            process_limit=4,
            timeout_seconds=30.0,
        ),
        capabilities=frozenset({"deterministic", "batch-compatible"}),
        isolation=IsolationRequirements(),
    )


def test_registry_rejects_duplicate_version() -> None:
    registry = ContestantRegistry()
    registry.register(manifest(), artifact=b"contestant")
    with pytest.raises(DuplicateContestantError, match="already registered"):
        registry.register(manifest(), artifact=b"contestant")


def test_registry_rejects_artifact_hash_mismatch() -> None:
    registry = ContestantRegistry()
    with pytest.raises(ArtifactDigestMismatchError, match="does not match"):
        registry.register(manifest(), artifact=b"tampered")


def test_manifest_is_versioned_and_content_addressed() -> None:
    value = manifest()
    assert value.key == ("example-agent", "1.2.0")
    assert len(value.manifest_sha256()) == 64
    assert value.isolation.network_policy == "deny"
