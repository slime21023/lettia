from typing import Any

import pytest

from lettia import App, WebSocketContext


@pytest.mark.asyncio
async def test_websocket_lifecycle() -> None:
    app = App()
    received_messages: list[str] = []

    @app.websocket("/ws")
    async def chat(ws: WebSocketContext) -> None:
        await ws.accept()
        msg = await ws.receive_text()
        received_messages.append(msg)
        await ws.send_text(f"Echo: {msg}")
        await ws.close(1000)

    # Simulate ASGI websocket scope communication
    client_sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        client_sent.append(message)

    server_messages: list[dict[str, Any]] = [
        {"type": "websocket.receive", "text": "Hello WebSocket"}
    ]

    async def receive() -> dict[str, Any]:
        if server_messages:
            return server_messages.pop(0)
        return {"type": "websocket.disconnect"}

    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [(b"host", b"localhost")],
    }

    await app(scope, receive, send)

    assert received_messages == ["Hello WebSocket"]
    assert client_sent[0]["type"] == "websocket.accept"
    assert client_sent[1]["text"] == "Echo: Hello WebSocket"
    assert client_sent[2]["type"] == "websocket.close"


@pytest.mark.asyncio
async def test_websocket_binary_json_and_headers() -> None:
    sent: list[dict[str, Any]] = []
    received = [
        {"type": "websocket.receive", "bytes": b"binary"},
        {"type": "websocket.receive", "text": '{"ok": true}'},
    ]

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return received.pop(0)

    ws = WebSocketContext(
        scope={"type": "websocket", "path": "/ws", "headers": [(b"x-test", b"ok")]},
        receive=receive,
        send=send,
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
    async def send(message: dict[str, Any]) -> None:
        return None

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.receive", "text": "ok"}

    ws = WebSocketContext(
        scope={"type": "websocket", "path": "/ws"},
        receive=receive,
        send=send,
    )

    with pytest.raises(RuntimeError, match="not connected"):
        await ws.receive_text()

    await ws.accept()
    await ws.close()

    with pytest.raises(RuntimeError, match="not connected"):
        await ws.send_text("after close")
