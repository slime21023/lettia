import asyncio
from collections.abc import AsyncGenerator

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia.asgi import HTTPSendEvent
from lettia.response import (
    ResponseWriter,
    StreamResponse,
    TextResponse,
)
from tests.support.asgi import response_body


@pytest.mark.contract("RESP-TIMEOUT")
@given(after_headers=st.booleans())
async def test_response_deadline_only_finishes_after_commit(
    after_headers: bool,
) -> None:
    sent: list[HTTPSendEvent] = []
    closed = False

    async def send(message: HTTPSendEvent) -> None:
        if not after_headers:
            await asyncio.Event().wait()
        sent.append(message)

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            await asyncio.Event().wait()
            yield b"unreachable"
        finally:
            closed = True

    writer = ResponseWriter(send)
    response = StreamResponse(stream())
    deadline = asyncio.get_running_loop().time() - 1
    if after_headers:
        await writer.write(response, deadline=deadline)
        assert response_body(sent) == b""
        assert closed
    else:
        with pytest.raises(TimeoutError):
            await writer.write(response, deadline=deadline)
        assert not sent and not writer.committed
        await writer.write(TextResponse("must not retry"))
        assert not sent


@pytest.mark.contract("RESP-TIMEOUT")
@given(terminal=st.booleans())
async def test_deadline_during_send_never_retries_transport(terminal: bool) -> None:
    attempts: list[HTTPSendEvent] = []
    closed = False

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            if not terminal:
                yield b"chunk"
        finally:
            closed = True

    async def send(message: HTTPSendEvent) -> None:
        attempts.append(message)
        if len(attempts) == 2:
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await ResponseWriter(send).write(
            StreamResponse(stream()), deadline=asyncio.get_running_loop().time() - 1
        )
    assert len(attempts) == 2 and closed
