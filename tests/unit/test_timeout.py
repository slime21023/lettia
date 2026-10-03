import asyncio

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware import (
    timeout,
)
from lettia.response import Response, TextResponse
from tests.support.asgi import http_context


@pytest.mark.contract("MW-TIMEOUT")
@pytest.mark.asyncio
async def test_timeout_middleware() -> None:
    async def slow_handler(ctx: Context) -> Response:
        await asyncio.Event().wait()
        return TextResponse("Slow OK")

    with pytest.raises(HTTPException) as exc_info:
        await timeout(seconds=0.02)(slow_handler)(http_context(path="/slow"))
    assert exc_info.value.status_code == 504


@pytest.mark.contract("MW-TIMEOUT")
@given(seconds=st.integers(1, 100))
async def test_timeout_middleware_does_not_relabel_upstream_timeout(
    seconds: int,
) -> None:
    async def fail(ctx: Context) -> Response:
        raise TimeoutError("upstream")

    with pytest.raises(TimeoutError, match="upstream"):
        await timeout(seconds)(fail)(http_context())
