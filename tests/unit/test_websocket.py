import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia import WebSocketContext
from lettia.asgi import WebSocketAcceptEvent, WebSocketCloseEvent, WebSocketSendEvent
from tests.support.asgi import websocket_receive, websocket_scope, websocket_sender
from tests.support.strategies import TEXT


@pytest.mark.contract("WS-STATE")
@given(
    operations=st.lists(
        st.tuples(st.sampled_from(["accept", "send", "close"]), TEXT), max_size=50
    )
)
async def test_websocket_operations_follow_connection_model(
    operations: list[tuple[str, str]],
) -> None:
    sent: list[WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent] = []
    ws = WebSocketContext(
        websocket_scope(), websocket_receive([]), websocket_sender(sent)
    )
    state = "connecting"
    expected: list[WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent] = []
    for operation, text in operations:
        if operation == "accept":
            if state != "connecting":
                with pytest.raises(RuntimeError):
                    await ws.accept()
            else:
                await ws.accept()
                state = "connected"
                expected.append({"type": "websocket.accept"})
        elif operation == "send":
            if state != "connected":
                with pytest.raises(RuntimeError):
                    await ws.send_text(text)
            else:
                await ws.send_text(text)
                expected.append({"type": "websocket.send", "text": text})
        else:
            await ws.close()
            if state != "closed":
                expected.append({"type": "websocket.close", "code": 1000})
            state = "closed"
        assert sent == expected


@pytest.mark.contract("WS-STATE")
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


@pytest.mark.contract("WS-STATE")
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


@pytest.mark.contract("WS-STATE")
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
