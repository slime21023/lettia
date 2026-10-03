import asyncio

import pytest

from lettia import App, Context
from lettia.asgi import HTTPSendEvent
from lettia.middleware import (
    timeout,
)
from lettia.response import Response, StreamResponse
from lettia.testing import TestClient
from tests.support.asgi import (
    http_receive,
    http_scope,
    response_body,
)


@pytest.mark.contract("APP-COMPLETE")
@pytest.mark.parametrize("phase", ["start", "body", "end"])
@pytest.mark.parametrize("failure", ["error", "cancel", "deadline"])
@pytest.mark.parametrize("accepted", [False, True])
async def test_app_transport_failure_never_retries_or_runs_background(
    phase: str,
    failure: str,
    accepted: bool,
) -> None:
    app = App()
    if failure == "deadline":
        app.use(timeout(0))
    attempts: list[HTTPSendEvent] = []
    delivered: list[HTTPSendEvent] = []
    background: list[str] = []

    class Stream:
        closed = False
        emitted = False

        def __aiter__(self) -> "Stream":
            return self

        async def __anext__(self) -> bytes:
            if self.emitted:
                raise StopAsyncIteration
            self.emitted = True
            return b"chunk"

        async def aclose(self) -> None:
            self.closed = True

    body = Stream()

    def route(ctx: Context) -> StreamResponse:
        ctx.add_background_task(background.append, "ran")
        return StreamResponse(body)

    app.add_route("GET", "/", route)

    async def send(message: HTTPSendEvent) -> None:
        attempts.append(message)
        event_phase = (
            "start"
            if message["type"] == "http.response.start"
            else "body"
            if message.get("more_body", False)
            else "end"
        )
        if event_phase != phase:
            delivered.append(message)
            return
        if accepted:
            delivered.append(message)
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "deadline":
            await asyncio.Event().wait()
        raise OSError("transport failed")

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await app(http_scope(), http_receive([]), send)
    else:
        await app(http_scope(), http_receive([]), send)

    assert len(attempts) == {"start": 1, "body": 2, "end": 3}[phase]
    assert sum(event["type"] == "http.response.start" for event in attempts) == 1
    assert len(delivered) == len(attempts) - (not accepted)
    assert not background
    assert body.closed


@pytest.mark.contract("APP-COMPLETE")
@pytest.mark.parametrize("failure", ["none", "source", "cleanup"])
@pytest.mark.parametrize("deadline", [False, True])
def test_completed_stream_awaits_cleanup_before_background(
    failure: str, deadline: bool
) -> None:
    app = App()
    if deadline:
        app.use(timeout(0))
    events: list[str] = []

    class Stream:
        emitted = False

        def __aiter__(self) -> "Stream":
            return self

        async def __anext__(self) -> bytes:
            if self.emitted:
                if failure == "source":
                    raise ValueError("source failed")
                raise StopAsyncIteration
            self.emitted = True
            return b"complete"

        async def aclose(self) -> None:
            events.append("closing")
            for _ in range(3):
                await asyncio.sleep(0)
            events.append("closed")
            if failure == "cleanup":
                raise ValueError("cleanup failed")

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        ctx.add_background_task(events.append, "background")
        return StreamResponse(Stream())

    result = TestClient(app).get("/")

    assert result.status_code == 200 and result.content == b"complete"
    assert events == ["closing", "closed"] + (
        ["background"] if failure == "none" else []
    )


@pytest.mark.contract("APP-COMPLETE")
@pytest.mark.parametrize("failure", ["handler", "writer", "fallback"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("streamed", [False, True])
def test_delivered_error_responses_run_background_tasks_once(
    failure: str,
    method: str,
    streamed: bool,
) -> None:
    app = App()
    events: list[str] = []

    @app.get("/")
    def route(ctx: Context) -> Response:
        ctx.add_background_task(events.append, "route task")
        if failure == "handler":
            raise ValueError("route error")
        return Response(headers={"bad name": "invalid"})

    async def audit() -> None:
        await asyncio.sleep(0)
        events.append("error task")

    class Stream:
        emitted = False

        def __aiter__(self) -> "Stream":
            return self

        async def __anext__(self) -> bytes:
            if self.emitted:
                raise StopAsyncIteration
            self.emitted = True
            return b"handled"

        async def aclose(self) -> None:
            events.append("closed")

    @app.error_handler
    def error_handler(ctx: Context, exc: Exception) -> Response:
        ctx.add_background_task(audit)
        headers = {"bad name": "invalid"} if failure == "fallback" else {}
        if streamed:
            return StreamResponse(Stream(), status_code=500, headers=headers)
        return Response(status_code=500, body=b"handled", headers=headers)

    result = TestClient(app).request(method, "/")
    assert result.status_code == 500
    assert bool(result.content) == (method == "GET")
    assert events == (["closed"] if streamed else []) + ["route task", "error task"]


@pytest.mark.contract("APP-COMPLETE")
async def test_pre_start_cleanup_failure_suppresses_background_after_fallback() -> None:
    app = App()
    events: list[str] = []
    sent: list[HTTPSendEvent] = []

    class Stream:
        def __aiter__(self) -> "Stream":
            return self

        async def __anext__(self) -> bytes:
            raise AssertionError("Invalid headers must not start the iterator")

        async def aclose(self) -> None:
            raise ValueError("cleanup failed")

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        ctx.add_background_task(events.append, "background")
        return StreamResponse(Stream(), headers={"bad name": "invalid"})

    async def send(message: HTTPSendEvent) -> None:
        sent.append(message)

    await app(http_scope(), http_receive([]), send)
    assert response_body(sent) == b"Internal Server Error" and not events
    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == 500


@pytest.mark.contract("APP-COMPLETE")
@pytest.mark.parametrize("phase", ["start", "body", "end"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_error_response_transport_failure_suppresses_background(
    phase: str,
    cancel: bool,
) -> None:
    app = App()
    events: list[str] = []
    attempts: list[HTTPSendEvent] = []

    class Stream:
        emitted = False

        def __aiter__(self) -> "Stream":
            return self

        async def __anext__(self) -> bytes:
            if self.emitted:
                raise StopAsyncIteration
            self.emitted = True
            return b"error"

        async def aclose(self) -> None:
            events.append("closed")

    @app.get("/")
    def route(ctx: Context) -> Response:
        return Response(headers={"bad name": "invalid"})

    @app.error_handler
    def error_handler(ctx: Context, exc: Exception) -> Response:
        ctx.add_background_task(events.append, "background")
        return StreamResponse(Stream(), status_code=500)

    async def send(message: HTTPSendEvent) -> None:
        attempts.append(message)
        current = (
            "start"
            if message["type"] == "http.response.start"
            else "body"
            if message.get("more_body")
            else "end"
        )
        if current == phase:
            if cancel:
                raise asyncio.CancelledError
            raise OSError("transport failed")

    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await app(http_scope(), http_receive([]), send)
    else:
        await app(http_scope(), http_receive([]), send)
    assert events == ["closed"]
    assert len(attempts) == {"start": 1, "body": 2, "end": 3}[phase]
