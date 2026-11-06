import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from lettia.core import (
    ASGIApp,
    ClientDisconnect,
    Context,
    HTTPError,
    Message,
    Receive,
    Response,
    Scope,
    Send,
)
from lettia.core.asgi import asgi_app, run_lifespan


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
    send_error: BaseException | None = None,
    exchange: Exchange | None = None,
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
    if exchange is None:
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


# --- after_send ---------------------------------------------------------


class _CloseFailed(Exception):
    pass


class _Stop(BaseException):
    pass


async def _failing_close_stream() -> AsyncIterator[bytes]:
    try:
        yield b"a"
    finally:
        raise _CloseFailed


@pytest.mark.parametrize("kind", ["bytes", "head", "no-content", "stream"])
async def test_asgi_calls_after_send_once_the_response_is_complete(kind: str) -> None:
    exchange = Exchange()
    closed: list[bool] = []
    seen: list[tuple[bool, bool, bool]] = []
    sent: list[Response] = []

    async def handler(c: Context) -> Response:
        async def parts() -> AsyncIterator[bytes]:
            try:
                yield b"a"
                yield b"b"
            finally:
                closed.append(True)

        def after_send() -> None:
            seen.append(
                (
                    exchange.messages[-1]["more_body"] is False,
                    bool(closed) or kind != "stream",
                    sent[0].after_send is None,
                )
            )

        if kind == "stream":
            response = c.stream(200, parts(), "text/plain")
        elif kind == "no-content":
            response = Response(204)
        else:
            response = c.text(200, "ok")
        response.after_send = after_send
        sent.append(response)
        return response

    await call(handler, method="HEAD" if kind == "head" else "GET", exchange=exchange)

    assert seen == [(True, True, True)]


@pytest.mark.parametrize(
    "failure", ["send", "stream-iteration", "stream-close", "disconnect"]
)
async def test_asgi_skips_after_send_when_the_response_does_not_complete(
    failure: str,
) -> None:
    called: list[bool] = []

    async def handler(c: Context) -> Response:
        async def broken() -> AsyncIterator[bytes]:
            yield b"a"
            raise _CloseFailed

        if failure == "disconnect":
            raise ClientDisconnect
        if failure == "stream-iteration":
            response = c.stream(200, broken(), "text/plain")
        elif failure == "stream-close":
            response = c.stream(200, _failing_close_stream(), "text/plain")
        else:
            response = c.text(200, "ok")
        response.after_send = lambda: called.append(True)
        return response

    expected: type[BaseException] = {
        "send": ConnectionResetError,
        "disconnect": ClientDisconnect,
    }.get(failure, _CloseFailed)
    with pytest.raises(expected):
        await call(
            handler,
            send_error=ConnectionResetError() if failure == "send" else None,
        )

    assert called == []


async def test_asgi_skips_after_send_when_the_request_is_cancelled_while_sending() -> (
    None
):
    called: list[bool] = []
    sending = asyncio.Event()

    async def handler(c: Context) -> Response:
        response = c.text(200, "ok")
        response.after_send = lambda: called.append(True)
        return response

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            sending.set()
            await asyncio.Event().wait()

    scope: Mapping[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
    }
    request = asyncio.ensure_future(asgi_app(handler)(scope, receive, send))
    await sending.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert called == []


async def test_asgi_logs_a_failing_after_send_and_keeps_the_sent_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = RuntimeError("callback failed")

    def after_send() -> None:
        raise error

    async def handler(c: Context) -> Response:
        response = c.text(200, "ok")
        response.after_send = after_send
        return response

    with caplog.at_level(logging.ERROR, logger="lettia"):
        out = await call(handler)

    assert out.status == 200
    assert out.body == b"ok"
    [record] = caplog.records
    assert record.exc_info is not None
    assert record.exc_info[1] is error


async def test_asgi_does_not_swallow_a_base_exception_from_after_send() -> None:
    def after_send() -> None:
        raise _Stop

    async def handler(c: Context) -> Response:
        response = c.text(200, "ok")
        response.after_send = after_send
        return response

    with pytest.raises(_Stop):
        await call(handler)


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


