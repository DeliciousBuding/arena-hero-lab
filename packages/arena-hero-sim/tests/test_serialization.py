from arena_hero_sim import (
    canonical_json_bytes,
    content_sha256,
    quantize_float,
    quantized_content_sha256,
)


def test_canonical_json_is_order_independent_and_unicode_stable() -> None:
    left = {"z": [3, 2, 1], "name": "信标", "a": 1}
    right = {"a": 1, "name": "信标", "z": [3, 2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert content_sha256(left) == content_sha256(right)
    assert b"\\u" not in canonical_json_bytes(left)


def test_quantize_float_collapses_observed_libm_ulp_drift() -> None:
    # Traced values observed to differ by one ULP between Windows (MSVC CRT) and
    # Ubuntu (glibc) in the platform research evidence chain.
    assert quantize_float(25.48932902296051) == quantize_float(25.489329022960515)
    assert quantize_float(-1.1336850411579595) == quantize_float(-1.1336850411579604)
    assert quantize_float(-1.1336774787333486) == quantize_float(-1.1336774787333495)


def test_quantize_float_preserves_semantics_and_normalizes_zero() -> None:
    assert quantize_float(1.234567890123456) != quantize_float(1.234567891)
    assert quantize_float(1.0) == 1.0
    assert quantize_float(0.0) == 0.0
    assert quantize_float(-0.0) == 0.0


def test_quantized_content_sha256_ignores_ulp_drift_but_not_semantics() -> None:
    base = {"value": 25.48932902296051, "nested": [{"objective": -1.1336850411579595}]}
    drifted = {"value": 25.489329022960515, "nested": [{"objective": -1.1336850411579604}]}
    assert quantized_content_sha256(base) == quantized_content_sha256(drifted)

    semantically_tampered = {"value": 25.48932902296051, "nested": [{"objective": -1.133685042}]}
    assert quantized_content_sha256(base) != quantized_content_sha256(semantically_tampered)
