import asyncio
from collections.abc import AsyncIterator

import pytest

from lettia.core import ClientDisconnect, HTTPError, Request, RequestHead
from lettia.core.hosts import HostPolicy
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


@pytest.mark.parametrize(
    ("path", "raw"),
    [
        ("/items/%41", b"/items/%2541"),
        ("/items/é", b"/items/%C3%A9"),
        ("/a b", b"/a%20b"),
        ("/keep/:@!$&'()*+,;=-._~", b"/keep/:@!$&'()*+,;=-._~"),
    ],
)
def test_request_head_without_raw_path_percent_encodes_the_decoded_path(
    path: str, raw: bytes
) -> None:
    assert RequestHead(method="GET", path=path).raw_path == raw


# --- released body ------------------------------------------------------


async def test_request_released_body_keeps_serving_cached_bytes() -> None:
    request = make(chunks(b"ab", b"c"))
    assert await request.body() == b"abc"

    await request._release_body()

    assert await request.body() == b"abc"


async def test_request_released_unread_body_cannot_be_read() -> None:
    request = make(chunks(b"abc"))

    await request._release_body()

    with pytest.raises(RuntimeError, match="released"):
        await request.body()
    with pytest.raises(RuntimeError, match="released"):
        request.stream()


async def test_request_release_stops_a_stream_that_was_already_handed_out() -> None:
    request = make(chunks(b"a", b"b"))
    stream = request.stream()
    assert await anext(stream) == b"a"

    await request._release_body()

    with pytest.raises(RuntimeError, match="released"):
        await anext(stream)


async def test_request_release_closes_the_source_once_and_is_idempotent() -> None:
    closed: list[bool] = []

    async def source() -> AsyncIterator[bytes]:
        try:
            yield b"a"
        finally:
            closed.append(True)

    request = make(source())
    await anext(request.stream())

    await request._release_body()
    await request._release_body()

    assert closed == [True]


async def test_request_release_fails_while_another_reader_holds_the_source() -> None:
    gate = asyncio.Event()

    async def source() -> AsyncIterator[bytes]:
        await gate.wait()
        yield b"late"

    request = make(source())
    reader = asyncio.ensure_future(request.body())
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError):
        await request._release_body()

    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader


# --- scheme, host and url ----------------------------------------------------


def head(
    host: str | None = "Example.com",
    *,
    scheme: str = "http",
    server: tuple[str, int | None] | None = ("10.0.0.5", 8000),
    raw_path: bytes = b"/a",
    query: bytes = b"",
    allowed: HostPolicy | None = None,
) -> RequestHead:
    headers = [] if host is None else [(b"host", host.encode())]
    return RequestHead(
        method="GET",
        path="/a",
        raw_path=raw_path,
        query_string=query,
        headers=headers,
        scheme=scheme,
        server=server,
        allowed_hosts=allowed,
    )


def test_request_scheme_defaults_to_http_and_follows_the_scope() -> None:
    assert RequestHead(method="GET", path="/").scheme == "http"
    assert head(scheme="https").scheme == "https"
    assert head(scheme="wss").scheme == "wss"


def test_request_host_is_the_host_header_in_lower_case_with_its_port() -> None:
    assert head("API.Example.com:8443").host == "api.example.com:8443"


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "user@example.com", "x:99999"])
def test_request_host_falls_back_to_the_server_when_the_header_is_malformed(
    bad: str,
) -> None:
    assert head(bad).host == "10.0.0.5:8000"


def test_request_host_without_a_header_is_the_server_address() -> None:
    assert head(None).host == "10.0.0.5:8000"
    assert head(None, server=("10.0.0.5", 80)).host == "10.0.0.5"
    assert head(None, scheme="https", server=("::1", 443)).host == "[::1]"
    assert head(None, scheme="https", server=("::1", 8443)).host == "[::1]:8443"


@pytest.mark.parametrize("server", [None, ("/run/app.sock", None)])
def test_request_host_without_header_or_tcp_server_is_localhost(
    server: tuple[str, int | None] | None,
) -> None:
    assert head(None, server=server).host == "localhost"


def test_request_url_keeps_the_raw_path_and_query_as_received() -> None:
    request = head(
        "example.com", scheme="https", raw_path=b"/a%2Fb/c", query=b"x=1&y=%20"
    )

    assert request.url == "https://example.com/a%2Fb/c?x=1&y=%20"
    assert request.base_url == "https://example.com/"


def test_request_url_without_a_query_has_no_question_mark() -> None:
    assert head("example.com").url == "http://example.com/a"


def test_request_host_is_rejected_when_it_is_not_in_the_allowed_hosts() -> None:
    request = head("evil.example", allowed=HostPolicy(["example.com"]))

    for read in (lambda: request.host, lambda: request.base_url, lambda: request.url):
        with pytest.raises(HTTPError) as caught:
            read()
        assert caught.value.status == 400


def test_request_host_passes_the_allowed_hosts() -> None:
    request = head("API.example.com:8443", allowed=HostPolicy(["*.example.com"]))

    assert request.host == "api.example.com:8443"
    assert request.url == "http://api.example.com:8443/a"


def test_request_allowed_hosts_judge_the_fallback_address_too() -> None:
    request = head(None, server=("10.0.0.5", 8000), allowed=HostPolicy(["example.com"]))

    with pytest.raises(HTTPError):
        assert request.host
