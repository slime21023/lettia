import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia.state import StateKey
from tests.support.asgi import (
    http_context,
)


@pytest.mark.contract("STATE-STORE")
@given(
    operations=st.lists(
        st.tuples(
            st.sampled_from(["set", "get", "discard"]), st.integers(0, 3), st.integers()
        ),
        max_size=50,
    )
)
def test_state_operations_match_identity_key_model(
    operations: list[tuple[str, int, int]],
) -> None:
    keys = [StateKey[int]("same-name") for _ in range(4)]
    ctx, other = http_context(), http_context()
    expected: dict[int, int] = {}
    for operation, index, value in operations:
        key = keys[index]
        if operation == "set":
            ctx.state.set(key, value)
            expected[index] = value
        elif operation == "discard":
            ctx.state.discard(key)
            expected.pop(index, None)
        assert ctx.state.get(key) == expected.get(index)
        assert other.state.get(key) is None
        if index in expected:
            assert ctx.state.require(key) == expected[index]
        else:
            with pytest.raises(KeyError):
                ctx.state.require(key)


@pytest.mark.contract("STATE-STORE")
def test_state_store_has_no_dictionary_style_api() -> None:
    with pytest.raises(AttributeError):
        object.__getattribute__(http_context().state, "__getitem__")
