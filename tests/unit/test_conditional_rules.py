import pytest
from hypothesis import given
from hypothesis import strategies as st


@pytest.mark.contract("STATIC-CONDITIONAL")
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("validator", [None, '"tag"', ' W/"tag" ', '"old"', "date", ""])
def test_range_selection_requires_get_and_matching_strong_validator(
    method: str,
    validator: str | None,
) -> None:
    from lettia._conditional import select_byte_range

    expected = (1, 3) if method == "GET" and validator in (None, '"tag"') else None
    assert select_byte_range(method, "bytes=1-3", validator, '"tag"', 5) == expected
    assert select_byte_range(method, "items=1-3", validator, '"tag"', 5) is None
    assert select_byte_range(method, None, validator, '"tag"', 5) is None


@pytest.mark.contract("STATIC-CONDITIONAL")
@pytest.mark.parametrize(
    "value,expected",
    [
        ("*", True),
        ('W/"tag", "other"', True),
        ('"other,tag"', False),
        ('"tag", invalid', False),
        ('"tag" garbage', False),
    ],
)
def test_etag_rule_uses_complete_list_and_weak_comparison(
    value: str, expected: bool
) -> None:
    from lettia._conditional import if_none_match

    assert if_none_match(value, '"tag"') is expected


@pytest.mark.contract("STATIC-CONDITIONAL")
@given(
    size=st.integers(1, 10000), start=st.integers(0, 9999), length=st.integers(1, 10000)
)
def test_range_rule_matches_python_slice(size: int, start: int, length: int) -> None:
    from lettia._conditional import byte_range

    if start >= size:
        with pytest.raises(ValueError):
            byte_range(f"bytes={start}-{start + length - 1}", size)
    else:
        first, last = byte_range(f"bytes={start}-{start + length - 1}", size)
        assert bytes(size)[first : last + 1] == bytes(size)[start : start + length]
        assert first == start
        assert last == min(size, start + length) - 1


@pytest.mark.contract("STATIC-CONDITIONAL")
@pytest.mark.parametrize("size", [0, 5])
@pytest.mark.parametrize(
    "value",
    ["bytes=999-", "bytes=x-y", "bytes=0-1,3-4", "bytes=-0", "bytes=-", "bytes=4-1"],
)
def test_range_invalid_partitions_raise_value_error(value: str, size: int) -> None:
    from lettia._conditional import byte_range

    with pytest.raises(ValueError):
        byte_range(value, size)
