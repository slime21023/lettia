import asyncio

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from lettia import Context
from lettia.asgi import (
    HTTPReceiveEvent,
    HTTPSendEvent,
)
from lettia.errors import HTTPException
from lettia.middleware.body_limit import body_limit
from lettia.response import Response, TextResponse
from tests.support.asgi import (
    http_context,
    http_receive,
    http_scope,
    http_sender,
)
from tests.support.strategies import PAYLOADS


@pytest.mark.contract("REQ-BODY")
@pytest.mark.parametrize("limit", [0, 2, 4])
async def test_body_rejection_is_sticky_for_later_unlimited_reads(limit: int) -> None:
    ctx = http_context(body=b"hello")
    with pytest.raises(HTTPException) as first:
        await ctx.body(max_bytes=limit)
    assert first.value.status_code == 413
    with pytest.raises(HTTPException) as later:
        await ctx.body()
    assert later.value.status_code == 413


@pytest.mark.contract("REQ-BODY")
async def test_cancelled_body_read_preserves_chunks_and_limit() -> None:
    first = asyncio.Event()
    release = asyncio.Event()
    count = 0

    async def receive() -> HTTPReceiveEvent:
        nonlocal count
        if count == 0:
            count += 1
            return {"type": "http.request", "body": b"ab", "more_body": True}
        first.set()
        await release.wait()
        count += 1
        return {"type": "http.request", "body": b"cd"}

    sent: list[HTTPSendEvent] = []
    ctx = Context(http_scope(), receive, http_sender(sent))
    reading = asyncio.create_task(ctx.body(max_bytes=4))
    await first.wait()
    reading.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reading
    release.set()

    assert await ctx.body() == b"abcd"
    assert count == 2
    with pytest.raises(HTTPException) as error:
        await ctx.body(max_bytes=3)
    assert error.value.status_code == 413


@pytest.mark.contract("REQ-BODY")
@given(
    payload=PAYLOADS,
    delta=st.sampled_from([-1, 0, 1]),
    cached=st.booleans(),
    chunk_size=st.integers(1, 64),
)
@example(payload=b"\x00", delta=-1, cached=True, chunk_size=1)
async def test_body_limit_is_independent_of_cache_and_chunking(
    payload: bytes, delta: int, cached: bool, chunk_size: int
) -> None:
    limit = max(0, len(payload) + delta)
    chunks = [
        payload[i : i + chunk_size] for i in range(0, len(payload), chunk_size)
    ] or [b""]
    events: list[HTTPReceiveEvent] = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ]
    sent: list[HTTPSendEvent] = []
    ctx = Context(http_scope(method="POST"), http_receive(events), http_sender(sent))
    if cached:
        assert await ctx.body() == payload
    if len(payload) > limit:
        with pytest.raises(HTTPException) as error:
            await ctx.body(max_bytes=limit)
        assert error.value.status_code == 413
    else:
        result = await ctx.body(max_bytes=limit)
        assert result == payload
        assert await ctx.body() is result


@pytest.mark.contract("REQ-BODY")
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


@pytest.mark.contract("REQ-BODY")
@pytest.mark.asyncio
async def test_body_limit_rejects_invalid_content_length() -> None:
    async def handler(ctx: Context) -> Response:
        return TextResponse("OK")

    chain = body_limit(max_bytes=10)(handler)
    ctx = http_context(method="POST", headers=[(b"content-length", b"invalid")])

    with pytest.raises(HTTPException, match="must be an integer") as exc_info:
        await chain(ctx)

    assert exc_info.value.status_code == 400


@pytest.mark.contract("REQ-BODY")
@pytest.mark.asyncio
async def test_body_disconnect_raises_http_error_instead_of_looping() -> None:
    sent: list[HTTPSendEvent] = []
    ctx = Context(
        scope=http_scope(),
        receive=http_receive([{"type": "http.disconnect"}]),
        send=http_sender(sent),
    )
    with pytest.raises(HTTPException, match="disconnected") as exc_info:
        await ctx.body()
    assert exc_info.value.status_code == 400
