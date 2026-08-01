from typing import Any

import pytest

from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware import Handler, build_chain
from lettia.middleware.body_limit import body_limit
from lettia.middleware.logger import request_logger
from lettia.middleware.recover import recover
from lettia.response import TextResponse


@pytest.mark.asyncio
async def test_middleware_chain_execution_order() -> None:
    execution_order: list[str] = []

    def mw1(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
            execution_order.append("mw1_start")
            res = await next_handler(ctx)
            execution_order.append("mw1_end")
            return res

        return handler

    def mw2(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
            execution_order.append("mw2_start")
            res = await next_handler(ctx)
            execution_order.append("mw2_end")
            return res

        return handler

    async def target_handler(ctx: Context) -> Any:
        execution_order.append("target")
        return TextResponse("OK")

    chain = build_chain(target_handler, [mw1, mw2])
    ctx = Context(scope={"type": "http"}, receive=None, send=None)
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
    async def failing_handler(ctx: Context) -> Any:
        raise ValueError("Something went wrong")

    recover_mw = recover()
    chain = recover_mw(failing_handler)

    ctx = Context(scope={"type": "http"}, receive=None, send=None)
    res = await chain(ctx)

    assert res.status_code == 500
    assert res.body == b"Internal Server Error"


@pytest.mark.asyncio
async def test_logger_middleware() -> None:
    logged_messages: list[str] = []

    def mock_log(msg: str) -> None:
        logged_messages.append(msg)

    async def ok_handler(ctx: Context) -> Any:
        return TextResponse("OK", status_code=200)

    log_mw = request_logger(log_func=mock_log)
    chain = log_mw(ok_handler)

    ctx = Context(
        scope={"type": "http", "method": "GET", "path": "/test-log"},
        receive=None,
        send=None,
    )
    await chain(ctx)

    assert len(logged_messages) == 1
    assert "GET /test-log -> 200" in logged_messages[0]


@pytest.mark.asyncio
async def test_body_limit_middleware() -> None:
    async def handler(ctx: Context) -> Any:
        return TextResponse("OK")

    limit_mw = body_limit(max_bytes=10)
    chain = limit_mw(handler)

    # Test Header exceeding limit
    ctx_header_over = Context(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-length", b"100")],
        },
        receive=None,
        send=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chain(ctx_header_over)
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_body_limit_rejects_invalid_content_length() -> None:
    async def handler(ctx: Context) -> Any:
        return TextResponse("OK")

    chain = body_limit(max_bytes=10)(handler)
    ctx = Context(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-length", b"invalid")],
        },
        receive=None,
        send=None,
    )

    with pytest.raises(HTTPException, match="must be an integer") as exc_info:
        await chain(ctx)

    assert exc_info.value.status_code == 400
