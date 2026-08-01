---
title: WebSocket API
---

# WebSocket API

WebSockets use a separate ASGI branch and do not pass through the HTTP
middleware or response pipeline. Register a handler with
`app.websocket(path)` and operate through `WebSocketContext`.

## Connection states

```python
class WebSocketState(Enum):
    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2
```

The normal lifecycle is:

```text
CONNECTING --accept()--> CONNECTED --close()/peer disconnect--> DISCONNECTED
```

`WebSocketDisconnect` represents a normal peer disconnect and should usually
be caught by an echo or subscription loop.

## `WebSocketContext`

```python
WebSocketContext(
    scope: MutableMapping[str, Any],
    receive: Any,
    send: Any,
    path_params: dict[str, str] = {},
    state: dict[str, Any] = {},
)
```

The mutable defaults above are conceptual; the implementation creates fresh
dictionaries for each context.

### Connection and metadata

| API | Behavior |
|---|---|
| `ws.path` | Path from the WebSocket scope |
| `ws.headers` | Lower-case handshake headers |
| `ws.path_params` | Router-provided string parameters |
| `ws.state` | Connection-local mutable state |
| `ws.client_state` | Current `WebSocketState` |

### Handshake and close

```python
await ws.accept(subprotocol: str | None = None) -> None
await ws.close(code: int = 1000, reason: str | None = None) -> None
```

Sending or receiving before `accept()` raises `RuntimeError`. Closing an
already disconnected context is idempotent.

### Receive methods

```python
await ws.receive_message() -> dict[str, Any]
await ws.receive_text() -> str
await ws.receive_bytes() -> bytes
await ws.receive_json() -> Any
```

`receive_text()` decodes binary frames as UTF-8; `receive_bytes()` encodes text
frames as UTF-8. A peer disconnect changes the state and raises
`WebSocketDisconnect` from the typed receive helpers.

### Send methods

```python
await ws.send_text(data: str) -> None
await ws.send_bytes(data: bytes) -> None
await ws.send_json(data: Any) -> None
```

Send methods require the context to be connected. JSON is serialized as a text
frame with UTF-8-compatible JSON.

## Handler pattern

```python
@app.websocket("/ws")
async def echo(ws: WebSocketContext) -> None:
    await ws.accept()
    try:
        while True:
            message = await ws.receive_text()
            await ws.send_text(message)
    except WebSocketDisconnect:
        return
```

Authentication or protocol negotiation can inspect `ws.headers` before
accepting the connection.
