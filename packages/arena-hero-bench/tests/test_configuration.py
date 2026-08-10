from __future__ import annotations

import pytest

from arena_hero_bench.configuration import (
    ConfigField,
    ConfigResolver,
    ConfigSchema,
    ConfigTypeError,
    ConfigValueType,
    SecretInSnapshotError,
    UnknownConfigKeyError,
)


def schema() -> ConfigSchema:
    return ConfigSchema(
        schema_version="arena.config.v1",
        fields=(
            ConfigField("ticks", ConfigValueType.INTEGER, required=True, minimum=1),
            ConfigField("risk", ConfigValueType.NUMBER, default=0.25, minimum=0, maximum=1),
            ConfigField("mode", ConfigValueType.STRING, default="balanced"),
            ConfigField("credential", ConfigValueType.STRING, secret=True),
        ),
    )


def test_configuration_precedence_and_canonical_snapshot() -> None:
    snapshot = ConfigResolver(schema()).resolve(
        defaults={"ticks": 100, "risk": 0.1},
        experiment={"ticks": 200},
        contestant={"risk": 0.5},
        run_overrides={"ticks": 300},
    )
    assert snapshot.values == {"mode": "balanced", "risk": 0.5, "ticks": 300}
    assert len(snapshot.canonical_sha256) == 64
    assert set(snapshot.layer_sha256) == {
        "defaults",
        "experiment",
        "contestant",
        "run_overrides",
    }


def test_configuration_rejects_unknown_key() -> None:
    with pytest.raises(UnknownConfigKeyError, match="unknown keys"):
        ConfigResolver(schema()).resolve(defaults={"ticks": 100}, experiment={"typo": 1})


def test_configuration_rejects_type_error() -> None:
    with pytest.raises(ConfigTypeError, match="ticks must be integer"):
        ConfigResolver(schema()).resolve(defaults={"ticks": "many"})


def test_configuration_rejects_secret_snapshot() -> None:
    with pytest.raises(SecretInSnapshotError, match="cannot enter snapshot"):
        ConfigResolver(schema()).resolve(
            defaults={"ticks": 100},
            run_overrides={"credential": "not-for-snapshots"},
        )
