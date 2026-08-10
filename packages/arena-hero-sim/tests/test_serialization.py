from arena_hero_sim import canonical_json_bytes, content_sha256


def test_canonical_json_is_order_independent_and_unicode_stable() -> None:
    left = {"z": [3, 2, 1], "name": "信标", "a": 1}
    right = {"a": 1, "name": "信标", "z": [3, 2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert content_sha256(left) == content_sha256(right)
    assert b"\\u" not in canonical_json_bytes(left)
