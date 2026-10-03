import asyncio
from collections.abc import AsyncGenerator

import pytest

from lettia import App, Context
from lettia.asgi import HTTPReceiveEvent, HTTPSendEvent
from lettia.response import StreamResponse
from tests.support.asgi import http_receive, http_scope


@pytest.mark.contract("RESP-STREAM")
@pytest.mark.parametrize(
    ("method", "status"), [("GET", 200), ("HEAD", 200), ("GET", 204), ("GET", 304)]
)
async def test_external_cancellation_during_cleanup_waits_and_propagates(
    method: str,
    status: int,
) -> None:
    app = App()
    closing = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []

    class Stream:
        def __aiter__(self) -> "Stream":
            return self

        async def __anext__(self) -> bytes:
            raise StopAsyncIteration

        async def aclose(self) -> None:
            closing.set()
            await release.wait()
            events.append("closed")

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        ctx.add_background_task(events.append, "background")
        return StreamResponse(Stream(), status_code=status)

    async def send(message: HTTPSendEvent) -> None:
        pass

    task = asyncio.create_task(app(http_scope(method=method), http_receive([]), send))
    try:
        async with asyncio.timeout(2):
            await closing.wait()
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert events == ["closed"]


@pytest.mark.contract("RESP-STREAM")
@pytest.mark.parametrize("after_chunk", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_idle_stream_disconnect_or_cancellation_closes_all_tasks(
    after_chunk: bool, cancel: bool
) -> None:
    app = App()
    waiting = asyncio.Event()
    disconnected = asyncio.Event()
    closed = False
    receiving = False
    background: list[str] = []
    sent: list[HTTPSendEvent] = []

    async def receive() -> HTTPReceiveEvent:
        nonlocal receiving
        receiving = True
        try:
            await disconnected.wait()
            return {"type": "http.disconnect"}
        finally:
            receiving = False

    async def chunks() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            if after_chunk:
                yield b"first"
            waiting.set()
            await asyncio.Event().wait()
        finally:
            closed = True

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        ctx.add_background_task(background.append, "completed")
        return StreamResponse(chunks())

    async def send(message: HTTPSendEvent) -> None:
        sent.append(message)

    task = asyncio.create_task(app(http_scope(), receive, send))
    try:
        async with asyncio.timeout(2):
            await waiting.wait()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                disconnected.set()
                await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert closed and not receiving and not background
    assert len(sent) == (2 if after_chunk else 1)


@pytest.mark.contract("RESP-STREAM")
@pytest.mark.parametrize("phase", ["start", "body", "end"])
async def test_disconnect_interrupts_blocked_send_without_retry(phase: str) -> None:
    app = App()
    waiting = asyncio.Event()
    attempts: list[HTTPSendEvent] = []
    background: list[str] = []

    async def chunks() -> AsyncGenerator[bytes, None]:
        yield b"first"

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        ctx.add_background_task(background.append, "completed")
        return StreamResponse(chunks())

    async def receive() -> HTTPReceiveEvent:
        await waiting.wait()
        return {"type": "http.disconnect"}

    async def send(message: HTTPSendEvent) -> None:
        attempts.append(message)
        current = (
            "start"
            if message["type"] == "http.response.start"
            else ("body" if message.get("more_body") else "end")
        )
        if current == phase:
            waiting.set()
            await asyncio.Event().wait()

    async with asyncio.timeout(2):
        await app(http_scope(), receive, send)

    assert len(attempts) == {"start": 1, "body": 2, "end": 3}[phase]
    assert not background
