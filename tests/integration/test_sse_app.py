"""Typed event streams mounted on an App, driven in memory."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Protocol, override

import msgspec
import pytest

from lettia import App, Context, GuardSpec, HeaderName, Query, op, public
from lettia.contract import StreamReply
from lettia.core import Message, RequestHead, Scope, StartupError, Unauthorized
from lettia.extensions.sse import EventStream, ServerSentEvent
from lettia.testing import TestClient


class Tick(msgspec.Struct):
    n: int


class Viewer(msgspec.Struct):
    name: str


class CountReq(msgspec.Struct):
    count: Query[int] = 2


viewer = GuardSpec[Viewer]("viewer")


class Api(Protocol):
    @op.get("/ticks", guard=public)
    async def ticks(self, c: Context[None], req: CountReq) -> EventStream[Tick]: ...

    @op.get("/guarded", guard=viewer)
    async def guarded(self, c: Context[Viewer], req: None) -> EventStream[Tick]: ...


class Service(Api):
    def __init__(self, closed: list[str]) -> None:
        self.closed = closed

    async def source(
        self, count: int, *, wrong: bool = False
    ) -> AsyncIterator[ServerSentEvent[Tick]]:
        try:
            for n in range(1, count + 1):
                bad: object = "x" if wrong else n
                yield ServerSentEvent(Tick(bad), id=str(n))  # type: ignore[arg-type]
        finally:
            self.closed.append("source")

    @override
    async def ticks(self, c: Context[None], req: CountReq) -> EventStream[Tick]:
        return EventStream[Tick](self.source(req.count, wrong=req.count == 7))

    @override
    async def guarded(self, c: Context[Viewer], req: None) -> EventStream[Tick]:
        return EventStream[Tick](self.source(1))


async def verify_viewer(head: RequestHead) -> Viewer:
    if head.headers.get("authorization") is None:
        raise Unauthorized("sign in")
    return Viewer("v")


def make_app(closed: list[str], *, debug: bool = False) -> App:
    app = App(debug=debug, guards=[viewer.use(verify_viewer)])
    app.mount(Service(closed))
    return app


async def test_event_stream_operation_streams_typed_events_through_the_app() -> None:
    closed: list[str] = []

    async with TestClient(make_app(closed)) as client:
        res = await client.get("/ticks", params={"count": 2})

    assert res.status_code == 200
    assert res.headers["content-type"] == "text/event-stream"
    assert res.text == (
        ': connected\n\nid: 1\ndata: {"n":1}\n\nid: 2\ndata: {"n":2}\n\n'
    )
    assert closed == ["source"]


async def test_event_stream_operation_refuses_before_streaming() -> None:
    closed: list[str] = []

    async with TestClient(make_app(closed)) as client:
        unauthorized = await client.get("/guarded")
        invalid = await client.get("/ticks", params={"count": "many"})

    assert (unauthorized.status_code, invalid.status_code) == (401, 422)
    assert closed == []


async def test_event_stream_head_has_headers_and_no_body() -> None:
    closed: list[str] = []

    async with TestClient(make_app(closed)) as client:
        res = await client.head("/ticks")

    assert (res.status_code, res.content) == (200, b"")
    assert res.headers["content-type"] == "text/event-stream"


async def test_event_stream_debug_mode_stops_a_stream_on_a_mismatched_item() -> None:
    closed: list[str] = []
    messages: list[str] = []
    app = make_app(closed, debug=True)
    app.build()

    requests: list[Message] = [
        {"type": "http.request", "body": b"", "more_body": False}
    ]

    async def receive() -> Message:
        if requests:
            return requests.pop()
        await asyncio.Event().wait()  # a server answers only once the client leaves
        raise AssertionError("unreachable")

    async def send(message: Message) -> None:
        messages.append(str(message["type"]))

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/ticks",
        "query_string": b"count=7",
        "headers": [],
    }

    with pytest.raises(RuntimeError, match=r"Api\.ticks.*declared"):
        await app(scope, receive, send)

    assert messages == ["http.response.start", "http.response.body"]
    assert closed == ["source"]


def test_mount_reports_stream_reply_classes_that_cannot_stream() -> None:
    class Bare(StreamReply[Tick]):
        status = 200

    class BadApi(Protocol):
        @op.get("/bare", guard=public)
        async def bare(self, c: Context[None], req: None) -> Bare: ...

    class BadService(BadApi):
        @override
        async def bare(self, c: Context[None], req: None) -> Bare:
            raise NotImplementedError

    app = App()
    app.mount(BadService())

    with pytest.raises(StartupError, match="frames"):
        app.build()


def test_mount_collects_every_header_problem_of_a_stream_class() -> None:
    class Clashing[T](EventStream[T]):
        default_headers = (("content-type", "x"), ("x-a", "1"), ("X-A", "2"))

    class ClashApi(Protocol):
        @op.get("/clash", guard=public)
        async def clash(self, c: Context[None], req: None) -> Clashing[Tick]: ...

    class ClashService(ClashApi):
        @override
        async def clash(self, c: Context[None], req: None) -> Clashing[Tick]:
            raise NotImplementedError

    app = App()
    app.mount(ClashService())

    with pytest.raises(StartupError) as caught:
        app.build()

    assert "content-type" in str(caught.value)
    assert "x-a" in str(caught.value)


class _Source:
    """An event source that counts how often it was closed."""

    def __init__(self) -> None:
        self.closes = 0

    def __aiter__(self) -> "_Source":
        return self

    async def __anext__(self) -> ServerSentEvent[Tick]:
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closes += 1


async def test_event_stream_source_is_closed_when_the_reply_cannot_be_encoded() -> None:
    class Tagged[T](EventStream[T]):
        tag: Annotated[str, HeaderName("X-Tag")]

    class TaggedApi(Protocol):
        @op.get("/tagged", guard=public)
        async def tagged(self, c: Context[None], req: None) -> Tagged[Tick]: ...

    source = _Source()

    class TaggedService(TaggedApi):
        @override
        async def tagged(self, c: Context[None], req: None) -> Tagged[Tick]:
            # msgspec does not check types on construction: a required header
            # field can be None, and the reply then fails to become a response.
            return Tagged[Tick](source, None)  # type: ignore[arg-type]

    app = App()
    app.mount(TaggedService())
    async with TestClient(app) as http:
        res = await http.get("/tagged")

    assert res.status_code == 500
    assert source.closes == 1
