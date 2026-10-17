from collections.abc import AsyncIterator

import pytest

from lettia.core import ClientDisconnect, HTTPError, Request, RequestHead
from lettia.core.request import DEFAULT_MAX_BODY_SIZE


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def exploding() -> AsyncIterator[bytes]:
    raise AssertionError("the body must not be read")
    yield b""  # pragma: no cover - makes this an async generator


async def disconnecting() -> AsyncIterator[bytes]:
    yield b"par"
    raise ClientDisconnect


def make(
    body: AsyncIterator[bytes] | None = None,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    max_body_size: int | None = DEFAULT_MAX_BODY_SIZE,
) -> Request:
    return Request(
        method="POST",
        path="/items",
        headers=headers,
        body=chunks() if body is None else body,
        max_body_size=max_body_size,
    )


def length(n: int | str) -> list[tuple[bytes, bytes]]:
    return [(b"content-length", str(n).encode())]


# --- head ---------------------------------------------------------------


def test_request_head_exposes_method_path_and_client() -> None:
    head = RequestHead(method="GET", path="/a b", client=("127.0.0.1", 5000))

    assert (head.method, head.path, head.client) == ("GET", "/a b", ("127.0.0.1", 5000))


def test_request_head_raw_path_defaults_to_the_encoded_path() -> None:
    assert RequestHead(method="GET", path="/users/1").raw_path == b"/users/1"


def test_request_head_keeps_the_raw_path_when_given() -> None:
    head = RequestHead(method="GET", path="/a/b", raw_path=b"/a%2Fb")

    assert head.raw_path == b"/a%2Fb"


def test_request_head_client_defaults_to_none() -> None:
    assert RequestHead(method="GET", path="/").client is None


def test_request_head_headers_are_case_insensitive_and_cached() -> None:
    head = RequestHead(method="GET", path="/", headers=[(b"x-token", b"abc")])

    assert head.headers["X-Token"] == "abc"
    assert head.headers is head.headers


def test_request_head_without_headers_has_empty_headers() -> None:
    assert len(RequestHead(method="GET", path="/").headers) == 0


def test_request_head_query_is_parsed_from_the_query_string() -> None:
    head = RequestHead(method="GET", path="/", query_string=b"a=1&a=2")

    assert head.query.get_all("a") == ["1", "2"]
    assert head.query is head.query


def test_request_head_cookies_merge_every_cookie_header() -> None:
    head = RequestHead(
        method="GET",
        path="/",
        headers=[(b"cookie", b"a=1"), (b"cookie", b"b=2; a=3")],
    )

    assert head.cookies == {"a": "1", "b": "2"}


# --- body ---------------------------------------------------------------


async def test_request_body_joins_the_chunks() -> None:
    assert await make(chunks(b"he", b"llo")).body() == b"hello"


async def test_request_body_of_an_empty_request_is_empty() -> None:
    assert await make().body() == b""


async def test_request_body_is_read_once_and_cached() -> None:
    request = make(chunks(b"abc"))

    first = await request.body()

    assert await request.body() == first == b"abc"


async def test_request_stream_yields_chunks_in_order() -> None:
    request = make(chunks(b"a", b"b", b"c"))

    assert [part async for part in request.stream()] == [b"a", b"b", b"c"]


async def test_request_stream_skips_empty_chunks() -> None:
    request = make(chunks(b"a", b"", b"b"))

    assert [part async for part in request.stream()] == [b"a", b"b"]


async def test_request_stream_after_body_is_an_error() -> None:
    request = make(chunks(b"abc"))
    await request.body()

    with pytest.raises(RuntimeError, match="body"):
        request.stream()


async def test_request_body_after_stream_is_an_error() -> None:
    request = make(chunks(b"abc"))
    request.stream()

    with pytest.raises(RuntimeError, match="stream"):
        await request.body()


def test_request_stream_twice_is_an_error() -> None:
    request = make(chunks(b"abc"))
    request.stream()

    with pytest.raises(RuntimeError, match="stream"):
        request.stream()


# --- body limit ---------------------------------------------------------


def test_default_body_limit_is_five_mebibytes() -> None:
    assert DEFAULT_MAX_BODY_SIZE == 5 * 1024 * 1024


async def test_request_body_over_declared_content_length_is_413_without_reading() -> (
    None
):
    request = make(exploding(), headers=length(11), max_body_size=10)

    with pytest.raises(HTTPError) as caught:
        await request.body()

    assert caught.value.status == 413


def test_request_stream_over_declared_content_length_is_413_when_called() -> None:
    request = make(exploding(), headers=length(11), max_body_size=10)

    with pytest.raises(HTTPError) as caught:
        request.stream()

    assert caught.value.status == 413


async def test_request_body_exactly_at_the_limit_is_accepted() -> None:
    request = make(chunks(b"x" * 10), headers=length(10), max_body_size=10)

    assert await request.body() == b"x" * 10


async def test_request_chunked_body_past_the_limit_is_413() -> None:
    request = make(chunks(b"x" * 6, b"x" * 5), max_body_size=10)

    with pytest.raises(HTTPError) as caught:
        await request.body()

    assert caught.value.status == 413


async def test_request_stream_past_the_limit_is_413_at_the_crossing_chunk() -> None:
    request = make(chunks(b"x" * 6, b"x" * 5), max_body_size=10)
    seen: list[bytes] = []

    with pytest.raises(HTTPError) as caught:
        async for part in request.stream():
            seen.append(part)

    assert caught.value.status == 413
    assert seen == [b"x" * 6]


async def test_request_without_a_limit_accepts_any_size() -> None:
    request = make(chunks(b"x" * 100), headers=length(100), max_body_size=None)

    assert len(await request.body()) == 100


@pytest.mark.parametrize("value", ["abc", "-1", "1.5", ""])
async def test_request_invalid_content_length_is_400(value: str) -> None:
    request = make(exploding(), headers=length(value))

    with pytest.raises(HTTPError) as caught:
        await request.body()

    assert caught.value.status == 400


async def test_request_body_propagates_client_disconnect() -> None:
    request = make(disconnecting())

    with pytest.raises(ClientDisconnect):
        await request.body()


async def test_request_stream_propagates_client_disconnect() -> None:
    request = make(disconnecting())

    with pytest.raises(ClientDisconnect):
        [part async for part in request.stream()]
