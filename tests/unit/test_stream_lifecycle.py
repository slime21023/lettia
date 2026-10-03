import asyncio
from collections.abc import AsyncGenerator
from contextvars import ContextVar, Token

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from lettia.asgi import HTTPSendEvent
from lettia.response import (
    ResponseWriter,
    StreamResponse,
)
from tests.support.asgi import http_sender, response_body


@pytest.mark.contract("RESP-STREAM")
@pytest.mark.parametrize("failure", ["none", "source", "error", "cancel", "timeout"])
async def test_stream_context_is_preserved_through_iteration_and_close(
    failure: str,
) -> None:
    state: ContextVar[str] = ContextVar("stream-state", default="request")
    owners: list[int] = []
    events: list[str] = []
    sent: list[HTTPSendEvent] = []

    async def chunks() -> AsyncGenerator[bytes, None]:
        assert state.get() == "request"
        token = state.set("stream")
        owners.append(id(asyncio.current_task()))
        try:
            yield b"first"
            if failure == "source":
                raise LookupError("source failed")
        finally:
            owners.append(id(asyncio.current_task()))
            await asyncio.sleep(0)
            state.reset(token)
            events.append("closed")

    async def send(message: HTTPSendEvent) -> None:
        if message["type"] == "http.response.body" and message.get("more_body"):
            assert state.get() == "stream"
            if failure == "error":
                raise OSError("transport failed")
            if failure == "cancel":
                raise asyncio.CancelledError
            if failure == "timeout":
                await asyncio.Event().wait()
        sent.append(message)

    writer = ResponseWriter(send)
    response = StreamResponse(chunks())
    if failure == "none":
        await writer.write(response)
        assert response_body(sent) == b"first"
    else:
        error = {
            "source": LookupError,
            "error": OSError,
            "cancel": asyncio.CancelledError,
            "timeout": TimeoutError,
        }[failure]
        deadline = asyncio.get_running_loop().time() if failure == "timeout" else None
        with pytest.raises(error):
            await writer.write(response, deadline=deadline)
    assert events == ["closed"] and len(set(owners)) == 1
    assert state.get() == "request"


@pytest.mark.contract("RESP-STREAM")
@pytest.mark.parametrize(
    ("head", "status"), [(False, 200), (True, 200), (False, 204), (False, 304)]
)
@pytest.mark.parametrize("invalid", [False, True])
async def test_iterable_acquisition_and_close_share_context(
    head: bool,
    status: int,
    invalid: bool,
) -> None:
    state: ContextVar[str] = ContextVar("resource-state", default="request")
    owners: list[int] = []
    closed: list[bool] = []

    class Stream:
        token: Token[str] | None = None

        def __aiter__(self) -> "Stream":
            self.token = state.set("acquired")
            owners.append(id(asyncio.current_task()))
            return self

        async def __anext__(self) -> bytes:
            assert not head and status not in (204, 304) and not invalid
            assert state.get() == "acquired"
            owners.append(id(asyncio.current_task()))
            raise StopAsyncIteration

        async def aclose(self) -> None:
            assert self.token is not None
            owners.append(id(asyncio.current_task()))
            await asyncio.sleep(0)
            state.reset(self.token)
            closed.append(True)

    sent: list[HTTPSendEvent] = []
    writer = ResponseWriter(http_sender(sent), head_only=head)
    response = StreamResponse(
        Stream(), status_code=status, headers={"bad name": "invalid"} if invalid else {}
    )
    if invalid:
        with pytest.raises(ValueError, match="header names"):
            await writer.write(response)
        assert not sent
    else:
        await writer.write(response)
        assert response_body(sent) == b""
    assert closed == [True] and len(set(owners)) == 1
    assert state.get() == "request"


@pytest.mark.contract("RESP-STREAM")
async def test_writer_repeated_cancellation_preserves_cleanup_context() -> None:
    state: ContextVar[str] = ContextVar("resource-state", default="request")
    closing = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []

    class Stream:
        token: Token[str] | None = None

        def __aiter__(self) -> "Stream":
            self.token = state.set("acquired")
            return self

        async def __anext__(self) -> bytes:
            raise StopAsyncIteration

        async def aclose(self) -> None:
            closing.set()
            await release.wait()
            assert self.token is not None
            state.reset(self.token)
            events.append("closed")

    sent: list[HTTPSendEvent] = []
    writer = ResponseWriter(http_sender(sent))
    writing = asyncio.create_task(writer.write(StreamResponse(Stream())))
    try:
        async with asyncio.timeout(2):
            await closing.wait()
            for _ in range(2):
                writing.cancel()
                await asyncio.sleep(0)
            assert not writing.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await writing
    finally:
        release.set()
        await asyncio.gather(writing, return_exceptions=True)
    assert events == ["closed"] and state.get() == "request"


@pytest.mark.contract("RESP-STREAM")
@given(
    chunks=st.lists(st.binary(max_size=64), max_size=20),
    failure=st.sampled_from(["none", "runtime", "timeout"]),
    with_deadline=st.booleans(),
)
@example(chunks=[b"first"], failure="timeout", with_deadline=False)
@example(chunks=[b"first"], failure="timeout", with_deadline=True)
async def test_stream_finishes_once_and_preserves_upstream_errors(
    chunks: list[bytes],
    failure: str,
    with_deadline: bool,
) -> None:
    closed = False

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            for chunk in chunks:
                yield chunk
            if failure == "runtime":
                raise RuntimeError("upstream")
            if failure == "timeout":
                raise TimeoutError("upstream")
        finally:
            closed = True

    sent: list[HTTPSendEvent] = []
    writer = ResponseWriter(http_sender(sent))
    response = StreamResponse(stream())
    deadline = asyncio.get_running_loop().time() + 60 if with_deadline else None
    if failure == "none":
        await writer.write(response, deadline=deadline)
    else:
        error = TimeoutError if failure == "timeout" else RuntimeError
        with pytest.raises(error, match="upstream"):
            await writer.write(response, deadline=deadline)
    assert closed
    assert response_body(sent) == b"".join(chunks)


@pytest.mark.contract("RESP-STREAM")
@given(
    chunks=st.lists(st.binary(max_size=64), min_size=1, max_size=10),
    cancel=st.booleans(),
)
async def test_stream_transport_failure_closes_without_retry(
    chunks: list[bytes],
    cancel: bool,
) -> None:
    closed = False
    attempts = 0

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            for chunk in chunks:
                yield chunk
        finally:
            closed = True

    async def send(message: HTTPSendEvent) -> None:
        nonlocal attempts
        attempts += 1
        if message["type"] == "http.response.body":
            if cancel:
                raise asyncio.CancelledError
            raise OSError("disconnected")

    iterator = stream()
    error = asyncio.CancelledError if cancel else OSError
    with pytest.raises(error):
        await ResponseWriter(send).write(StreamResponse(iterator))
    assert closed and attempts == 2
