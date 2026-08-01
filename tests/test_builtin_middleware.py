import asyncio

import pytest

from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware import cors, request_id, timeout
from lettia.response import Response, TextResponse


@pytest.mark.asyncio
async def test_cors_middleware() -> None:
    async def handler(ctx: Context) -> Response:
        return TextResponse("OK")

    cors_mw = cors(
        allow_origins=["https://example.com"],
        allow_methods=["GET", "POST"],
        allow_headers=["X-Custom"],
        allow_credentials=True,
    )
    chain = cors_mw(handler)

    # 1. Pre-flight OPTIONS request
    ctx_options = Context(
        scope={"type": "http", "method": "OPTIONS", "path": "/api"},
        receive=None,
        send=None,
    )
    ctx_options._headers = {"origin": "https://example.com"}
    resp_options = await chain(ctx_options)

    assert resp_options.status_code == 204
    assert resp_options.headers["access-control-allow-origin"] == "https://example.com"
    assert resp_options.headers["access-control-allow-credentials"] == "true"
    assert "GET, POST" in resp_options.headers["access-control-allow-methods"]

    # 2. Regular GET request
    ctx_get = Context(
        scope={"type": "http", "method": "GET", "path": "/api"},
        receive=None,
        send=None,
    )
    ctx_get._headers = {"origin": "https://example.com"}
    resp_get = await chain(ctx_get)

    assert resp_get.status_code == 200
    assert resp_get.headers["access-control-allow-origin"] == "https://example.com"


@pytest.mark.asyncio
async def test_request_id_middleware() -> None:
    async def handler(ctx: Context) -> Response:
        req_id = ctx.state.get("request_id")
        return TextResponse(f"ID: {req_id}")

    req_id_mw = request_id()
    chain = req_id_mw(handler)

    # 1. Request without header (generates new UUID)
    ctx1 = Context(
        scope={"type": "http", "method": "GET", "path": "/"},
        receive=None,
        send=None,
    )
    resp1 = await chain(ctx1)
    assert resp1.status_code == 200
    generated_id = resp1.headers["x-request-id"]
    assert generated_id is not None
    assert len(generated_id) > 10

    # 2. Request with existing header (propagates ID)
    ctx2 = Context(
        scope={"type": "http", "method": "GET", "path": "/"},
        receive=None,
        send=None,
    )
    ctx2._headers = {"x-request-id": "custom-req-id-123"}
    resp2 = await chain(ctx2)
    assert resp2.headers["x-request-id"] == "custom-req-id-123"
    assert ctx2.state["request_id"] == "custom-req-id-123"


@pytest.mark.asyncio
async def test_timeout_middleware() -> None:
    async def slow_handler(ctx: Context) -> Response:
        await asyncio.sleep(0.1)
        return TextResponse("Slow OK")

    timeout_mw = timeout(seconds=0.02)
    chain = timeout_mw(slow_handler)

    ctx = Context(
        scope={"type": "http", "method": "GET", "path": "/slow"},
        receive=None,
        send=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chain(ctx)

    assert exc_info.value.status_code == 504
