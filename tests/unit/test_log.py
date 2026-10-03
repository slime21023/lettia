import pytest

from lettia.context import Context
from lettia.middleware.logger import request_logger
from lettia.response import Response, TextResponse
from tests.support.asgi import http_context


@pytest.mark.contract("MW-LOG")
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
