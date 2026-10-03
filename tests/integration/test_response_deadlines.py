import asyncio
from collections.abc import AsyncGenerator

import pytest

from lettia import App
from lettia.asgi import HTTPReceiveEvent, HTTPSendEvent
from lettia.context import Context
from lettia.middleware.timeout import timeout
from lettia.response import StreamResponse
from tests.support.asgi import (
    http_receive,
    http_scope,
    http_sender,
)


@pytest.mark.contract("RESP-TIMEOUT")
@pytest.mark.asyncio
async def test_timeout_covers_response_stream_and_closes_generator() -> None:
    app = App()
    app.use(timeout(0.01))
    generator_closed = False

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal generator_closed
        try:
            yield b"first"
            await asyncio.Event().wait()
            yield b"second"
        finally:
            generator_closed = True

    async def stream_handler(ctx: Context) -> StreamResponse:
        return StreamResponse(stream())

    app.add_route("GET", "/stream", stream_handler)

    sent_messages: list[HTTPSendEvent] = []
    await app(
        http_scope(path="/stream"),
        http_receive([{"type": "http.request", "body": b""}]),
        http_sender(sent_messages),
    )

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 200
    assert sent_messages[1]["type"] == "http.response.body"
    assert sent_messages[1]["body"] == b"first"
    assert sent_messages[-1] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }
    assert generator_closed


@pytest.mark.contract("RESP-TIMEOUT")
@pytest.mark.parametrize("interrupt", ["disconnect", "cancel", "both"])
async def test_timeout_cleanup_survives_disconnect_and_repeated_cancellation(
    interrupt: str,
) -> None:
    app = App()
    app.use(timeout(0))
    closing = asyncio.Event()
    release = asyncio.Event()
    disconnect = asyncio.Event()
    events: list[str] = []
    sent: list[HTTPSendEvent] = []

    async def chunks() -> AsyncGenerator[bytes, None]:
        try:
            await asyncio.Event().wait()
            yield b"unreachable"
        finally:
            events.append("closing")
            closing.set()
            await release.wait()
            events.append("closed")

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        ctx.add_background_task(events.append, "background")
        return StreamResponse(chunks())

    async def receive() -> HTTPReceiveEvent:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: HTTPSendEvent) -> None:
        sent.append(message)

    task = asyncio.create_task(app(http_scope(), receive, send))
    try:
        async with asyncio.timeout(2):
            await closing.wait()
            if interrupt != "cancel":
                disconnect.set()
            # Allow the monitor to observe disconnect before repeated cancels.
            for _ in range(4):
                await asyncio.sleep(0)
            if interrupt != "disconnect":
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
            assert not task.done()
            release.set()
            if interrupt == "disconnect":
                await task
            else:
                with pytest.raises(asyncio.CancelledError):
                    await task
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert events == ["closing", "closed"]
    assert len(sent) == 1 and sent[0]["type"] == "http.response.start"
