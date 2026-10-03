"""Typed ASGI fixtures shared by tests.

The production interfaces deliberately require complete ASGI scopes and
protocol-specific receive/send callables.  These helpers keep tests just as
explicit without repeating transport boilerplate in every module.
"""

import asyncio
from collections.abc import Iterable

from lettia.asgi import (
    HTTPReceive,
    HTTPReceiveEvent,
    HTTPScope,
    HTTPSend,
    HTTPSendEvent,
    LifespanReceive,
    LifespanScope,
    LifespanSend,
    LifespanShutdownCompleteEvent,
    LifespanShutdownEvent,
    LifespanShutdownFailedEvent,
    LifespanStartupCompleteEvent,
    LifespanStartupEvent,
    LifespanStartupFailedEvent,
    WebSocketAcceptEvent,
    WebSocketCloseEvent,
    WebSocketConnectEvent,
    WebSocketReceive,
    WebSocketReceiveMessage,
    WebSocketScope,
    WebSocketSend,
    WebSocketSendEvent,
)
from lettia.context import Context


def http_scope(
    *,
    method: str = "GET",
    path: str = "/",
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> HTTPScope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.5"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": query_string,
        "root_path": "",
        "headers": headers or [],
    }


def websocket_scope(
    *,
    path: str = "/",
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    subprotocols: list[str] | None = None,
) -> WebSocketScope:
    return {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.5"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": path,
        "query_string": query_string,
        "root_path": "",
        "headers": headers or [],
        "subprotocols": subprotocols or [],
    }


def lifespan_scope() -> LifespanScope:
    return {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}


def http_receive(events: Iterable[HTTPReceiveEvent]) -> HTTPReceive:
    queued = iter(events)

    async def receive() -> HTTPReceiveEvent:
        message = next(queued, None)
        if message is not None:
            return message
        # An open ASGI connection blocks until more data or a disconnect.
        await asyncio.Future[None]()
        raise AssertionError("Unreachable")

    return receive


def http_sender(messages: list[HTTPSendEvent]) -> HTTPSend:
    async def send(message: HTTPSendEvent) -> None:
        messages.append(message)

    return send


def response_body(messages: list[HTTPSendEvent]) -> bytes:
    """Check a completed response against the ASGI event grammar."""
    assert messages and messages[0]["type"] == "http.response.start"
    assert sum(message["type"] == "http.response.start" for message in messages) == 1
    chunks: list[bytes] = []
    for index, message in enumerate(messages[1:], 1):
        assert message["type"] == "http.response.body"
        assert message.get("more_body", False) == (index < len(messages) - 1)
        chunks.append(message.get("body", b""))
    assert len(messages) >= 2
    return b"".join(chunks)


def http_context(
    *,
    method: str = "GET",
    path: str = "/",
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes = b"",
) -> Context:
    messages: list[HTTPSendEvent] = []
    return Context(
        scope=http_scope(
            method=method,
            path=path,
            query_string=query_string,
            headers=headers,
        ),
        receive=http_receive([{"type": "http.request", "body": body}]),
        send=http_sender(messages),
    )


def websocket_receive(
    events: Iterable[WebSocketConnectEvent | WebSocketReceiveMessage],
) -> WebSocketReceive:
    queued = iter(events)

    async def receive() -> WebSocketConnectEvent | WebSocketReceiveMessage:
        return next(queued)

    return receive


def websocket_sender(
    messages: list[WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent],
) -> WebSocketSend:
    async def send(
        message: WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent,
    ) -> None:
        messages.append(message)

    return send


def lifespan_receive(
    events: Iterable[LifespanStartupEvent | LifespanShutdownEvent],
) -> LifespanReceive:
    queued = iter(events)

    async def receive() -> LifespanStartupEvent | LifespanShutdownEvent:
        return next(queued)

    return receive


type LifespanSendEvent = (
    LifespanStartupCompleteEvent
    | LifespanStartupFailedEvent
    | LifespanShutdownCompleteEvent
    | LifespanShutdownFailedEvent
)


def lifespan_sender(messages: list[LifespanSendEvent]) -> LifespanSend:
    async def send(message: LifespanSendEvent) -> None:
        messages.append(message)

    return send
