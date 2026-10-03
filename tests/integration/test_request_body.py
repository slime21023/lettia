import asyncio
from collections.abc import AsyncGenerator

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia import App
from lettia.asgi import HTTPReceiveEvent, HTTPSendEvent
from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware.body_limit import body_limit
from lettia.response import Response, StreamResponse
from tests.support.asgi import (
    http_receive,
    http_scope,
    http_sender,
    response_body,
)


@pytest.mark.contract("REQ-BODY")
@given(limit=st.integers(0, 32))
async def test_route_limit_cannot_be_bypassed_by_global_body_cache(limit: int) -> None:
    app = App()
    app.use(body_limit(100))
    app.add_route("POST", "/", lambda ctx: "accepted", middlewares=[body_limit(limit)])
    sent: list[HTTPSendEvent] = []
    await app(
        http_scope(method="POST"),
        http_receive([{"type": "http.request", "body": b"x" * (limit + 1)}]),
        http_sender(sent),
    )
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


@pytest.mark.contract("REQ-BODY", "RESP-STREAM")
@pytest.mark.parametrize("declared", [False, True])
async def test_rejected_body_is_not_cached_by_stream_disconnect_monitor(
    declared: bool,
) -> None:
    app = App()
    app.use(body_limit(16))
    captured: list[Context] = []
    drained = asyncio.Event()
    release = asyncio.Event()
    messages: list[HTTPReceiveEvent] = [
        {"type": "http.request", "body": b"x" * 32, "more_body": True},
        {"type": "http.request", "body": b"y" * 1024 * 1024, "more_body": False},
    ]
    events = iter(messages)
    sent: list[HTTPSendEvent] = []

    @app.get("/")
    def route(ctx: Context) -> str:
        raise AssertionError("Rejected input reached the route")

    @app.error_handler
    def error_handler(ctx: Context, exc: Exception) -> Response:
        assert isinstance(exc, HTTPException) and exc.status_code == 413
        captured.append(ctx)

        async def chunks() -> AsyncGenerator[bytes, None]:
            yield b"rejected"
            await release.wait()

        return StreamResponse(chunks(), status_code=413)

    async def receive() -> HTTPReceiveEvent:
        message = next(events, None)
        if message is None:
            drained.set()
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}
        return message

    async def send(message: HTTPSendEvent) -> None:
        sent.append(message)

    scope = http_scope(headers=[(b"content-length", b"1048608")] if declared else [])
    task = asyncio.create_task(app(scope, receive, send))
    try:
        async with asyncio.timeout(2):
            await drained.wait()
            ctx = captured[0]
            # Inspect retention as well as the public rejection contract.
            assert ctx._body is None and not ctx._body_chunks
            for read in (ctx.body, ctx.text, ctx.json):
                with pytest.raises(HTTPException) as error:
                    await read()
                assert error.value.status_code == 413
            release.set()
            await task
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert response_body(sent) == b"rejected"


@pytest.mark.contract("REQ-BODY", "RESP-STREAM")
@pytest.mark.parametrize("read_in_stream", [False, True])
@pytest.mark.parametrize("error_stream", [False, True])
async def test_stream_monitor_preserves_request_body_and_normal_completion(
    read_in_stream: bool, error_stream: bool
) -> None:
    app = App()
    sent: list[HTTPSendEvent] = []
    background: list[str] = []
    monitor_active = False
    events: list[HTTPReceiveEvent] = [
        {"type": "http.request", "body": b"part1", "more_body": True},
        {"type": "http.request", "body": b"part2"},
    ]

    async def receive() -> HTTPReceiveEvent:
        nonlocal monitor_active
        await asyncio.sleep(0)
        if events:
            return events.pop(0)
        monitor_active = True
        try:
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}
        finally:
            monitor_active = False

    async def make_response(ctx: Context) -> StreamResponse:
        if not read_in_stream:
            assert await ctx.body() == b"part1part2"

        async def chunks() -> AsyncGenerator[bytes, None]:
            yield b"start:"
            await asyncio.sleep(0)
            yield await ctx.body()

        return StreamResponse(chunks())

    @app.get("/")
    async def route(ctx: Context) -> StreamResponse:
        ctx.add_background_task(background.append, "completed")
        if error_stream:
            raise ValueError("handled")
        return await make_response(ctx)

    if error_stream:

        @app.error_handler
        async def error_handler(ctx: Context, exc: Exception) -> Response:
            return await make_response(ctx)

    async def send(message: HTTPSendEvent) -> None:
        await asyncio.sleep(0)
        sent.append(message)

    await app(http_scope(), receive, send)

    assert response_body(sent) == b"start:part1part2"
    assert background == ["completed"] and not monitor_active


@pytest.mark.contract("REQ-BODY", "RESP-STREAM")
async def test_stream_validation_failure_preserves_body_for_error_handler() -> None:
    app = App()
    sent: list[HTTPSendEvent] = []

    async def chunks() -> AsyncGenerator[bytes, None]:
        yield b"unreachable"

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        return StreamResponse(chunks(), headers={"bad name": "value"})

    @app.error_handler
    async def error_handler(ctx: Context, exc: Exception) -> Response:
        return Response(status_code=500, body=await ctx.body())

    async def send(message: HTTPSendEvent) -> None:
        sent.append(message)

    await app(
        http_scope(),
        http_receive([{"type": "http.request", "body": b"original"}]),
        send,
    )

    assert response_body(sent) == b"original"
