import pytest
from asgi_helpers import (
    http_context,
    websocket_receive,
    websocket_scope,
    websocket_sender,
)

from lettia.asgi import WebSocketAcceptEvent, WebSocketCloseEvent, WebSocketSendEvent
from lettia.state import StateKey
from lettia.websocket import WebSocketContext


def test_state_store_preserves_key_identity_and_value_types() -> None:
    first_key = StateKey[int]("example.value")
    same_name_key = StateKey[int]("example.value")
    ctx = http_context()
    ctx.state.set(first_key, 42)
    assert ctx.state.get(first_key) == 42
    assert ctx.state.get(same_name_key) is None
    assert ctx.state.require(first_key) == 42


def test_state_store_require_and_discard() -> None:
    key = StateKey[str]("example.token")
    ctx = http_context()
    with pytest.raises(KeyError, match="example.token"):
        ctx.state.require(key)
    ctx.state.set(key, "token")
    ctx.state.discard(key)
    assert ctx.state.get(key) is None


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


def test_state_store_has_no_dictionary_style_api() -> None:
    with pytest.raises(AttributeError):
        object.__getattribute__(http_context().state, "__getitem__")
