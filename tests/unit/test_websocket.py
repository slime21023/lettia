"""Unit tests for core WebSocket connection, routing, and lifecycle."""

import msgspec
import pytest

from lettia.core import (
    App,
    Context,
    Response,
    WebSocket,
    WebSocketError,
)
from lettia.testing import TestClient


class ChatMessage(msgspec.Struct):
    user: str
    content: str


@pytest.mark.asyncio
async def test_websocket_text_and_bytes_and_json() -> None:
    app = App()

    async def ws_handler(ws: WebSocket) -> None:
        await ws.accept()
        # Text frame
        text = await ws.receive_text()
        await ws.send_text(f"echo: {text}")

        # Binary frame
        binary = await ws.receive_bytes()
        await ws.send_bytes(binary + b"_reply")

        # JSON frame
        msg = await ws.receive_json(ChatMessage)
        await ws.send_json(
            ChatMessage(user=msg.user, content=f"received: {msg.content}")
        )
        await ws.close()

    app.websocket("/ws", ws_handler)

    async with TestClient(app) as client:
        async with client.websocket_connect("/ws") as ws:
            assert ws.accepted

            # Send/receive text
            await ws.send_text("hello")
            assert await ws.receive_text() == "echo: hello"

            # Send/receive bytes
            await ws.send_bytes(b"data")
            assert await ws.receive_bytes() == b"data_reply"

            # Send/receive JSON
            await ws.send_json({"user": "alice", "content": "hello world"})
            reply = await ws.receive_json(ChatMessage)
            assert reply.user == "alice"
            assert reply.content == "received: hello world"


@pytest.mark.asyncio
async def test_websocket_path_params_and_query() -> None:
    app = App()
    group = app.group("/chat")

    async def room_handler(ws: WebSocket) -> None:
        await ws.accept()
        room = ws.path_params["room_id"]
        query = ws.query_string.decode("ascii")
        await ws.send_text(f"room={room}&query={query}")
        await ws.close()

    group.websocket("/rooms/{room_id}", room_handler)

    async with TestClient(app) as client:
        async with client.websocket_connect("/chat/rooms/general?token=xyz") as ws:
            assert ws.accepted
            reply = await ws.receive_text()
            assert reply == "room=general&query=token=xyz"


@pytest.mark.asyncio
async def test_websocket_subprotocol_negotiation() -> None:
    app = App()

    async def proto_handler(ws: WebSocket) -> None:
        assert ws.subprotocols == ["v2.chat", "v1.chat"]
        await ws.accept(subprotocol="v2.chat")
        await ws.send_text("ok")
        await ws.close()

    app.websocket("/proto", proto_handler)

    async with TestClient(app) as client:
        async with client.websocket_connect(
            "/proto", subprotocols=["v2.chat", "v1.chat"]
        ) as ws:
            assert ws.accepted
            assert ws.accepted_subprotocol == "v2.chat"
            assert await ws.receive_text() == "ok"


@pytest.mark.asyncio
async def test_websocket_stream_iterators() -> None:
    app = App()

    async def stream_handler(ws: WebSocket) -> None:
        await ws.accept()
        async for text in ws.iter_text():
            await ws.send_text(f"stream:{text}")

    app.websocket("/stream", stream_handler)

    async with TestClient(app) as client:
        async with client.websocket_connect("/stream") as ws:
            assert ws.accepted
            for i in range(3):
                await ws.send_text(str(i))
                assert await ws.receive_text() == f"stream:{i}"


@pytest.mark.asyncio
async def test_websocket_not_found() -> None:
    app = App()

    async def ws_handler(ws: WebSocket) -> None:
        await ws.accept()

    app.websocket("/ws", ws_handler)

    async with TestClient(app) as client:
        async with client.websocket_connect("/nonexistent") as ws:
            assert not ws.accepted
            assert ws.response_status == 404


@pytest.mark.asyncio
async def test_websocket_method_not_allowed_for_http_route() -> None:
    app = App()

    async def http_handler(c: Context) -> Response:
        return c.text(200, "hello")

    app.get("/http-only", http_handler)

    async with TestClient(app) as client:
        async with client.websocket_connect("/http-only") as ws:
            assert not ws.accepted
            assert ws.response_status == 405


@pytest.mark.asyncio
async def test_websocket_error_before_accept() -> None:
    app = App()

    async def error_handler(ws: WebSocket) -> None:
        with pytest.raises(WebSocketError, match="Cannot send text before accept"):
            await ws.send_text("too early")
        await ws.accept()
        await ws.close()

    app.websocket("/error", error_handler)

    async with TestClient(app) as client:
        async with client.websocket_connect("/error") as ws:
            assert ws.accepted
