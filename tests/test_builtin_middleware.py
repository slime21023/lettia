import asyncio

import pytest
from asgi_helpers import http_context

from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware import cors, request_id, timeout
from lettia.response import Response, TextResponse
from lettia.state import REQUEST_ID


@pytest.mark.asyncio
async def test_cors_middleware() -> None:
    async def handler(ctx: Context) -> Response:
        return TextResponse("OK")

    chain = cors(
        allow_origins=["https://example.com"],
        allow_methods=["GET", "POST"],
        allow_headers=["X-Custom"],
        allow_credentials=True,
    )(handler)
    resp_options = await chain(
        http_context(
            method="OPTIONS", path="/api", headers=[(b"origin", b"https://example.com")]
        )
    )
    assert resp_options.status_code == 204
    assert resp_options.headers["access-control-allow-origin"] == "https://example.com"
    assert resp_options.headers["access-control-allow-credentials"] == "true"
    assert "GET, POST" in resp_options.headers["access-control-allow-methods"]

    resp_get = await chain(
        http_context(path="/api", headers=[(b"origin", b"https://example.com")])
    )
    assert resp_get.status_code == 200
    assert resp_get.headers["access-control-allow-origin"] == "https://example.com"


@pytest.mark.asyncio
async def test_request_id_middleware() -> None:
    async def handler(ctx: Context) -> Response:
        return TextResponse(f"ID: {ctx.state.require(REQUEST_ID)}")

    chain = request_id()(handler)
    response = await chain(http_context())
    generated_id = response.headers["x-request-id"]
    assert len(generated_id) > 10

    existing = http_context(headers=[(b"x-request-id", b"custom-req-id-123")])
    response = await chain(existing)
    assert response.headers["x-request-id"] == "custom-req-id-123"
    assert existing.state.require(REQUEST_ID) == "custom-req-id-123"


@pytest.mark.asyncio
async def test_timeout_middleware() -> None:
    async def slow_handler(ctx: Context) -> Response:
        await asyncio.sleep(0.1)
        return TextResponse("Slow OK")

    with pytest.raises(HTTPException) as exc_info:
        await timeout(seconds=0.02)(slow_handler)(http_context(path="/slow"))
    assert exc_info.value.status_code == 504
