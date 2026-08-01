import json
from collections.abc import MutableMapping
from enum import Enum
from typing import Any

from attrs import define, field


class WebSocketState(Enum):
    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2


class WebSocketDisconnect(Exception):
    """Raised when the peer closes a WebSocket connection."""


@define(slots=True)
class WebSocketContext:
    scope: MutableMapping[str, Any]
    receive: Any
    send: Any
    path_params: dict[str, str] = field(factory=dict)
    state: dict[str, Any] = field(factory=dict)
    client_state: WebSocketState = field(default=WebSocketState.CONNECTING, init=False)

    @property
    def path(self) -> str:
        return str(self.scope.get("path", "/"))

    @property
    def headers(self) -> dict[str, str]:
        hdr_dict: dict[str, str] = {}
        raw_headers = self.scope.get("headers", [])
        for k, v in raw_headers:
            name = k.decode("latin-1").lower()
            val = v.decode("latin-1")
            hdr_dict[name] = val
        return hdr_dict

    async def accept(self, subprotocol: str | None = None) -> None:
        if self.client_state != WebSocketState.CONNECTING:
            raise RuntimeError("WebSocket is already connected or closed.")

        message: dict[str, Any] = {"type": "websocket.accept"}
        if subprotocol is not None:
            message["subprotocol"] = subprotocol

        await self.send(message)
        self.client_state = WebSocketState.CONNECTED

    async def receive_message(self) -> dict[str, Any]:
        if self.client_state != WebSocketState.CONNECTED:
            raise RuntimeError("WebSocket is not connected.")

        message = await self.receive()
        msg_type = message.get("type")

        if msg_type == "websocket.disconnect":
            self.client_state = WebSocketState.DISCONNECTED

        return message

    async def receive_text(self) -> str:
        message = await self.receive_message()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect
        text = message.get("text")
        if text is not None:
            return text
        bytes_data = message.get("bytes")
        if bytes_data is not None:
            return bytes_data.decode("utf-8")
        raise RuntimeError("Received invalid websocket frame")

    async def receive_bytes(self) -> bytes:
        message = await self.receive_message()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect
        bytes_data = message.get("bytes")
        if bytes_data is not None:
            return bytes_data
        text_data = message.get("text")
        if text_data is not None:
            return text_data.encode("utf-8")
        raise RuntimeError("Received invalid websocket frame")

    async def receive_json(self) -> Any:
        text = await self.receive_text()
        return json.loads(text)

    async def send_text(self, data: str) -> None:
        if self.client_state != WebSocketState.CONNECTED:
            raise RuntimeError("WebSocket is not connected.")
        await self.send({"type": "websocket.send", "text": data})

    async def send_bytes(self, data: bytes) -> None:
        if self.client_state != WebSocketState.CONNECTED:
            raise RuntimeError("WebSocket is not connected.")
        await self.send({"type": "websocket.send", "bytes": data})

    async def send_json(self, data: Any) -> None:
        text = json.dumps(data, ensure_ascii=False)
        await self.send_text(text)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        if self.client_state == WebSocketState.DISCONNECTED:
            return
        message: dict[str, Any] = {"type": "websocket.close", "code": code}
        if reason:
            message["reason"] = reason
        await self.send(message)
        self.client_state = WebSocketState.DISCONNECTED
