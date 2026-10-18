"""WebSocket connection encapsulation, lifecycle states, and frame codecs."""

from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    MutableMapping,
    Sequence,
)
from enum import Enum, auto
from typing import Any, cast, overload

import msgspec

from lettia.core.headers import Headers
from lettia.core.response import Response

type Scope = Mapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]


class WebSocketState(Enum):
    """Lifecycle state of a WebSocket connection."""

    CONNECTING = auto()
    CONNECTED = auto()
    DISCONNECTED = auto()


class WebSocketError(Exception):
    """Raised on invalid WebSocket state transitions or mismatched frame types."""


class WebSocketDisconnect(Exception):
    """Raised when the remote WebSocket client disconnects."""

    def __init__(self, code: int = 1000, reason: str = "") -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"WebSocket disconnected with code {code} ({reason})")


class WebSocket:
    """Represents a two-way WebSocket connection governed by ASGI 3.0."""

    __slots__ = (
        "_accepted_subprotocol",
        "_application_state",
        "_client_state",
        "_headers",
        "_receive",
        "_scope",
        "_send",
        "_state",
        "path_params",
    )

    def __init__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        path_params: dict[str, str] | None = None,
    ) -> None:
        self._scope = scope
        self._receive = receive
        self._send = send
        self._client_state = WebSocketState.CONNECTING
        self._application_state = WebSocketState.CONNECTING
        self._accepted_subprotocol: str | None = None
        self._headers: Headers | None = None
        self._state: dict[object, object] | None = None
        self.path_params: dict[str, str] = {} if path_params is None else path_params

    @property
    def scope(self) -> Scope:
        """The underlying ASGI connection scope."""
        return self._scope

    @property
    def client(self) -> tuple[str, int] | None:
        """Remote host and port, if available from the ASGI server."""
        client_info = self._scope.get("client")
        return (client_info[0], client_info[1]) if client_info else None

    @property
    def path(self) -> str:
        """The path requested during the HTTP upgrade handshake."""
        return cast(str, self._scope["path"])

    @property
    def raw_path(self) -> bytes:
        """Percent-encoded URL path from the ASGI server, or ASCII-encoded path."""
        raw = self._scope.get("raw_path")
        return cast(bytes, raw) if raw is not None else self.path.encode("ascii")

    @property
    def query_string(self) -> bytes:
        """Query string bytes from the upgrade handshake."""
        return cast(bytes, self._scope.get("query_string", b""))

    @property
    def headers(self) -> Headers:
        """HTTP upgrade request headers."""
        if self._headers is None:
            self._headers = Headers(self._scope.get("headers", ()))
        return self._headers

    @property
    def subprotocols(self) -> list[str]:
        """Subprotocols requested by the client."""
        return list(self._scope.get("subprotocols", ()))

    @property
    def accepted_subprotocol(self) -> str | None:
        """Subprotocol negotiated and accepted by the server."""
        return self._accepted_subprotocol

    @property
    def client_state(self) -> WebSocketState:
        """Lifecycle state of the client-side socket."""
        return self._client_state

    @property
    def application_state(self) -> WebSocketState:
        """Lifecycle state of the server application handler."""
        return self._application_state

    @property
    def state(self) -> dict[object, object]:
        """Typed storage shared with Context.state, accessed via Key[T]."""
        if self._state is None:
            self._state = {}
        return self._state

    async def accept(
        self,
        subprotocol: str | None = None,
        headers: Sequence[tuple[bytes, bytes]] | None = None,
    ) -> None:
        """Complete the handshake and accept the WebSocket connection."""
        if self._application_state != WebSocketState.CONNECTING:
            raise WebSocketError(
                "Cannot accept a WebSocket that is already accepted or closed"
            )

        if self._client_state == WebSocketState.CONNECTING:
            message = await self._receive()
            message_type = message.get("type")
            if message_type != "websocket.connect":
                raise WebSocketError(
                    f"Expected 'websocket.connect', got {message_type!r}"
                )
            self._client_state = WebSocketState.CONNECTED

        accept_message: dict[str, Any] = {"type": "websocket.accept"}
        if subprotocol is not None:
            accept_message["subprotocol"] = subprotocol
            self._accepted_subprotocol = subprotocol
        if headers is not None:
            accept_message["headers"] = list(headers)

        await self._send(accept_message)
        self._application_state = WebSocketState.CONNECTED

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Send a close frame and terminate the WebSocket connection."""
        if self._application_state == WebSocketState.DISCONNECTED:
            return

        if self._application_state == WebSocketState.CONNECTING:
            if self._client_state == WebSocketState.CONNECTING:
                message = await self._receive()
                if message.get("type") == "websocket.connect":
                    self._client_state = WebSocketState.CONNECTED

        await self._send({"type": "websocket.close", "code": code, "reason": reason})
        self._application_state = WebSocketState.DISCONNECTED

    async def send_denial_response(self, response: Response) -> None:
        """Reject connection during upgrade using ASGI HTTP response or close."""
        if "websocket.http.response" in self._scope.get("extensions", {}):
            body_bytes = response.body if isinstance(response.body, bytes) else b""
            headers = list(response.headers.raw)
            headers.append((b"content-length", str(len(body_bytes)).encode("ascii")))
            if self._client_state == WebSocketState.CONNECTING:
                message = await self._receive()
                if message.get("type") == "websocket.connect":
                    self._client_state = WebSocketState.CONNECTED
            await self._send(
                {
                    "type": "websocket.http.response.start",
                    "status": response.status,
                    "headers": headers,
                }
            )
            await self._send(
                {
                    "type": "websocket.http.response.body",
                    "body": body_bytes,
                    "more_body": False,
                }
            )
            self._application_state = WebSocketState.DISCONNECTED
        else:
            close_code = (
                4000 + response.status if 4000 + response.status <= 4999 else 1008
            )
            await self.close(code=close_code, reason=f"HTTP {response.status}")

    async def receive(self) -> Message:
        """Receive the next raw ASGI message, raising on disconnect."""
        if self._client_state == WebSocketState.DISCONNECTED:
            raise WebSocketDisconnect(1006, "Connection is already closed")

        message = await self._receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            self._client_state = WebSocketState.DISCONNECTED
            raise WebSocketDisconnect(
                code=cast(int, message.get("code", 1000)),
                reason=cast(str, message.get("reason", "")),
            )
        return message

    async def receive_text(self) -> str:
        """Receive a single UTF-8 text frame."""
        message = await self.receive()
        text = message.get("text")
        if text is None:
            raise WebSocketError("Expected text WebSocket frame, got binary or empty")
        return cast(str, text)

    async def receive_bytes(self) -> bytes:
        """Receive a single binary frame."""
        message = await self.receive()
        data = message.get("bytes")
        if data is None:
            raise WebSocketError("Expected binary WebSocket frame, got text or empty")
        return cast(bytes, data)

    @overload
    async def receive_json(self) -> Any: ...

    @overload
    async def receive_json[T](self, type_spec: type[T]) -> T: ...

    async def receive_json[T](self, type_spec: type[T] | None = None) -> Any:
        """Receive a text or binary frame decoded into a msgspec type."""
        message = await self.receive()
        text = message.get("text")
        if text is not None:
            payload = cast(str, text).encode("utf-8")
        else:
            raw = message.get("bytes")
            if raw is not None:
                payload = cast(bytes, raw)
            else:
                raise WebSocketError("WebSocket frame contained neither text nor bytes")

        if type_spec is None:
            return msgspec.json.decode(payload)
        return msgspec.json.decode(payload, type=type_spec)

    async def send_text(self, data: str) -> None:
        """Send a UTF-8 text frame to the client."""
        if self._application_state != WebSocketState.CONNECTED:
            raise WebSocketError("Cannot send text before accept() or after close()")
        await self._send({"type": "websocket.send", "text": data})

    async def send_bytes(self, data: bytes) -> None:
        """Send a binary frame to the client."""
        if self._application_state != WebSocketState.CONNECTED:
            raise WebSocketError("Cannot send bytes before accept() or after close()")
        await self._send({"type": "websocket.send", "bytes": data})

    async def send_json(self, payload: object) -> None:
        """Encode an object using msgspec and send it as a text frame."""
        if self._application_state != WebSocketState.CONNECTED:
            raise WebSocketError("Cannot send JSON before accept() or after close()")
        encoded = msgspec.json.encode(payload).decode("utf-8")
        await self._send({"type": "websocket.send", "text": encoded})

    async def iter_text(self) -> AsyncIterator[str]:
        """Stream received text frames until the client disconnects."""
        try:
            while True:
                yield await self.receive_text()
        except WebSocketDisconnect:
            return

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        """Stream received binary frames until the client disconnects."""
        try:
            while True:
                yield await self.receive_bytes()
        except WebSocketDisconnect:
            return

    @overload
    def iter_json(self) -> AsyncIterator[Any]: ...

    @overload
    def iter_json[T](self, type_spec: type[T]) -> AsyncIterator[T]: ...

    async def iter_json[T](
        self, type_spec: type[T] | None = None
    ) -> AsyncIterator[Any]:
        """Stream received JSON frames until the client disconnects."""
        try:
            while True:
                if type_spec is None:
                    yield await self.receive_json()
                else:
                    yield await self.receive_json(type_spec)
        except WebSocketDisconnect:
            return
