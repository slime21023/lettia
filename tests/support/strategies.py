"""Bounded inputs shared by behavioral property tests."""

import string

from hypothesis import strategies as st

from lettia.asgi import JSONValue

TEXT = st.text(st.characters(exclude_categories=("Cs",)), max_size=64)
SEGMENTS = st.text(string.ascii_letters + string.digits + "_-", min_size=1, max_size=20)
PAYLOADS = st.binary(max_size=4096)
JSON_SCALARS: st.SearchStrategy[JSONValue] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**63), max_value=2**63 - 1),
    st.floats(allow_nan=False, allow_infinity=False),
    TEXT,
)
JSON_VALUES: st.SearchStrategy[JSONValue] = st.recursive(
    JSON_SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=8), st.dictionaries(TEXT, children, max_size=8)
    ),
    max_leaves=32,
)
JSON_OBJECTS = st.dictionaries(TEXT, JSON_VALUES, max_size=8)
