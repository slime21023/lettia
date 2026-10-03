import asyncio
from collections.abc import AsyncIterator

import pytest

from lettia.asgi import HTTPSendEvent
from lettia.response import Response, ResponseWriter, StreamResponse
from tests.support.asgi import http_sender, response_body


@pytest.mark.contract("RESP-WRITE", "RESP-HEADERS")
async def test_delivery_replacement_query_and_finalizer_copy() -> None:
    sent: list[HTTPSendEvent] = []
    writer = ResponseWriter(http_sender(sent))
    original = Response(body=b"ok")

    def invalid(response: Response) -> None:
        response.headers["bad name"] = "invalid"

    with pytest.raises(ValueError):
        await writer._deliver(original, None, invalid)
    assert writer._can_replace_response and not writer.committed and not sent
    assert original.headers == {}
    assert await writer._deliver(original, None, lambda r: r.set_header("X-ID", "1"))
    assert not writer._can_replace_response and writer.committed
    assert original.headers == {} and response_body(sent) == b"ok"


@pytest.mark.contract("RESP-STREAM", "RESP-WRITE")
async def test_delivery_disconnect_waits_for_source_cleanup() -> None:
    sent: list[HTTPSendEvent] = []
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def source() -> AsyncIterator[bytes]:
        try:
            yield b"first"
            entered.set()
            await asyncio.Future[None]()
        finally:
            closed.set()

    writer = ResponseWriter(http_sender(sent))

    async def disconnect() -> None:
        await entered.wait()

    async with asyncio.timeout(2):
        completed = await writer._deliver(
            StreamResponse(source()), None, None, disconnect
        )
    assert not completed and closed.is_set()
    assert len(sent) == 2 and not writer._can_replace_response


@pytest.mark.contract("RESP-WRITE")
async def test_delivery_and_public_write_share_serial_ownership() -> None:
    sent: list[HTTPSendEvent] = []
    writer = ResponseWriter(http_sender(sent))
    async with asyncio.timeout(2):
        results = await asyncio.gather(
            writer._deliver(Response(body=b"first"), None, None),
            writer.write(Response(body=b"second")),
        )
    assert results == [True, None]
    assert response_body(sent) == b"first"
