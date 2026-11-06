import asyncio
from collections.abc import AsyncIterator
from typing import Any, override

import msgspec
import pytest

from lettia.extensions.sse import EventStream, ServerSentEvent, event_stream


class Tick(msgspec.Struct):
    n: int


class Source:
    """An event source that records closes and reads."""

    def __init__(
        self,
        *items: object,
        idle: asyncio.Event | None = None,
        fail: bool = False,
    ) -> None:
        self.items = list(items)
        self.idle = idle
        self.fail = fail
        self.reads = 0
        self.closed = 0
        self.cancelled = 0

    def __aiter__(self) -> "Source":
        return self

    async def __anext__(self) -> Any:
        self.reads += 1
        if self.items:
            return self.items.pop(0)  # type: ignore[return-value]
        if self.idle is not None:
            try:
                await self.idle.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
            self.idle = None
            if self.items:
                return self.items.pop(0)  # type: ignore[return-value]
        if self.fail:
            raise ValueError("producer failed")
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed += 1


class BareSource:
    """An async iterator with no ``aclose`` hook."""

    def __init__(self, *items: ServerSentEvent[object]) -> None:
        self.items = list(items)

    def __aiter__(self) -> "BareSource":
        return self

    async def __anext__(self) -> ServerSentEvent[object]:
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


def stream_of(
    source: AsyncIterator[Any],
    *,
    heartbeat_sec: float | None = None,
) -> AsyncIterator[bytes]:
    body = event_stream(source, heartbeat_sec=heartbeat_sec).body
    assert body is not None and not isinstance(body, bytes)
    return body


async def drain(body: AsyncIterator[bytes]) -> list[bytes]:
    return [frame async for frame in body]


async def close(body: AsyncIterator[bytes]) -> None:
    await body.aclose()  # type: ignore[attr-defined]


def no_stray_tasks() -> bool:
    return asyncio.all_tasks() == {asyncio.current_task()}


# --- wire format --------------------------------------------------------


async def test_event_stream_sends_connected_comment_then_framed_events() -> None:
    event = ServerSentEvent("a\nb", event="token", id="7", retry=1000)

    frames = await drain(stream_of(Source(event, ServerSentEvent(None))))

    assert frames == [
        b": connected\n\n",
        b'event: token\nid: 7\nretry: 1000\ndata: "a\\nb"\n\n',
        b"data: null\n\n",
    ]


async def test_event_stream_prefixes_every_line_of_a_multiline_encoding() -> None:
    reply = EventStream[Tick](Source(ServerSentEvent(Tick(1))))

    frames = reply.frames(lambda _: b"one\r\ntwo\rthree\nfour")

    assert await drain(frames) == [
        b": connected\n\n",
        b"data: one\ndata: two\ndata: three\ndata: four\n\n",
    ]


async def test_event_stream_encodes_data_as_utf8_json() -> None:
    frames = await drain(stream_of(Source(ServerSentEvent("héllo ✓"))))

    assert frames[1] == 'data: "héllo ✓"\n\n'.encode()


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"event": "a\nb"}, ValueError),
        ({"event": "a\rb"}, ValueError),
        ({"id": "a\nb"}, ValueError),
        ({"id": "a\rb"}, ValueError),
        ({"id": "a\0b"}, ValueError),
        ({"retry": -1}, ValueError),
        ({"retry": True}, TypeError),
        ({"retry": "5"}, TypeError),
        ({"event": 1}, TypeError),
        ({"id": 1}, TypeError),
    ],
)
def test_server_sent_event_rejects_invalid_metadata(
    kwargs: dict[str, object], error: type[Exception]
) -> None:
    with pytest.raises(error):
        ServerSentEvent("x", **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "value"), [("event", "x\ny"), ("id", "x\0"), ("retry", -5)]
)
async def test_event_stream_rejects_metadata_changed_after_construction(
    name: str, value: object
) -> None:
    event = ServerSentEvent("x")
    setattr(event, name, value)
    source = Source(event)
    frames = stream_of(source)
    await anext(frames)

    with pytest.raises(ValueError, match="ServerSentEvent"):
        await anext(frames)

    assert source.closed == 1


@pytest.mark.parametrize("item", ["text", b"bytes", {"data": 1}, None])
async def test_event_stream_rejects_items_that_are_not_server_sent_events(
    item: object,
) -> None:
    source = Source(item)
    frames = stream_of(source)
    await anext(frames)

    with pytest.raises(TypeError, match="ServerSentEvent"):
        await anext(frames)

    assert source.closed == 1


# --- heartbeat configuration --------------------------------------------


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_event_stream_helper_rejects_unusable_heartbeats(value: float) -> None:
    with pytest.raises(ValueError, match="heartbeat_sec"):
        event_stream(Source(), heartbeat_sec=value)


def test_event_stream_helper_rejects_a_non_numeric_heartbeat() -> None:
    with pytest.raises(TypeError, match="heartbeat_sec"):
        event_stream(Source(), heartbeat_sec=True)


def test_event_stream_class_validates_its_heartbeat_when_created() -> None:
    class Broken[T](EventStream[T]):
        heartbeat_sec = 0

    with pytest.raises(ValueError, match="heartbeat_sec"):
        Broken[Tick](Source())  # type: ignore[arg-type]


# --- heartbeat behaviour ------------------------------------------------


