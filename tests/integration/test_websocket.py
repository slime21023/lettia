import pytest

from lettia import App, WebSocketContext
from lettia.asgi import (
    WebSocketAcceptEvent,
    WebSocketCloseEvent,
    WebSocketSendEvent,
)
from tests.support.asgi import (
    websocket_receive,
    websocket_scope,
    websocket_sender,
)


@pytest.mark.contract("WS-STATE")
@pytest.mark.asyncio
async def test_websocket_disconnect_is_not_reported_as_internal_error() -> None:
    app = App()

    async def websocket_handler(ws: WebSocketContext) -> None:
        await ws.accept()
        await ws.receive_text()

    app.websocket("/ws")(websocket_handler)
    sent: list[WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent] = []
    await app(
        websocket_scope(path="/ws"),
        websocket_receive(
            [
                {"type": "websocket.connect"},
                {"type": "websocket.disconnect", "code": 1000},
            ]
        ),
        websocket_sender(sent),
    )
    assert sent == [{"type": "websocket.accept"}]


@pytest.mark.contract("WS-STATE")
@pytest.mark.asyncio
async def test_websocket_lifecycle() -> None:
    app = App()
    received_messages: list[str] = []

    async def chat(ws: WebSocketContext) -> None:
        await ws.accept()
        message = await ws.receive_text()
        received_messages.append(message)
        await ws.send_text(f"Echo: {message}")
        await ws.close(1000)

    app.websocket("/ws")(chat)
    client_sent: list[
        WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent
    ] = []
    await app(
        websocket_scope(path="/ws", headers=[(b"host", b"localhost")]),
        websocket_receive(
            [
                {"type": "websocket.connect"},
                {"type": "websocket.receive", "text": "Hello WebSocket"},
            ]
        ),
        websocket_sender(client_sent),
    )

    assert received_messages == ["Hello WebSocket"]
    assert client_sent[0]["type"] == "websocket.accept"
    assert client_sent[1]["type"] == "websocket.send"
    assert client_sent[1]["text"] == "Echo: Hello WebSocket"
    assert client_sent[2]["type"] == "websocket.close"


@pytest.mark.contract("WS-STATE")
@pytest.mark.asyncio
async def test_websocket_handles_missing_and_failing_handlers() -> None:
    missing_app = App()
    missing_messages: list[
        WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent
    ] = []
    await missing_app(
        websocket_scope(path="/missing"),
        websocket_receive([{"type": "websocket.connect"}]),
        websocket_sender(missing_messages),
    )
    assert missing_messages == [{"type": "websocket.close", "code": 404}]

    failing_app = App()

    async def failing_handler(ws: WebSocketContext) -> None:
        await ws.accept()
        raise RuntimeError("boom")

    failing_app.websocket("/failing")(failing_handler)
    failed_messages: list[
        WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent
    ] = []
    await failing_app(
        websocket_scope(path="/failing"),
        websocket_receive([{"type": "websocket.connect"}]),
        websocket_sender(failed_messages),
    )
    assert failed_messages[-1] == {
        "type": "websocket.close",
        "code": 1011,
        "reason": "Internal Error",
    }
