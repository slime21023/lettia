import asyncio
from collections.abc import AsyncGenerator

import pytest

from lettia.asgi import HTTPSendEvent
from lettia.response import (
    Response,
    ResponseWriter,
    StreamResponse,
    TextResponse,
)
from tests.support.asgi import http_sender, response_body


@pytest.mark.contract("RESP-WRITE")
async def test_concurrent_writer_calls_attempt_only_one_response_start() -> None:
    sent: list[HTTPSendEvent] = []
    consumed: list[bytes] = []

    async def chunks(value: bytes) -> AsyncGenerator[bytes, None]:
        consumed.append(value)
        yield value

    writer = ResponseWriter(http_sender(sent))
    await asyncio.gather(
        writer.write(StreamResponse(chunks(b"first"))),
        writer.write(StreamResponse(chunks(b"second"))),
    )
    assert consumed == [b"first"] and response_body(sent) == b"first"


@pytest.mark.contract("RESP-WRITE")
@pytest.mark.asyncio
async def test_response_writer_committed_safety() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    assert not writer.committed

    resp = TextResponse("OK", status_code=200)
    await writer.write(resp)

    assert writer.committed
    assert len(sent_messages) == 2


@pytest.mark.contract("RESP-WRITE")
@pytest.mark.asyncio
async def test_response_writer_preserves_explicit_content_type() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    await writer.write(Response(headers={"content-type": "application/custom"}))
    assert sent_messages[0]["type"] == "http.response.start"
    assert (
        sent_messages[0]["headers"].count((b"content-type", b"application/custom")) == 1
    )
    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 200

    # Write second time should be ignored because committed is True
    await writer.write(TextResponse("Duplicate", status_code=500))
    assert len(sent_messages) == 2
