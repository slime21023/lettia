import json
from enum import Enum

from attrs import define, field

from lettia._json import validate_json_value
from lettia.asgi import (
    Headers,
    JSONValue,
    WebSocketAcceptEvent,
    WebSocketCloseEvent,
    WebSocketReceive,
    WebSocketReceiveMessage,
    WebSocketScope,
    WebSocketSend,
    WebSocketSendEvent,
)
from lettia.state import StateStore


class WebSocketState(Enum):
    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2


class WebSocketDisconnect(Exception):
    """Raised when the peer closes a WebSocket connection."""

    def __init__(self, code: int = 1000, reason: str | None = None) -> None:
        self.code: int = code
        self.reason: str | None = reason
        super().__init__(reason or f"WebSocket disconnected ({code})")


@define(slots=True)
class WebSocketContext:
    scope: WebSocketScope
    receive: WebSocketReceive
    send: WebSocketSend
    path_params: dict[str, str] = field(factory=dict[str, str])
    state: StateStore = field(factory=StateStore)
    client_state: WebSocketState = field(default=WebSocketState.CONNECTING, init=False)

    @property
    def path(self) -> str:
        return self.scope["path"]

    @property
    def headers(self) -> dict[str, str]:
        hdr_dict: dict[str, str] = {}
        raw_headers = self.scope["headers"]
        for k, v in raw_headers:
            name = k.decode("latin-1").lower()
            val = v.decode("latin-1")
            hdr_dict[name] = val
        return hdr_dict

    async def accept(
        self, subprotocol: str | None = None, headers: Headers | None = None
    ) -> None:
        if self.client_state != WebSocketState.CONNECTING:
            raise RuntimeError("WebSocket is already connected or closed.")

        message: WebSocketAcceptEvent = {"type": "websocket.accept"}
        if subprotocol is not None:
            message["subprotocol"] = subprotocol
        if headers is not None:
            message["headers"] = headers

        await self.send(message)
        self.client_state = WebSocketState.CONNECTED

    async def receive_message(self) -> WebSocketReceiveMessage:
        if self.client_state != WebSocketState.CONNECTED:
            raise RuntimeError("WebSocket is not connected.")

        message = await self.receive()
        if message["type"] == "websocket.connect":
            raise RuntimeError("WebSocket connect event must be consumed by App")

        if message["type"] == "websocket.disconnect":
            self.client_state = WebSocketState.DISCONNECTED

        return message

    async def receive_text(self) -> str:
        message = await self.receive_message()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message["code"], message.get("reason"))
        received = message
        text = received.get("text")
        if text is not None:
            return text
        bytes_data = received.get("bytes")
        if bytes_data is not None:
            return bytes_data.decode("utf-8")
        raise RuntimeError("Received invalid websocket frame")

    async def receive_bytes(self) -> bytes:
        message = await self.receive_message()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message["code"], message.get("reason"))
        received = message
        bytes_data = received.get("bytes")
        if bytes_data is not None:
            return bytes_data
        text_data = received.get("text")
        if text_data is not None:
            return text_data.encode("utf-8")
        raise RuntimeError("Received invalid websocket frame")

    async def receive_json(self) -> JSONValue:
        text = await self.receive_text()
        return validate_json_value(json.loads(text))

    async def send_text(self, data: str) -> None:
        if self.client_state != WebSocketState.CONNECTED:
            raise RuntimeError("WebSocket is not connected.")
        message: WebSocketSendEvent = {"type": "websocket.send", "text": data}
        await self.send(message)

    async def send_bytes(self, data: bytes) -> None:
        if self.client_state != WebSocketState.CONNECTED:
            raise RuntimeError("WebSocket is not connected.")
        message: WebSocketSendEvent = {"type": "websocket.send", "bytes": data}
        await self.send(message)

    async def send_json(self, data: JSONValue) -> None:
        text = json.dumps(data, ensure_ascii=False)
        await self.send_text(text)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        if self.client_state == WebSocketState.DISCONNECTED:
            return
        message: WebSocketCloseEvent = {"type": "websocket.close", "code": code}
        if reason:
            message["reason"] = reason
        await self.send(message)
        self.client_state = WebSocketState.DISCONNECTED
