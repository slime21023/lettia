import json
from urllib.parse import urlencode

import pytest
from asgi_helpers import http_context, http_receive, http_scope, http_sender
from hypothesis import example, given
from hypothesis import strategies as st
from strategies import JSON_VALUES, PAYLOADS, SEGMENTS, TEXT

from lettia import Context, JSONValue
from lettia.asgi import HTTPReceiveEvent, HTTPSendEvent
from lettia.errors import HTTPException


@given(
    payload=PAYLOADS,
    delta=st.sampled_from([-1, 0, 1]),
    cached=st.booleans(),
    chunk_size=st.integers(1, 64),
)
@example(payload=b"\x00", delta=-1, cached=True, chunk_size=1)
async def test_body_limit_is_independent_of_cache_and_chunking(
    payload: bytes, delta: int, cached: bool, chunk_size: int
) -> None:
    limit = max(0, len(payload) + delta)
    chunks = [
        payload[i : i + chunk_size] for i in range(0, len(payload), chunk_size)
    ] or [b""]
    events: list[HTTPReceiveEvent] = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ]
    sent: list[HTTPSendEvent] = []
    ctx = Context(http_scope(method="POST"), http_receive(events), http_sender(sent))
    if cached:
        assert await ctx.body() == payload
    if len(payload) > limit:
        with pytest.raises(HTTPException) as error:
            await ctx.body(max_bytes=limit)
        assert error.value.status_code == 413
    else:
        result = await ctx.body(max_bytes=limit)
        assert result == payload
        assert await ctx.body() is result


@given(value=JSON_VALUES)
async def test_json_request_round_trip(value: JSONValue) -> None:
    payload = json.dumps(value, allow_nan=False).encode()
    ctx = http_context(method="POST", body=payload)
    assert await ctx.json() == value
    assert await ctx.text() == payload.decode()
    assert await ctx.body() == payload


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


@given(status=st.integers(400, 599), detail=TEXT)
def test_abort_preserves_http_error(status: int, detail: str) -> None:
    with pytest.raises(HTTPException) as error:
        http_context().abort(status, detail)
    assert error.value.status_code == status and error.value.detail == detail


@given(payload=st.sampled_from([b"{", b"\xff", b'"unterminated']))
async def test_invalid_json_is_a_client_error(payload: bytes) -> None:
    with pytest.raises(HTTPException) as error:
        await http_context(body=payload).json()
    assert error.value.status_code == 400
