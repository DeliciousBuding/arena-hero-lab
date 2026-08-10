from __future__ import annotations

from dataclasses import replace

import pytest

from arena_hero_research.provenance import (
    EnvironmentProvenance,
    EnvironmentSnapshot,
    ProvenanceError,
    SoftwareBillOfMaterials,
    SoftwareComponent,
)


def _environment() -> EnvironmentSnapshot:
    return EnvironmentSnapshot.create(
        python_implementation="CPython",
        python_version="3.12.10",
        operating_system="Linux",
        operating_system_release="6.8.0",
        machine="x86_64",
        executor="local-reference",
        metadata={"container": {"runtime": "none"}},
    )


def _sbom() -> SoftwareBillOfMaterials:
    return SoftwareBillOfMaterials.create(
        generator="arena-hero-research",
        components=(
            SoftwareComponent(
                name="arena-hero-research",
                version="0.2.0",
                purl="pkg:pypi/arena-hero-research@0.2.0",
                license_ids=("Apache-2.0",),
            ),
            SoftwareComponent(
                name="Python",
                version="3.12.10",
                component_type="runtime",
                purl="pkg:generic/python@3.12.10",
            ),
        ),
        metadata={"scope": "explicit-runtime-allowlist"},
    )


def test_environment_snapshot_is_stable_public_and_round_trippable() -> None:
    environment = _environment()

    assert environment.verify()
    assert EnvironmentSnapshot.from_dict(environment.to_dict()) == environment
    assert set(environment.payload()) == {
        "schema_version",
        "python_implementation",
        "python_version",
        "operating_system",
        "operating_system_release",
        "machine",
        "executor",
        "metadata",
    }
    assert "hostname" not in environment.payload()
    assert "executable" not in environment.payload()


def test_public_capture_does_not_expose_ambient_environment(monkeypatch) -> None:
    monkeypatch.setenv("ARENA_RESEARCH_SECRET", "must-not-be-read")
    snapshot = EnvironmentSnapshot.capture_public(executor="local-reference")

    serialized = str(snapshot.to_dict())
    assert "ARENA_RESEARCH_SECRET" not in serialized
    assert "must-not-be-read" not in serialized
    assert snapshot.verify()


def test_sbom_is_deterministic_and_round_trippable() -> None:
    sbom = _sbom()
    reversed_sbom = SoftwareBillOfMaterials.create(
        generator=sbom.generator,
        components=tuple(reversed(sbom.components)),
        metadata=sbom.metadata,
    )

    assert sbom.verify()
    assert SoftwareBillOfMaterials.from_dict(sbom.to_dict()) == sbom
    assert reversed_sbom == sbom


def test_sbom_rejects_duplicate_component_identity() -> None:
    component = SoftwareComponent(name="package", version="1.0")
    with pytest.raises(ProvenanceError, match="unique"):
        SoftwareBillOfMaterials.create(
            generator="arena-hero-research",
            components=(component, component),
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EnvironmentSnapshot.create(
            python_implementation="CPython",
            python_version="3.12",
            operating_system="Linux",
            operating_system_release="6",
            machine="x86_64",
            executor="local-reference",
            metadata={"nested": [{"api-token": "forbidden"}]},
        ),
        lambda: SoftwareBillOfMaterials.create(
            generator="arena-hero-research",
            components=(SoftwareComponent(name="package", version="1"),),
            metadata={"build": {"password": "forbidden"}},
        ),
        lambda: SoftwareComponent(
            name="package",
            version="1",
            properties={"registry": {"credential_file": "forbidden"}},
        ),
    ],
)
def test_environment_and_sbom_recursively_reject_sensitive_fields(factory) -> None:
    with pytest.raises(ValueError, match="sensitive key"):
        factory()


def test_environment_provenance_binds_verified_artifacts() -> None:
    environment = _environment()
    sbom = _sbom()
    provenance = EnvironmentProvenance.create(
        environment=environment,
        sbom=sbom,
        metadata={"visibility": "public"},
    )

    assert provenance.verify(environment, sbom)
    assert EnvironmentProvenance.from_dict(provenance.to_dict()) == provenance
    assert not provenance.verify(replace(environment, canonical_sha256="f" * 64), sbom)
