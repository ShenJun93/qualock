from qualock.evidence.fingerprint import canonical_json, sha256_canonical


def test_dict_order_does_not_change_fingerprint() -> None:
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}
    assert canonical_json(left) == canonical_json(right)
    assert sha256_canonical(left) == sha256_canonical(right)


def test_list_order_changes_fingerprint() -> None:
    assert sha256_canonical([1, 2, 3]) != sha256_canonical([3, 2, 1])


def test_unicode_is_encoded_without_ascii_escape_instability() -> None:
    assert canonical_json({"task": "sửa lỗi"}) == '{"task":"sửa lỗi"}'.encode()
