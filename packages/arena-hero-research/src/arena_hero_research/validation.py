"""Shared validation helpers for versioned research execution artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from arena_hero_sim.serialization import JsonValue, to_json_value

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SENSITIVE_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


def require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def require_identifier(value: str, field_name: str) -> str:
    normalized = require_text(value, field_name)
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase portable identifier")
    return normalized


def require_sha256(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def freeze_public_metadata(
    value: Mapping[str, JsonValue], field_name: str
) -> Mapping[str, JsonValue]:
    """Validate JSON metadata recursively and reject credential-like keys."""

    normalized = to_json_value(dict(value))
    if not isinstance(normalized, dict):
        raise TypeError(f"{field_name} must be a mapping")

    def visit(item: JsonValue, path: tuple[str, ...]) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                lowered = key.lower().replace("-", "_")
                if any(part in lowered for part in _SENSITIVE_PARTS):
                    location = ".".join((*path, key))
                    raise ValueError(f"{field_name} contains sensitive key {location}")
                visit(nested, (*path, key))
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                visit(nested, (*path, str(index)))

    visit(normalized, ())
    return MappingProxyType(normalized)


def require_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be numeric")
    return float(value)


def require_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    return value


def require_json_mapping(value: object, field_name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return freeze_public_metadata(
        {str(key): to_json_value(item) for key, item in value.items()}, field_name
    )


def require_sequence(value: object, field_name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise TypeError(f"{field_name} must be a sequence")
    return value
