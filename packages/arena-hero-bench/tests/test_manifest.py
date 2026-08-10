import pytest

from arena_hero_bench.manifest import ArtifactManifest, ArtifactStatus, RunManifest

_SHA = "a" * 64


def test_complete_artifact_verifies_content() -> None:
    content = {"run": "fixture", "scores": [1, 2, 3]}
    manifest = ArtifactManifest.for_content(
        content=content,
        schema_version="arena.lab.artifact.v1",
        generator_version="0.1.0",
        provenance={"source": "tests/fixture.json"},
        source_build_sha256=_SHA,
    )

    assert manifest.publishable is True
    assert manifest.verify_content(content)
    assert not manifest.verify_content({"run": "tampered"})


def test_partial_artifact_must_not_be_publishable() -> None:
    with pytest.raises(ValueError, match="partial manifests"):
        ArtifactManifest(
            schema_version="arena.lab.artifact.v1",
            generator_version="0.1.0",
            provenance={"source": "tests/fixture.json"},
            source_build_sha256=_SHA,
            content_sha256="b" * 64,
            status=ArtifactStatus.PARTIAL,
            publishable=True,
        )


def test_publishable_run_rejects_partial_artifact() -> None:
    partial = ArtifactManifest(
        schema_version="arena.lab.artifact.v1",
        generator_version="0.1.0",
        provenance={"source": "tests/fixture.json"},
        source_build_sha256=_SHA,
        content_sha256="b" * 64,
        status=ArtifactStatus.PARTIAL,
        publishable=False,
    )

    with pytest.raises(ValueError, match="unpublishable artifact"):
        RunManifest(
            schema_version="arena.lab.run.v1",
            generator_version="0.1.0",
            provenance={"source": "tests/run.json"},
            source_build_sha256=_SHA,
            content_sha256="c" * 64,
            status=ArtifactStatus.COMPLETE,
            publishable=True,
            artifacts=(partial,),
        )


def test_manifest_rejects_non_sha256_identifiers() -> None:
    with pytest.raises(ValueError, match="source_build_sha256"):
        ArtifactManifest(
            schema_version="arena.lab.artifact.v1",
            generator_version="0.1.0",
            provenance={"source": "tests/fixture.json"},
            source_build_sha256="git-short-sha",
            content_sha256="b" * 64,
            status=ArtifactStatus.COMPLETE,
            publishable=False,
        )
