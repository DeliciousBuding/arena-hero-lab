"""Canonical serialization shared by deterministic Lab artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def canonical_json_bytes(value: object) -> bytes:
    """Serialize JSON data with stable keys, separators, Unicode, and no NaN values."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_sha256(value: object | bytes) -> str:
    """Return the lowercase SHA-256 digest of canonical JSON or raw bytes."""

    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def to_json_value(value: object) -> JsonValue:
    """Narrow ordinary Python containers into the canonical JSON type."""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [to_json_value(item) for item in value]
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")
