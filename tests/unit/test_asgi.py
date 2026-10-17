from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from lettia.core import ClientDisconnect, Context, HTTPError, Response
from lettia.core.asgi import asgi_app

Message = MutableMapping[str, Any]


@dataclass
class Exchange:
    """What the application sent for one call, as a server would see it."""

    messages: list[Message] = field(default_factory=list)

    @property
    def status(self) -> int:
        return self.messages[0]["status"]

    @property
    def headers(self) -> dict[bytes, bytes]:
        return dict(self.messages[0]["headers"])

    @property
    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.messages[1:])


async def call(
    handler: Callable[[Context], Awaitable[Response]],
    *,
    method: str = "GET",
    path: str = "/",
    raw_path: bytes | None = b"",
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    incoming: list[Message] | None = None,
    max_body_size: int | None = 1024,
    send_error: Exception | None = None,
) -> Exchange:
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query_string,
        "headers": headers or [],
        "client": ("10.0.0.1", 4000),
    }
    if raw_path is not None:
        scope["raw_path"] = raw_path or path.encode()
    default: list[Message] = [{"type": "http.request", "body": b"", "more_body": False}]
    queue = list(incoming or default)
    exchange = Exchange()

    async def receive() -> Message:
        return queue.pop(0)

    async def send(message: Message) -> None:
        if send_error is not None and message["type"] == "http.response.body":
            raise send_error
        exchange.messages.append(message)

    await asgi_app(handler, max_body_size=max_body_size)(scope, receive, send)
    return exchange


async def ok(c: Context) -> Response:
    return c.text(200, "ok")


# --- response serialization ---------------------------------------------


async def test_asgi_sends_status_headers_and_body_with_content_length() -> None:
    out = await call(ok)

    assert out.status == 200
    assert out.headers[b"content-type"] == b"text/plain; charset=utf-8"
    assert out.headers[b"content-length"] == b"2"
    assert out.body == b"ok"
    assert out.messages[-1]["more_body"] is False


async def test_asgi_keeps_a_content_length_the_handler_already_set() -> None:
    async def handler(c: Context) -> Response:
        res = c.text(200, "ok")
        res.headers.set("content-length", "2")
        return res

    out = await call(handler)

    sent = [v for k, v in out.messages[0]["headers"] if k == b"content-length"]
    assert sent == [b"2"]


async def test_asgi_empty_body_gets_a_zero_content_length() -> None:
    async def handler(c: Context) -> Response:
        return Response(200)

    out = await call(handler)

    assert out.headers[b"content-length"] == b"0"
    assert out.body == b""


async def test_asgi_head_sends_headers_but_no_body() -> None:
    out = await call(ok, method="HEAD")

    assert out.headers[b"content-length"] == b"2"
    assert out.body == b""


@pytest.mark.parametrize("status", [204, 304])
async def test_asgi_bodiless_statuses_drop_the_body_and_content_length(
    status: int,
) -> None:
    async def handler(c: Context) -> Response:
        return Response(status, b"must not be sent")

    out = await call(handler)

    assert out.status == status
    assert out.body == b""
    assert b"content-length" not in out.headers


async def test_asgi_streams_chunks_without_content_length() -> None:
    async def handler(c: Context) -> Response:
        async def parts() -> AsyncIterator[bytes]:
            yield b"a"
            yield b"b"

        return c.stream(200, parts(), "text/plain")

    out = await call(handler)

    assert out.body == b"ab"
    assert b"content-length" not in out.headers
    assert [m["more_body"] for m in out.messages[1:]] == [True, True, False]


async def test_asgi_streaming_head_sends_no_chunks_and_closes_the_stream() -> None:
    closed: list[bool] = []

    async def handler(c: Context) -> Response:
        async def parts() -> AsyncIterator[bytes]:
            try:
                yield b"a"
            finally:
                closed.append(True)

        return c.stream(200, parts(), "text/plain")

    out = await call(handler, method="HEAD")

    assert out.body == b""
    assert closed in ([], [True])  # never started, or started and closed


async def test_asgi_closes_the_stream_when_sending_fails() -> None:
    closed: list[bool] = []

    async def handler(c: Context) -> Response:
        async def parts() -> AsyncIterator[bytes]:
            try:
                yield b"a"
                yield b"b"
            finally:
                closed.append(True)

        return c.stream(200, parts(), "text/plain")

    with pytest.raises(ConnectionResetError):
        await call(handler, send_error=ConnectionResetError())

    assert closed == [True]


# --- request construction -----------------------------------------------


async def test_asgi_builds_the_request_from_the_scope() -> None:
    seen: dict[str, object] = {}

    async def handler(c: Context) -> Response:
        r = c.request
        seen.update(
            method=r.method,
            path=r.path,
            raw_path=r.raw_path,
            query=dict(r.query),
            token=r.headers["x-token"],
            client=r.client,
        )
        return Response(204)

    await call(
        handler,
        method="POST",
        path="/a/b",
        raw_path=b"/a%2Fb",
        query_string=b"x=1",
        headers=[(b"x-token", b"abc")],
    )

    assert seen == {
        "method": "POST",
        "path": "/a/b",
        "raw_path": b"/a%2Fb",
        "query": {"x": "1"},
        "token": "abc",
        "client": ("10.0.0.1", 4000),
    }


async def test_asgi_raw_path_falls_back_to_the_decoded_path() -> None:
    seen: list[bytes] = []

    async def handler(c: Context) -> Response:
        seen.append(c.request.raw_path)
        return Response(204)

    await call(handler, path="/users/1", raw_path=None)

    assert seen == [b"/users/1"]


async def test_asgi_request_body_arrives_across_several_messages() -> None:
    async def echo(c: Context) -> Response:
        return c.bytes(200, await c.request.body())

    out = await call(
        echo,
        incoming=[
            {"type": "http.request", "body": b"he", "more_body": True},
            {"type": "http.request", "body": b"llo", "more_body": False},
        ],
    )

    assert out.body == b"hello"


# --- failures -----------------------------------------------------------


async def test_asgi_oversized_declared_body_surfaces_as_http_error_413() -> None:
    async def echo(c: Context) -> Response:
        return c.bytes(200, await c.request.body())

    with pytest.raises(HTTPError) as caught:
        await call(echo, headers=[(b"content-length", b"2000")], max_body_size=1024)

    assert caught.value.status == 413


async def test_asgi_client_disconnect_while_reading_sends_nothing() -> None:
    async def echo(c: Context) -> Response:
        return c.bytes(200, await c.request.body())

    exchange = Exchange()
    scope: Mapping[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": [],
    }
    queue: list[Message] = [
        {"type": "http.request", "body": b"par", "more_body": True},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return queue.pop(0)

    async def send(message: Message) -> None:
        exchange.messages.append(message)

    with pytest.raises(ClientDisconnect):
        await asgi_app(echo)(scope, receive, send)

    assert exchange.messages == []


async def test_asgi_non_http_scope_is_rejected() -> None:
    async def receive() -> Message:
        raise AssertionError

    async def send(message: Message) -> None:
        raise AssertionError

    with pytest.raises(NotImplementedError, match="websocket"):
        await asgi_app(ok)({"type": "websocket"}, receive, send)
