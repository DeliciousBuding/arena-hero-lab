"""Strict layered configuration with canonical frozen snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value


class ConfigError(ValueError):
    pass


class UnknownConfigKeyError(ConfigError):
    pass


class ConfigTypeError(ConfigError):
    pass


class SecretInSnapshotError(ConfigError):
    pass


class ConfigValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"


@dataclass(frozen=True, slots=True)
class ConfigField:
    name: str
    value_type: ConfigValueType
    required: bool = False
    default: JsonValue | None = None
    secret: bool = False
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[JsonValue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("config field name must not be empty")
        if self.secret and self.default is not None:
            raise ValueError("secret fields cannot define snapshot defaults")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        if self.default is not None:
            self.validate(self.default)

    def validate(self, value: JsonValue) -> None:
        valid = {
            ConfigValueType.STRING: isinstance(value, str),
            ConfigValueType.INTEGER: isinstance(value, int) and not isinstance(value, bool),
            ConfigValueType.NUMBER: isinstance(value, int | float) and not isinstance(value, bool),
            ConfigValueType.BOOLEAN: isinstance(value, bool),
        }[self.value_type]
        if not valid:
            raise ConfigTypeError(f"{self.name} must be {self.value_type.value}")
        if isinstance(value, int | float) and not isinstance(value, bool):
            if self.minimum is not None and value < self.minimum:
                raise ConfigTypeError(f"{self.name} must be >= {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ConfigTypeError(f"{self.name} must be <= {self.maximum}")
        if self.choices and value not in self.choices:
            raise ConfigTypeError(f"{self.name} must be one of the declared choices")


@dataclass(frozen=True, slots=True)
class ConfigSchema:
    schema_version: str
    fields: tuple[ConfigField, ...]

    def __post_init__(self) -> None:
        if not self.schema_version.strip():
            raise ValueError("schema_version must not be empty")
        names = [item.name for item in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("config field names must be unique")

    @property
    def by_name(self) -> Mapping[str, ConfigField]:
        return MappingProxyType({item.name: item for item in self.fields})


@dataclass(frozen=True, slots=True)
class FrozenConfig:
    schema_version: str
    values: Mapping[str, JsonValue]
    canonical_sha256: str
    layer_sha256: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "layer_sha256", MappingProxyType(dict(self.layer_sha256)))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "values": dict(self.values),
            "canonical_sha256": self.canonical_sha256,
            "layer_sha256": dict(self.layer_sha256),
        }


class ConfigResolver:
    """Resolve defaults -> experiment -> contestant -> run overrides."""

    def __init__(self, schema: ConfigSchema) -> None:
        self.schema = schema

    def resolve(
        self,
        *,
        defaults: Mapping[str, object] | None = None,
        experiment: Mapping[str, object] | None = None,
        contestant: Mapping[str, object] | None = None,
        run_overrides: Mapping[str, object] | None = None,
    ) -> FrozenConfig:
        fields = self.schema.by_name
        values: dict[str, JsonValue] = {
            item.name: item.default
            for item in self.schema.fields
            if item.default is not None and not item.secret
        }
        layer_hashes: dict[str, str] = {}
        layers = (
            ("defaults", defaults or {}),
            ("experiment", experiment or {}),
            ("contestant", contestant or {}),
            ("run_overrides", run_overrides or {}),
        )
        for layer_name, raw_layer in layers:
            layer = to_json_value(raw_layer)
            if not isinstance(layer, dict):
                raise ConfigTypeError(f"{layer_name} must be an object")
            layer_hashes[layer_name] = content_sha256(layer)
            unknown = set(layer) - set(fields)
            if unknown:
                raise UnknownConfigKeyError(
                    f"unknown keys in {layer_name}: {', '.join(sorted(unknown))}"
                )
            for name, value in layer.items():
                field_spec = fields[name]
                if field_spec.secret:
                    raise SecretInSnapshotError(f"secret field cannot enter snapshot: {name}")
                field_spec.validate(value)
                values[name] = value
        missing = [
            item.name for item in self.schema.fields if item.required and item.name not in values
        ]
        if missing:
            raise ConfigError(f"missing required fields: {', '.join(sorted(missing))}")
        snapshot = {"schema_version": self.schema.schema_version, "values": values}
        return FrozenConfig(
            schema_version=self.schema.schema_version,
            values=MappingProxyType(dict(sorted(values.items()))),
            canonical_sha256=content_sha256(snapshot),
            layer_sha256=MappingProxyType(layer_hashes),
        )
