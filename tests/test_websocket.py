import pytest
from asgi_helpers import websocket_receive, websocket_scope, websocket_sender

from lettia import App, WebSocketContext
from lettia.asgi import WebSocketAcceptEvent, WebSocketCloseEvent, WebSocketSendEvent


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


@pytest.mark.asyncio
async def test_websocket_binary_json_and_headers() -> None:
    sent: list[WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent] = []
    ws = WebSocketContext(
        scope=websocket_scope(path="/ws", headers=[(b"x-test", b"ok")]),
        receive=websocket_receive(
            [
                {"type": "websocket.receive", "bytes": b"binary"},
                {"type": "websocket.receive", "text": '{"ok": true}'},
            ]
        ),
        send=websocket_sender(sent),
    )

    await ws.accept(subprotocol="json")
    assert ws.headers == {"x-test": "ok"}
    assert await ws.receive_bytes() == b"binary"
    assert await ws.receive_json() == {"ok": True}
    await ws.send_bytes(b"response")
    await ws.send_json({"ok": True})
    await ws.close()

    assert sent == [
        {"type": "websocket.accept", "subprotocol": "json"},
        {"type": "websocket.send", "bytes": b"response"},
        {"type": "websocket.send", "text": '{"ok": true}'},
        {"type": "websocket.close", "code": 1000},
    ]


@pytest.mark.asyncio
async def test_websocket_rejects_invalid_state_transitions() -> None:
    sent: list[WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent] = []
    ws = WebSocketContext(
        scope=websocket_scope(path="/ws"),
        receive=websocket_receive([{"type": "websocket.receive", "text": "ok"}]),
        send=websocket_sender(sent),
    )

    with pytest.raises(RuntimeError, match="not connected"):
        await ws.receive_text()

    await ws.accept()
    await ws.close()

    with pytest.raises(RuntimeError, match="not connected"):
        await ws.send_text("after close")


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


@pytest.mark.asyncio
async def test_websocket_converts_text_and_rejects_duplicate_accept() -> None:
    sent: list[WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent] = []
    ws = WebSocketContext(
        scope=websocket_scope(),
        receive=websocket_receive([{"type": "websocket.receive", "text": "hello"}]),
        send=websocket_sender(sent),
    )
    await ws.accept()
    assert await ws.receive_bytes() == b"hello"
    with pytest.raises(RuntimeError, match="already connected"):
        await ws.accept()
    await ws.close(reason="done")
    await ws.close()
