import pytest

from lettia.asgi import WebSocketAcceptEvent, WebSocketCloseEvent, WebSocketSendEvent
from lettia.state import StateKey
from lettia.websocket import WebSocketContext
from tests.support.asgi import (
    http_context,
    websocket_receive,
    websocket_scope,
    websocket_sender,
)


@pytest.mark.contract("STATE-STORE")
def test_http_and_websocket_context_state_are_isolated() -> None:
    key = StateKey[str]("example.owner")
    http_ctx = http_context()
    sent: list[WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent] = []
    websocket_ctx = WebSocketContext(
        scope=websocket_scope(),
        receive=websocket_receive([{"type": "websocket.connect"}]),
        send=websocket_sender(sent),
    )
    http_ctx.state.set(key, "http")
    websocket_ctx.state.set(key, "websocket")
    assert http_ctx.state.require(key) == "http"
    assert websocket_ctx.state.require(key) == "websocket"
