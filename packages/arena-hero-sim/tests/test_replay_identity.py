from dataclasses import replace

import pytest

from arena_hero_sim import (
    ReplayArtifactIdentity,
    canonical_reference_scenario_registry,
    run_reference_episode,
)


def _episode(request_id: str):
    scenario = canonical_reference_scenario_registry().scenarios[0]
    return run_reference_episode(
        scenario,
        request_id=request_id,
        episode_id="episode-semantic-identity",
        max_ticks=len(scenario.turns),
    )


def test_replay_v1_envelope_stays_compatible_and_semantic_identity_is_backend_neutral() -> None:
    reference = _episode("request-reference")
    candidate = _episode("request-candidate")

    assert reference.replay.payload["schemaVersion"] == "arena.reference.replay.v1"
    assert reference.replay.payload_sha256 != candidate.replay.payload_sha256
    assert reference.replay.envelope_sha256 != candidate.replay.envelope_sha256
    assert reference.replay.semantic_sha256 == candidate.replay.semantic_sha256
    assert (
        reference.replay.to_bytes()
        == type(reference.replay).from_bytes(reference.replay.to_bytes()).to_bytes()
    )


def test_replay_artifact_refs_separate_payload_envelope_and_semantic_identity() -> None:
    replay = _episode("request-reference").replay
    refs = replay.artifact_identity.to_artifact_refs()

    assert refs[0] == f"replay-sha256:{replay.payload_sha256}"
    assert refs[1] == f"replay-payload-sha256:{replay.payload_sha256}"
    assert refs[2] == f"replay-envelope-sha256:{replay.envelope_sha256}"
    assert refs[3] == f"replay-semantic-sha256:{replay.semantic_sha256}"
    parsed = ReplayArtifactIdentity.from_artifact_refs(refs)
    parsed.verify(replay)


def test_semantic_artifact_tamper_fails_strict_verification() -> None:
    replay = _episode("request-reference").replay
    forged = replace(replay.artifact_identity, semantic_sha256="0" * 64)

    with pytest.raises(ValueError, match="does not match"):
        forged.verify(replay)


def test_replay_artifact_refs_reject_wrong_legacy_alias() -> None:
    replay = _episode("request-reference").replay
    refs = replay.artifact_identity.to_artifact_refs()
    forged = (f"replay-sha256:{'0' * 64}", *refs[1:])

    with pytest.raises(ValueError, match="legacy replay digest"):
        ReplayArtifactIdentity.from_artifact_refs(forged)
