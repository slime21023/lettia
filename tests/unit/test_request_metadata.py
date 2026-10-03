from urllib.parse import urlencode

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia.errors import HTTPException
from tests.support.asgi import http_context
from tests.support.strategies import SEGMENTS, TEXT


@pytest.mark.contract("REQ-METADATA")
@given(pairs=st.lists(st.tuples(SEGMENTS, TEXT), max_size=20))
def test_query_parsing_preserves_repeated_values(pairs: list[tuple[str, str]]) -> None:
    ctx = http_context(query_string=urlencode(pairs).encode())
    expected: dict[str, list[str]] = {}
    for key, value in pairs:
        expected.setdefault(key, []).append(value)
    assert ctx.query_params == expected
    assert ctx.query_params is ctx.query_params
    for key, values in expected.items():
        assert ctx.query_param(key) == values[0]
    assert ctx.query_param("!missing", "default") == "default"


@pytest.mark.contract("REQ-METADATA")
@given(values=st.lists(SEGMENTS, min_size=1, max_size=10))
def test_headers_and_cookies_preserve_repeated_values(values: list[str]) -> None:
    headers = [(b"X-Test", value.encode()) for value in values]
    cookies = [(f"key{i}", value) for i, value in enumerate(values)]
    headers += [(b"cookie", f"{key}={value}".encode()) for key, value in cookies]
    ctx = http_context(headers=headers)
    assert ctx.header("x-TEST") == ", ".join(values)
    assert ctx.cookies == dict(cookies)
    assert ctx.headers is ctx.headers and ctx.cookies is ctx.cookies
    assert ctx.cookie("!missing", "default") == "default"


@pytest.mark.contract("REQ-METADATA")
@given(status=st.integers(400, 599), detail=TEXT)
def test_abort_preserves_http_error(status: int, detail: str) -> None:
    with pytest.raises(HTTPException) as error:
        http_context().abort(status, detail)
    assert error.value.status_code == status and error.value.detail == detail
