import pytest

from lettia.context import Context
from lettia.middleware import request_id
from lettia.response import Response, TextResponse
from lettia.state import REQUEST_ID
from tests.support.asgi import http_context


@pytest.mark.contract("MW-REQUEST-ID")
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
