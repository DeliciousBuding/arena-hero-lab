"""Canonical serialization shared by deterministic Lab artifacts."""

from __future__ import annotations

import decimal
import hashlib
import json
import math
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


def quantize_float(value: float, *, significant_digits: int = 12) -> float:
    """Round a finite float to ``significant_digits`` in a platform-independent way.

    ``repr(value)`` is CPython's shortest round-trip representation, which is
    deterministic on every platform, and the rounding is performed with
    :class:`decimal.Decimal` (exact) using IEEE ``ROUND_HALF_EVEN``. The returned
    binary64 value is therefore identical across operating systems and libm
    implementations (``math.exp``/``log``/``sqrt`` can differ by one ULP between
    MSVC CRT and glibc). This makes the value safe to include in content
    addresses that must be reproducible across Windows and Linux.

    Non-finite values and zero are returned unchanged; ``-0.0`` is normalized to
    ``0.0`` so the canonical form has no sign ambiguity.
    """

    if not math.isfinite(value) or value == 0.0:
        return 0.0 if value == 0.0 else value
    decimal_value = decimal.Decimal(repr(value))
    quantum = decimal.Decimal(1).scaleb(decimal_value.adjusted() - significant_digits + 1)
    quantized = decimal_value.quantize(quantum, rounding=decimal.ROUND_HALF_EVEN)
    return float(quantized)


def _quantize_json_value(value: object, *, significant_digits: int) -> object:
    """Recursively narrow JSON data while quantizing every float leaf."""

    if isinstance(value, float):
        return quantize_float(value, significant_digits=significant_digits)
    if isinstance(value, Mapping):
        return {
            str(key): _quantize_json_value(item, significant_digits=significant_digits)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_quantize_json_value(item, significant_digits=significant_digits) for item in value]
    return value


def quantized_content_sha256(value: object, *, significant_digits: int = 12) -> str:
    """Return SHA-256 over canonical JSON with every float quantized first.

    Use this to content-address evidence that contains solver or statistics
    intermediate floats: ULP-level libm drift does not change the digest, while
    any semantic change (status, identity, or a numerically meaningful value
    change) still does. The quantization itself is platform-independent, so the
    digest is reproducible across Windows and Linux.
    """

    return content_sha256(_quantize_json_value(value, significant_digits=significant_digits))


def to_json_value(value: object) -> JsonValue:
    """Narrow ordinary Python containers into the canonical JSON type."""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [to_json_value(item) for item in value]
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")
