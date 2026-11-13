"""Unit tests for core WebSocket connection, routing, and lifecycle."""

import logging
from typing import Any

import msgspec
import pytest

from lettia.core import (
    App,
    Context,
    Message,
    Response,
    WebSocket,
    WebSocketDisconnect,
    WebSocketError,
    WebSocketState,
)
from lettia.testing import TestClient


class ChatMessage(msgspec.Struct):
    user: str
    content: str


async def _drive(
    app: App,
    path: str,
    *,
    http_response: bool,
    incoming: list[Message] | None = None,
) -> list[Message]:
    """Run one WebSocket connection through ``app`` and return what it sent."""
    queue = incoming if incoming is not None else [{"type": "websocket.connect"}]
    sent: list[Message] = []

    async def receive() -> Message:
        return queue.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    extensions: dict[str, Any] = (
        {"websocket.http.response": {}} if http_response else {}
    )
    scope: dict[str, Any] = {
        "type": "websocket",
        "path": path,
        "headers": [],
        "extensions": extensions,
    }
    await app(scope, receive, send)
    return sent


def _socket(
    incoming: list[Message], scope: dict[str, Any] | None = None
) -> tuple[WebSocket, list[Message]]:
    """A WebSocket fed from ``incoming``; the second value collects what it sends."""
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    base: dict[str, Any] = {"type": "websocket", "path": "/ws", "headers": []}
    return WebSocket(scope or base, receive, send), sent


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


@pytest.mark.asyncio
async def test_websocket_without_raw_path_matches_a_percent_encoded_route() -> None:
    app = App()

    async def handler(ws: WebSocket) -> None:
        await ws.accept()
        await ws.send_bytes(ws.raw_path)
        await ws.close()

    app.websocket("/café", handler)
    app.build()

    incoming: list[Message] = [{"type": "websocket.connect"}]
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: dict[str, Any] = {
        "type": "websocket",
        "path": "/café",
        "headers": [],
        "extensions": {},
    }
    await app(scope, receive, send)

    assert [m["type"] for m in sent] == [
        "websocket.accept",
        "websocket.send",
        "websocket.close",
    ]
    assert sent[1]["bytes"] == b"/caf%C3%A9"


@pytest.mark.asyncio
async def test_websocket_denial_after_accept_raises_websocket_error() -> None:
    incoming: list[Message] = [{"type": "websocket.connect"}]
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: dict[str, Any] = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "extensions": {"websocket.http.response": {}},
    }
    ws = WebSocket(scope, receive, send)
    await ws.accept()

    with pytest.raises(WebSocketError, match="already accepted or closed"):
        await ws.send_denial_response(Response(403))

    assert [m["type"] for m in sent] == ["websocket.accept"]


@pytest.mark.asyncio
async def test_websocket_denial_sends_a_single_content_length() -> None:
    incoming: list[Message] = [{"type": "websocket.connect"}]
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: dict[str, Any] = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "extensions": {"websocket.http.response": {}},
    }
    ws = WebSocket(scope, receive, send)
    response = Response(401, b"no")
    response.headers.set("content-length", "999")

    await ws.send_denial_response(response)

    assert [v for n, v in sent[0]["headers"] if n == b"content-length"] == [b"2"]


@pytest.mark.asyncio
async def test_websocket_unknown_path_closes_with_4404_without_http_response() -> None:
    app = App()

    async def handler(ws: WebSocket) -> None:
        await ws.accept()

    app.websocket("/ws", handler)

    sent = await _drive(app, "/missing", http_response=False)
    assert sent == [{"type": "websocket.close", "code": 4404, "reason": "Not Found"}]


@pytest.mark.asyncio
async def test_websocket_on_an_http_route_closes_with_4405_without_http_response() -> (
    None
):
    app = App()

    async def http_handler(c: Context) -> Response:
        return c.text(200, "hello")

    app.get("/http-only", http_handler)

    sent = await _drive(app, "/http-only", http_response=False)
    assert sent == [
        {"type": "websocket.close", "code": 4405, "reason": "Method Not Allowed"}
    ]