async def test_event_stream_heartbeat_keeps_the_producer_and_reads_it_once() -> None:
    gate = asyncio.Event()
    source = Source(idle=gate)
    frames = stream_of(source, heartbeat_sec=0.01)
    assert await anext(frames) == b": connected\n\n"

    beats = [await anext(frames) for _ in range(3)]
    source.items.append(ServerSentEvent(Tick(5)))
    gate.set()
    nxt = await anext(frames)

    assert beats == [b": keep-alive\n\n"] * 3
    assert nxt == b'data: {"n":5}\n\n'
    assert (source.reads, source.cancelled) == (1, 0)
    await close(frames)
    assert no_stray_tasks()


async def test_event_stream_without_heartbeat_waits_on_the_source_directly() -> None:
    source = Source(idle=asyncio.Event())
    frames = stream_of(source, heartbeat_sec=None)
    await anext(frames)

    task = asyncio.ensure_future(anext(frames))
    await asyncio.sleep(0.02)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (source.cancelled, source.closed) == (1, 1)
    assert no_stray_tasks()


# --- cleanup ------------------------------------------------------------


@pytest.mark.parametrize("heartbeat", [None, 0.01])
async def test_event_stream_eof_closes_the_source_once(
    heartbeat: float | None,
) -> None:
    source = Source(ServerSentEvent("a"))
    frames = stream_of(source, heartbeat_sec=heartbeat)

    assert len(await drain(frames)) == 2

    assert source.closed == 1
    with pytest.raises(StopAsyncIteration):
        await anext(frames)
    await close(frames)
    assert source.closed == 1
    assert no_stray_tasks()


@pytest.mark.parametrize("heartbeat", [None, 0.01])
async def test_event_stream_source_error_propagates_and_closes_the_source(
    heartbeat: float | None,
) -> None:
    source = Source(fail=True)
    frames = stream_of(source, heartbeat_sec=heartbeat)
    await anext(frames)

    with pytest.raises(ValueError, match="producer failed"):
        await anext(frames)

    assert source.closed == 1
    with pytest.raises(StopAsyncIteration):  # an error never becomes a fake EOF
        await anext(frames)
    assert no_stray_tasks()


async def test_event_stream_cancel_while_idle_closes_the_source_and_the_read() -> None:
    source = Source(idle=asyncio.Event())
    frames = stream_of(source, heartbeat_sec=30)
    await anext(frames)
    task = asyncio.ensure_future(anext(frames))
    await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (source.cancelled, source.closed) == (1, 1)
    assert no_stray_tasks()


async def test_event_stream_aclose_before_start_closes_the_source_once() -> None:
    source = Source(ServerSentEvent("a"))
    frames = stream_of(source)

    await close(frames)
    await close(frames)

    assert source.closed == 1
    with pytest.raises(StopAsyncIteration):
        await anext(frames)


async def test_event_stream_aclose_cancels_an_unfinished_read() -> None:
    source = Source(idle=asyncio.Event())
    frames = stream_of(source, heartbeat_sec=0.01)
    await anext(frames)
    assert await anext(frames) == b": keep-alive\n\n"

    await close(frames)

    assert (source.cancelled, source.closed) == (1, 1)
    assert no_stray_tasks()


async def test_event_stream_aclose_retrieves_a_finished_read_error() -> None:
    gate = asyncio.Event()
    source = Source(idle=gate, fail=True)
    frames = stream_of(source, heartbeat_sec=0.01)
    await anext(frames)
    await anext(frames)
    gate.set()
    await asyncio.sleep(0.01)  # the pending read now holds the producer's error

    await close(frames)

    assert source.closed == 1
    assert no_stray_tasks()


async def test_event_stream_works_with_a_source_that_has_no_aclose() -> None:
    frames = stream_of(BareSource(ServerSentEvent("a")))

    assert len(await drain(frames)) == 2
    await close(frames)


async def test_event_stream_close_failure_surfaces_after_an_item_error() -> None:
    class BadClose(Source):
        @override
        async def aclose(self) -> None:
            raise OSError("close failed")

    frames = stream_of(BadClose(fail=True))
    await anext(frames)

    with pytest.raises(OSError, match="close failed") as caught:
        await anext(frames)

    assert isinstance(caught.value.__context__, ValueError)


# --- contract class and helper -------------------------------------------


async def test_event_stream_frames_run_the_debug_check_on_each_item() -> None:
    seen: list[object] = []
    event = ServerSentEvent(Tick(1))
    reply = EventStream[Tick](Source(event))

    frames = reply.frames(msgspec.json.Encoder().encode, check=seen.append)

    assert len(await drain(frames)) == 2
    assert seen == [event]


async def test_event_stream_frames_end_the_stream_when_the_check_fails() -> None:
    def check(item: object) -> None:
        raise RuntimeError("bad item")

    source = Source(ServerSentEvent(Tick(1)))
    reply = EventStream[Tick](source)
    frames = reply.frames(msgspec.json.Encoder().encode, check=check)
    await anext(frames)

    with pytest.raises(RuntimeError, match="bad item"):
        await anext(frames)

    assert source.closed == 1
    with pytest.raises(StopAsyncIteration):
        await anext(frames)


def test_event_stream_helper_returns_a_watched_event_stream_response() -> None:
    response = event_stream(Source())

    assert response.status == 200
    assert response.watch_disconnect is True
    assert response.headers["content-type"] == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