async def test_asgi_builds_contexts_with_the_given_context_class() -> None:
    class CustomContext(Context):
        pass

    seen: list[type[Context]] = []

    async def handler(c: Context) -> Response:
        seen.append(type(c))
        return Response(204)

    scope: Mapping[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        return None

    await asgi_app(handler, context_class=CustomContext)(scope, receive, send)

    assert seen == [CustomContext]


async def test_run_lifespan_sends_complete_messages_when_start_and_stop_succeed() -> (
    None
):
    queue: list[Message] = [
        {"type": "lifespan.startup"},
        {"type": "lifespan.shutdown"},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return queue.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    async def ok_step() -> str | None:
        return None

    await run_lifespan(receive, send, start=ok_step, stop=ok_step)

    assert sent == [
        {"type": "lifespan.startup.complete"},
        {"type": "lifespan.shutdown.complete"},
    ]


async def test_run_lifespan_stops_and_sends_failed_when_start_returns_message() -> None:
    queue: list[Message] = [{"type": "lifespan.startup"}]
    sent: list[Message] = []

    async def receive() -> Message:
        return queue.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    async def fail_start() -> str | None:
        return "boom"

    async def never_stop() -> str | None:
        raise AssertionError

    await run_lifespan(receive, send, start=fail_start, stop=never_stop)

    assert sent == [{"type": "lifespan.startup.failed", "message": "boom"}]
    assert (ASGIApp, Receive, Scope, Send) is not None


# --- watched (disconnect-aware) responses -------------------------------

_SCOPE: dict[str, Any] = {
    "type": "http",
    "method": "GET",
    "path": "/",
    "query_string": b"",
    "headers": [],
}
_REQUEST_DONE: Message = {"type": "http.request", "body": b"", "more_body": False}


class Wire:
    """A server side whose receive blocks until the test feeds it a message."""

    def __init__(self, *, initial: list[Message] | None = None) -> None:
        self.incoming: asyncio.Queue[Message | Exception] = asyncio.Queue()
        for message in [_REQUEST_DONE] if initial is None else initial:
            self.incoming.put_nowait(message)
        self.sent: list[Message] = []
        self.start_error: BaseException | None = None
        self.final_error: BaseException | None = None
        self.final_gate: asyncio.Event | None = None
        self.final_reached = asyncio.Event()

    async def receive(self) -> Message:
        item = await self.incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start" and self.start_error:
            raise self.start_error
        if message["type"] == "http.response.body" and not message["more_body"]:
            self.final_reached.set()
            if self.final_gate is not None:
                await self.final_gate.wait()
            if self.final_error is not None:
                raise self.final_error
        self.sent.append(message)

    def run(
        self, handler: Callable[[Context], Awaitable[Response]]
    ) -> "asyncio.Task[None]":
        app = asgi_app(handler)
        return asyncio.ensure_future(app(_SCOPE, self.receive, self.send))


class Source:
    """A response body that records how often it was closed."""

    def __init__(self, *parts: bytes, then_idle: bool = False, fail: bool = False):
        self.parts = list(parts)
        self.then_idle = then_idle
        self.fail = fail
        self.closed = 0

    def __aiter__(self) -> "Source":
        return self

    async def __anext__(self) -> bytes:
        if self.parts:
            return self.parts.pop(0)
        if self.then_idle:
            await asyncio.Event().wait()
        if self.fail:
            raise ValueError("producer failed")
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed += 1


async def _settle(condition: Callable[[], bool]) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


def _watched(
    source: Source, after_send: list[str] | None = None
) -> Callable[[Context], Awaitable[Response]]:
    async def handler(c: Context) -> Response:
        callback = None if after_send is None else lambda: after_send.append("ran")
        return Response(200, source, after_send=callback, watch_disconnect=True)

    return handler


def _no_stray_tasks() -> bool:
    return asyncio.all_tasks() == {asyncio.current_task()}


async def test_asgi_watched_stream_completes_and_runs_after_send() -> None:
    wire, source = Wire(), Source(b"a", b"b")
    after: list[str] = []

    await wire.run(_watched(source, after))

    assert [m.get("body") for m in wire.sent[1:]] == [b"a", b"b", b""]
    assert (source.closed, after) == (1, ["ran"])
    assert _no_stray_tasks()


async def test_asgi_watched_stream_stops_on_disconnect_without_after_send() -> None:
    wire, source = Wire(), Source(b"a", then_idle=True)
    after: list[str] = []
    task = wire.run(_watched(source, after))
    await _settle(lambda: len(wire.sent) == 2)

    wire.incoming.put_nowait({"type": "http.disconnect"})

    with pytest.raises(ClientDisconnect):
        await task
    assert (source.closed, after) == (1, [])
    assert _no_stray_tasks()


async def test_asgi_watched_stream_disconnect_during_final_send_still_succeeds() -> (
    None
):
    wire, source = Wire(), Source(b"a")
    after: list[str] = []
    wire.final_gate = asyncio.Event()
    task = wire.run(_watched(source, after))
    await wire.final_reached.wait()

    wire.incoming.put_nowait({"type": "http.disconnect"})
    await _settle(lambda: wire.incoming.empty())
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done()
    wire.final_gate.set()
    await task

    assert (source.closed, after) == (1, ["ran"])
    assert _no_stray_tasks()


async def test_asgi_watched_stream_failed_final_send_skips_after_send() -> None:
    wire, source = Wire(), Source(b"a")
    after: list[str] = []
    wire.final_gate = asyncio.Event()
    wire.final_error = ConnectionResetError()
    task = wire.run(_watched(source, after))
    await wire.final_reached.wait()
    wire.incoming.put_nowait({"type": "http.disconnect"})
    for _ in range(5):
        await asyncio.sleep(0)

    wire.final_gate.set()

    with pytest.raises(ConnectionResetError):
        await task
    assert (source.closed, after) == (1, [])


async def test_asgi_watched_stream_cancel_propagates_and_stops_children() -> None:
    wire, source = Wire(), Source(b"a", then_idle=True)
    task = wire.run(_watched(source))
    await _settle(lambda: len(wire.sent) == 2)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.closed == 1
    assert _no_stray_tasks()


async def test_asgi_watched_stream_receive_failure_is_not_a_disconnect() -> None:
    wire, source = Wire(), Source(b"a", then_idle=True)
    after: list[str] = []
    task = wire.run(_watched(source, after))
    await _settle(lambda: len(wire.sent) == 2)

    wire.incoming.put_nowait(RuntimeError("receive broke"))

    with pytest.raises(RuntimeError, match="receive broke"):
        await task
    assert (source.closed, after) == (1, [])
    assert _no_stray_tasks()


async def test_asgi_watched_stream_producer_error_wins_over_disconnect() -> None:
    wire, source = Wire(), Source(b"a", fail=True)
    wire.incoming.put_nowait({"type": "http.disconnect"})

    with pytest.raises(ValueError, match="producer failed"):
        await wire.run(_watched(source))

    assert source.closed == 1


async def test_asgi_watched_stream_start_failure_closes_the_source() -> None:
    wire, source = Wire(), Source(b"a")
    wire.start_error = ConnectionResetError()

    with pytest.raises(ConnectionResetError):
        await wire.run(_watched(source))

    assert source.closed == 1
    assert _no_stray_tasks()


async def test_asgi_unwatched_stream_start_failure_closes_the_source() -> None:
    wire, source = Wire(), Source(b"a")
    wire.start_error = ConnectionResetError()

    async def handler(c: Context) -> Response:
        return Response(200, source)

    with pytest.raises(ConnectionResetError):
        await wire.run(handler)

    assert source.closed == 1


async def test_asgi_watched_head_response_sends_no_body_and_closes_the_source() -> None:
    wire, source = Wire(), Source(b"a")
    scope = {**_SCOPE, "method": "HEAD"}

    await asgi_app(_watched(source))(scope, wire.receive, wire.send)

    assert [m.get("body") for m in wire.sent[1:]] == [b""]
    assert source.closed == 1


async def test_asgi_watched_flag_on_a_bytes_response_sends_it_unwatched() -> None:
    wire = Wire(initial=[])

    async def handler(c: Context) -> Response:
        return Response(200, b"hi", watch_disconnect=True)

    await wire.run(handler)

    assert wire.sent[-1]["body"] == b"hi"


async def test_asgi_watched_stream_with_a_live_body_reader_fails_before_start() -> None:
    wire, source = Wire(initial=[]), Source(b"a")
    readers: list[asyncio.Future[bytes]] = []

    async def handler(c: Context) -> Response:
        readers.append(asyncio.ensure_future(c.request.body()))
        await asyncio.sleep(0)
        return Response(200, source, watch_disconnect=True)

    with pytest.raises(RuntimeError):
        await wire.run(handler)

    assert (wire.sent, source.closed) == ([], 1)
    readers[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await readers[0]


async def test_asgi_watched_stream_keeps_a_body_read_by_the_handler() -> None:
    wire, source = Wire(initial=[]), Source(b"x")
    wire.incoming.put_nowait(
        {"type": "http.request", "body": b"in", "more_body": False}
    )
    seen: list[bytes] = []

    async def handler(c: Context) -> Response:
        seen.append(await c.request.body())
        return Response(200, source, watch_disconnect=True)

    await wire.run(handler)

    assert (seen, wire.sent[-1]["body"]) == ([b"in"], b"")