@pytest.mark.asyncio
async def test_websocket_405_denial_lists_the_allowed_methods() -> None:
    app = App()

    async def http_handler(c: Context) -> Response:
        return c.text(200, "hello")

    app.get("/http-only", http_handler)

    sent = await _drive(app, "/http-only", http_response=True)
    assert sent[0]["type"] == "websocket.http.response.start"
    assert sent[0]["status"] == 405
    assert (b"allow", b"GET, HEAD") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_websocket_handler_error_after_accept_closes_with_1011(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = App()

    async def boom(ws: WebSocket) -> None:
        await ws.accept()
        raise RuntimeError("boom")

    app.websocket("/ws", boom)

    with caplog.at_level(logging.ERROR, logger="lettia"):
        sent = await _drive(app, "/ws", http_response=False)

    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.close"]
    assert sent[1]["code"] == 1011
    assert sent[1]["reason"] == "Internal Server Error"
    assert [r.getMessage() for r in caplog.records] == [
        "unhandled exception in WebSocket handler on /ws"
    ]


@pytest.mark.asyncio
async def test_websocket_handler_error_before_accept_is_a_500_denial() -> None:
    app = App()

    async def boom(ws: WebSocket) -> None:
        raise RuntimeError("boom")

    app.websocket("/ws", boom)

    sent = await _drive(app, "/ws", http_response=True)
    assert [m["type"] for m in sent] == [
        "websocket.http.response.start",
        "websocket.http.response.body",
    ]
    assert sent[0]["status"] == 500


@pytest.mark.asyncio
async def test_websocket_error_before_accept_closes_with_1011_without_http() -> None:
    app = App()

    async def boom(ws: WebSocket) -> None:
        raise RuntimeError("boom")

    app.websocket("/ws", boom)

    sent = await _drive(app, "/ws", http_response=False)
    assert sent == [
        {"type": "websocket.close", "code": 1011, "reason": "Internal Server Error"}
    ]


@pytest.mark.asyncio
async def test_websocket_client_disconnect_ends_the_handler_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = App()

    async def handler(ws: WebSocket) -> None:
        await ws.accept()
        await ws.receive_text()

    app.websocket("/ws", handler)
    incoming: list[Message] = [
        {"type": "websocket.connect"},
        {"type": "websocket.disconnect", "code": 1001},
    ]

    with caplog.at_level(logging.ERROR, logger="lettia"):
        sent = await _drive(app, "/ws", http_response=False, incoming=incoming)

    assert [m["type"] for m in sent] == ["websocket.accept"]
    assert caplog.records == []


@pytest.mark.asyncio
async def test_websocket_exposes_its_scope_and_lifecycle_states() -> None:
    scope: dict[str, Any] = {
        "type": "websocket",
        "path": "/chat",
        "raw_path": b"/chat%21",
        "client": ("1.2.3.4", 5000),
        "headers": [],
    }
    ws, _ = _socket(
        [{"type": "websocket.connect"}, {"type": "websocket.disconnect", "code": 1000}],
        scope,
    )
    assert ws.scope is scope
    assert ws.path == "/chat"
    assert ws.raw_path == b"/chat%21"
    assert ws.client == ("1.2.3.4", 5000)
    assert ws.client_state == WebSocketState.CONNECTING
    assert ws.application_state == WebSocketState.CONNECTING

    await ws.accept()
    assert ws.client_state == WebSocketState.CONNECTED
    assert ws.application_state == WebSocketState.CONNECTED

    await ws.close()
    assert ws.application_state == WebSocketState.DISCONNECTED
    assert ws.client_state == WebSocketState.CONNECTED

    with pytest.raises(WebSocketDisconnect):
        await ws.receive()
    assert ws.client_state == WebSocketState.DISCONNECTED

    default_ws, _ = _socket([])
    assert default_ws.client is None


@pytest.mark.asyncio
async def test_websocket_accept_sends_headers_and_cannot_repeat() -> None:
    ws, sent = _socket([{"type": "websocket.connect"}])
    await ws.accept(headers=[(b"x-a", b"1")])
    assert sent == [{"type": "websocket.accept", "headers": [(b"x-a", b"1")]}]

    with pytest.raises(WebSocketError, match="already accepted or closed"):
        await ws.accept()


@pytest.mark.asyncio
async def test_websocket_close_is_final_and_idempotent() -> None:
    ws, sent = _socket([{"type": "websocket.connect"}])
    await ws.accept()
    await ws.close()
    await ws.close()

    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.close"]
    with pytest.raises(WebSocketError, match="already accepted or closed"):
        await ws.accept()
    with pytest.raises(WebSocketError, match="after close"):
        await ws.send_text("x")
    with pytest.raises(WebSocketError, match="after close"):
        await ws.send_bytes(b"x")
    with pytest.raises(WebSocketError, match="after close"):
        await ws.send_json({})


@pytest.mark.asyncio
async def test_websocket_sends_before_accept_are_rejected() -> None:
    ws, sent = _socket([])
    with pytest.raises(WebSocketError, match="before accept"):
        await ws.send_bytes(b"x")
    with pytest.raises(WebSocketError, match="before accept"):
        await ws.send_json({})
    assert sent == []


@pytest.mark.asyncio
async def test_websocket_receive_raises_disconnect_with_code_and_reason() -> None:
    ws, _ = _socket(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.disconnect", "code": 1001, "reason": "bye"},
        ]
    )
    await ws.accept()

    with pytest.raises(WebSocketDisconnect) as first:
        await ws.receive()
    assert (first.value.code, first.value.reason) == (1001, "bye")

    with pytest.raises(WebSocketDisconnect) as second:
        await ws.receive()
    assert second.value.code == 1006


@pytest.mark.asyncio
async def test_websocket_receive_rejects_the_wrong_frame_type() -> None:
    ws, _ = _socket(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "bytes": b"x"},
            {"type": "websocket.receive", "text": "y"},
            {"type": "websocket.receive"},
        ]
    )
    await ws.accept()

    with pytest.raises(WebSocketError, match="Expected text"):
        await ws.receive_text()
    with pytest.raises(WebSocketError, match="Expected binary"):
        await ws.receive_bytes()
    with pytest.raises(WebSocketError, match="neither text nor bytes"):
        await ws.receive_json()


@pytest.mark.asyncio
async def test_websocket_receive_json_reads_text_or_binary_frames() -> None:
    ws, _ = _socket(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": '{"a": 1}'},
            {"type": "websocket.receive", "bytes": b"[1, 2]"},
            {"type": "websocket.receive", "text": "not json"},
        ]
    )
    await ws.accept()

    assert await ws.receive_json() == {"a": 1}
    assert await ws.receive_json(list[int]) == [1, 2]
    with pytest.raises(msgspec.DecodeError):
        await ws.receive_json()


@pytest.mark.asyncio
async def test_websocket_iter_bytes_and_iter_json_end_on_disconnect() -> None:
    sock_a, _ = _socket(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "bytes": b"a"},
            {"type": "websocket.receive", "bytes": b"b"},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    sock_b, _ = _socket(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": '{"user": "u", "content": "c"}'},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    await sock_a.accept()
    await sock_b.accept()

    assert [b async for b in sock_a.iter_bytes()] == [b"a", b"b"]
    assert [m async for m in sock_b.iter_json(ChatMessage)] == [
        ChatMessage(user="u", content="c")
    ]
