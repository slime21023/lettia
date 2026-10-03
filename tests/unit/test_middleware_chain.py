import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia.context import Context
from lettia.middleware import (
    Handler,
    build_chain,
)
from lettia.response import Response, TextResponse
from tests.support.asgi import http_context


@pytest.mark.contract("MW-CHAIN")
@given(count=st.integers(0, 8))
async def test_generated_middleware_chain_preserves_nesting(count: int) -> None:
    events: list[tuple[str, int]] = []

    def middleware(index: int):
        def wrap(next_handler: Handler) -> Handler:
            async def handler(ctx: Context) -> Response:
                events.append(("enter", index))
                response = await next_handler(ctx)
                events.append(("exit", index))
                return response

            return handler

        return wrap

    async def target(ctx: Context) -> Response:
        return TextResponse("ok")

    await build_chain(target, [middleware(i) for i in range(count)])(http_context())
    assert events == [("enter", i) for i in range(count)] + [
        ("exit", i) for i in reversed(range(count))
    ]
