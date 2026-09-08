import asyncio
from collections.abc import AsyncGenerator

import pytest
from asgi_helpers import http_context, http_receive, http_scope, http_sender

from lettia import App
from lettia.asgi import HTTPSendEvent
from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware import Handler, build_chain
from lettia.middleware.body_limit import body_limit
from lettia.middleware.logger import request_logger
from lettia.middleware.recover import recover
from lettia.middleware.timeout import timeout
from lettia.response import Response, StreamResponse, TextResponse


@pytest.mark.asyncio
async def test_middleware_chain_execution_order() -> None:
    execution_order: list[str] = []

    def mw1(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            execution_order.append("mw1_start")
            res = await next_handler(ctx)
            execution_order.append("mw1_end")
            return res

        return handler

    def mw2(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            execution_order.append("mw2_start")
            res = await next_handler(ctx)
            execution_order.append("mw2_end")
            return res

        return handler

    async def target_handler(ctx: Context) -> Response:
        execution_order.append("target")
        return TextResponse("OK")

    chain = build_chain(target_handler, [mw1, mw2])
    ctx = http_context()
    await chain(ctx)

    assert execution_order == [
        "mw1_start",
        "mw2_start",
        "target",
        "mw2_end",
        "mw1_end",
    ]


@pytest.mark.asyncio
async def test_recover_middleware() -> None:
    async def failing_handler(ctx: Context) -> Response:
        raise ValueError("Something went wrong")

    recover_mw = recover()
    chain = recover_mw(failing_handler)

    ctx = http_context()
    res = await chain(ctx)

    assert res.status_code == 500
    assert res.body == b"Internal Server Error"


@pytest.mark.asyncio
async def test_logger_middleware() -> None:
    logged_messages: list[str] = []

    def mock_log(msg: str) -> None:
        logged_messages.append(msg)

    async def ok_handler(ctx: Context) -> Response:
        return TextResponse("OK", status_code=200)

    log_mw = request_logger(log_func=mock_log)
    chain = log_mw(ok_handler)

    ctx = http_context(path="/test-log")
    await chain(ctx)

    assert len(logged_messages) == 1
    assert "GET /test-log -> 200" in logged_messages[0]


@pytest.mark.asyncio
async def test_body_limit_middleware() -> None:
    async def handler(ctx: Context) -> Response:
        return TextResponse("OK")

    limit_mw = body_limit(max_bytes=10)
    chain = limit_mw(handler)

    # Test Header exceeding limit
    ctx_header_over = http_context(method="POST", headers=[(b"content-length", b"100")])

    with pytest.raises(HTTPException) as exc_info:
        await chain(ctx_header_over)
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_body_limit_rejects_invalid_content_length() -> None:
    async def handler(ctx: Context) -> Response:
        return TextResponse("OK")

    chain = body_limit(max_bytes=10)(handler)
    ctx = http_context(method="POST", headers=[(b"content-length", b"invalid")])

    with pytest.raises(HTTPException, match="must be an integer") as exc_info:
        await chain(ctx)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_timeout_covers_response_stream_and_closes_generator() -> None:
    app = App()
    app.use(timeout(0.01))
    generator_closed = False

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal generator_closed
        try:
            yield b"first"
            await asyncio.sleep(0.05)
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


@pytest.mark.asyncio
async def test_nested_timeouts_use_the_earliest_deadline() -> None:
    app = App()
    app.use(timeout(1), timeout(0.01))

    async def slow_handler(ctx: Context) -> str:
        await asyncio.sleep(0.05)
        return "done"

    app.add_route("GET", "/slow", slow_handler)

    sent_messages: list[HTTPSendEvent] = []
    await app(
        http_scope(path="/slow"),
        http_receive([{"type": "http.request", "body": b""}]),
        http_sender(sent_messages),
    )

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 504
