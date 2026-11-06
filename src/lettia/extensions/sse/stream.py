"""Server-Sent Events as a contract response class and as a Core helper."""

import asyncio
import math
from collections.abc import AsyncIterator, Callable
from typing import ClassVar, override

import msgspec

from lettia.contract import StreamReply
from lettia.core import MutableHeaders, Response
from lettia.extensions.sse.event import ServerSentEvent, encode_event

_CONNECTED = b": connected\n\n"
_KEEP_ALIVE = b": keep-alive\n\n"
_IDLE = object()  # stands for "no event yet": a source may yield None
_MEDIA_TYPE = "text/event-stream"
_DEFAULT_HEADERS = (("cache-control", "no-cache"), ("x-accel-buffering", "no"))


def _validate_heartbeat(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(
            f"heartbeat_sec must be a number or None, got {type(value).__name__}"
        )
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"heartbeat_sec must be finite and > 0, got {value!r}")


class _EventFrames:
    """Wire frames of an event source; owns the source until it is closed."""

    __slots__ = (
        "_check",
        "_closed",
        "_encode",
        "_heartbeat",
        "_pending",
        "_source",
        "_started",
    )

    def __init__(
        self,
        source: AsyncIterator[ServerSentEvent[object]],
        encode: Callable[[object], bytes],
        check: Callable[[object], None] | None,
        heartbeat_sec: float | None,
    ) -> None:
        self._source = source
        self._encode = encode
        self._check = check
        self._heartbeat = heartbeat_sec
        self._pending: asyncio.Future[ServerSentEvent[object]] | None = None
        self._started = False
        self._closed = False

    def __aiter__(self) -> "_EventFrames":
        return self

    async def __anext__(self) -> bytes:
        if self._closed:
            raise StopAsyncIteration
        if not self._started:
            self._started = True
            return _CONNECTED
        try:
            item = await self._next_item()
            if item is _IDLE:
                return _KEEP_ALIVE
            if self._check is not None:
                self._check(item)
            return encode_event(item, self._encode)
        except StopAsyncIteration:
            await self.aclose()
            raise
        except BaseException:
            await self.aclose()
            raise

    async def _next_item(self) -> object:
        """The next item, or ``_IDLE`` when a heartbeat is due."""
        if self._heartbeat is None:
            return await anext(self._source)
        if self._pending is None:
            # One pending read at a time: the producer is never cancelled by a
            # heartbeat and never runs ahead of the consumer.
            self._pending = asyncio.ensure_future(anext(self._source))
        done, _ = await asyncio.wait({self._pending}, timeout=self._heartbeat)
        if not done:
            return _IDLE
        pending, self._pending = self._pending, None
        return pending.result()

    async def aclose(self) -> None:
        """Release the source. Idempotent, also when never started."""
        if self._closed:
            return
        self._closed = True
        pending, self._pending = self._pending, None
        try:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                    await asyncio.wait({pending})
                if not pending.cancelled():
                    pending.exception()  # mark it retrieved
        finally:
            close = getattr(self._source, "aclose", None)
            if close is not None:
                await close()


def _frames(
    source: AsyncIterator[ServerSentEvent[object]],
    encode: Callable[[object], bytes],
    check: Callable[[object], None] | None,
    heartbeat_sec: float | None,
) -> AsyncIterator[bytes]:
    return _EventFrames(source, encode, check, heartbeat_sec)


class EventStream[T](StreamReply[ServerSentEvent[T]]):
    """A ``text/event-stream`` response; declare it as ``EventStream[Item]``.

    Each ``ServerSentEvent`` the body yields is sent as one event whose ``data``
    is JSON. A comment is sent first (so an idle stream still delivers its
    headers) and whenever the stream is idle for ``heartbeat_sec`` seconds
    (``None`` turns that off). Declare a subclass to change ``heartbeat_sec``.
    The request's DI scope and uploads are closed when the handler returns, so
    the source must own the resources it uses.
    """

    status = 200
    media_type = _MEDIA_TYPE
    heartbeat_sec: ClassVar[float | None] = 15.0
    default_headers = _DEFAULT_HEADERS
    watch_disconnect = True

    def __post_init__(self) -> None:
        _validate_heartbeat(self.heartbeat_sec)

    @override
    def frames(
        self,
        encode: Callable[[object], bytes],
        *,
        check: Callable[[object], None] | None = None,
    ) -> AsyncIterator[bytes]:
        source: AsyncIterator[ServerSentEvent[object]] = self.body  # type: ignore[assignment]
        return _frames(source, encode, check, self.heartbeat_sec)


def event_stream[T](
    events: AsyncIterator[ServerSentEvent[T]],
    *,
    heartbeat_sec: float | None = 15.0,
) -> Response:
    """A Core ``Response`` that streams ``events`` as Server-Sent Events."""
    _validate_heartbeat(heartbeat_sec)
    headers = MutableHeaders()
    headers.add("content-type", _MEDIA_TYPE)
    for name, value in _DEFAULT_HEADERS:
        headers.add(name, value)
    source: AsyncIterator[ServerSentEvent[object]] = events  # type: ignore[assignment]
    body = _frames(source, msgspec.json.Encoder().encode, None, heartbeat_sec)
    return Response(200, body, headers, watch_disconnect=True)
