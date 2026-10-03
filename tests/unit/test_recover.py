import pytest

from lettia.context import Context
from lettia.middleware.recover import recover
from lettia.response import Response
from tests.support.asgi import http_context


@pytest.mark.contract("MW-RECOVER")
@pytest.mark.asyncio
async def test_recover_middleware() -> None:
    async def failing_handler(ctx: Context) -> Response:
        raise ValueError("Something went wrong")

    recover_mw = recover()
    chain = recover_mw(failing_handler)

    ctx = http_context()
    with pytest.raises(ValueError, match="Something went wrong"):
        await chain(ctx)
