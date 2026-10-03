import asyncio

import pytest

from lettia import SESSION, App
from lettia.asgi import HTTPSendEvent
from lettia.context import Context
from lettia.middleware import (
    cors,
    request_id,
    session,
)
from lettia.middleware.timeout import timeout
from tests.support.asgi import (
    http_receive,
    http_scope,
    http_sender,
    response_body,
)


@pytest.mark.contract("MW-TIMEOUT")
@pytest.mark.asyncio
async def test_nested_timeouts_use_the_earliest_deadline() -> None:
    app = App()
    app.use(timeout(0), timeout(60))

    async def slow_handler(ctx: Context) -> str:
        await asyncio.Event().wait()
        return "done"

    app.add_route("GET", "/slow", slow_handler)

    sent_messages: list[HTTPSendEvent] = []
    await app(
        http_scope(path="/slow"),
        http_receive([{"type": "http.request", "body": b""}]),
        http_sender(sent_messages),
    )

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 504


@pytest.mark.contract("MW-TIMEOUT")
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("deadlines", [(0,), (0, 60), (60, 0), (0, 0)])
async def test_handler_timeout_can_send_error_through_yielding_transport(
    method: str, deadlines: tuple[int, ...]
) -> None:
    app = App()
    app.use(
        cors(allow_origins=["https://example.com"]), request_id(), session("secret")
    )
    app.use(*(timeout(seconds) for seconds in deadlines))

    @app.get("/")
    async def slow(ctx: Context) -> str:
        ctx.state.require(SESSION)["user"] = "alice"
        await asyncio.Event().wait()
        return "unreachable"

    sent: list[HTTPSendEvent] = []

    async def send(message: HTTPSendEvent) -> None:
        await asyncio.sleep(0)
        sent.append(message)

    await app(
        http_scope(method=method, headers=[(b"origin", b"https://example.com")]),
        http_receive([]),
        send,
    )

    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == 504
    headers = dict(start["headers"])
    assert headers[b"access-control-allow-origin"] == b"https://example.com"
    assert b"x-request-id" in headers and b"set-cookie" in headers
    assert bool(response_body(sent)) == (method == "GET")
