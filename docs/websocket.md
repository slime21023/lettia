# WebSockets

WebSocket routes exist in both layers. The Core layer registers a plain
`async def handler(ws: WebSocket) -> None` with `app.websocket(path, handler)`.
The contract layer declares the route with `@op.websocket`, which adds a
required guard that runs before the handshake. Both are driven by the same
`WebSocket` object and are tested with `TestClient.websocket_connect`.

## Core routes

```python
from lettia.core import App, WebSocket

async def echo(ws: WebSocket) -> None:
    await ws.accept()
    async for text in ws.iter_text():
        await ws.send_text(text)

app = App()
app.websocket("/echo", echo)
app.group("/rooms").websocket("/{room}", echo)   # path "/rooms/{room}"
```

| API | Notes |
| --- | --- |
| `app.websocket(path, handler)` | `handler: Callable[[WebSocket], Awaitable[None]]` (`lettia.core.WebSocketHandler`). |
| `group.websocket(path, handler)` | Registers `prefix + path`. A path without a leading `/` is a startup problem. |

Path parameters use the same `{name}` syntax as HTTP routes and arrive as
strings in `ws.path_params`. WebSocket routes and HTTP routes share one router,
keyed by method: a WebSocket route is the method `WEBSOCKET`.

Middleware does not apply to WebSocket routes: neither `app.use(...)` nor group
middleware is run for them (the group variant registers the route with no
middleware).

### Handler lifecycle

The handler is responsible for calling `await ws.accept()` (or denying the
connection) itself. What the app does around it:

| Situation | Result |
| --- | --- |
| Handler raises `WebSocketDisconnect` | Swallowed; the connection simply ends. |
| Handler raises any other `Exception` | Logged with `logger.exception`. If accepted: `ws.close(1011, "Internal Server Error")`. If not yet accepted and the server supports `websocket.http.response`: an HTTP 500 denial. Otherwise: close with code 1011. |
| Handler returns without closing | Nothing is sent; the ASGI server finishes the connection. |
| No route for the path | HTTP 404 denial (`application/json` problem body), or close code **4404** `"Not Found"` without the extension. |
| Path exists only for HTTP methods | HTTP 405 denial with an `Allow` header, or close code **4405** `"Method Not Allowed"` without the extension. |

`WebSocketError` raised by the handler is an ordinary exception and goes
through the 1011/500 path above.

## `WebSocket`

Imported from `lettia` or `lettia.core`; `WebSocketDisconnect`,
`WebSocketError` and `WebSocketState` come from the same places
(`WebSocketState` from `lettia.core` only).

### Request information

| Member | Type | Meaning |
| --- | --- | --- |
| `scope` | `Mapping[str, Any]` | The raw ASGI scope. |
| `path` | `str` | Path of the upgrade request. |
| `raw_path` | `bytes` | Percent-encoded path from the server, or the ASCII-encoded `path`. |
| `query_string` | `bytes` | Raw query string (`b""` if absent). |
| `headers` | `Headers` | Upgrade request headers, parsed on first access. |
| `client` | `tuple[str, int] \| None` | Remote host and port, if the server gave them. |
| `subprotocols` | `list[str]` | Subprotocols requested by the client. |
| `accepted_subprotocol` | `str \| None` | Subprotocol passed to `accept()`; `None` before. |
| `path_params` | `dict[str, str]` | Values of the `{name}` segments. |
| `state` | `dict[object, object]` | Per-connection storage, created lazily. In a contract operation it is the same dict as `c.state`, so a `Key[T]` set on one side is visible on the other. |
| `client_state`, `application_state` | `WebSocketState` | `CONNECTING`, `CONNECTED` or `DISCONNECTED`. |

### Methods

```python
async def accept(self, subprotocol: str | None = None,
                 headers: Sequence[tuple[bytes, bytes]] | None = None) -> None
async def close(self, code: int = 1000, reason: str = "") -> None
async def send_denial_response(self, response: Response) -> None

async def receive(self) -> Message          # raw ASGI message
async def receive_text(self) -> str
async def receive_bytes(self) -> bytes
async def receive_json(self) -> Any
async def receive_json(self, type_spec: type[T]) -> T

async def send_text(self, data: str) -> None
async def send_bytes(self, data: bytes) -> None
async def send_json(self, payload: object) -> None

def iter_text(self) -> AsyncIterator[str]
def iter_bytes(self) -> AsyncIterator[bytes]
def iter_json(self) -> AsyncIterator[Any]
def iter_json(self, type_spec: type[T]) -> AsyncIterator[T]
```

`accept` waits for the client's `websocket.connect`, then sends
`websocket.accept` with the optional `subprotocol` and extra response
`headers` (raw byte pairs). Calling it when the handler side is already
accepted or closed raises `WebSocketError`.

`close` is a no-op when already closed. If called before `accept`, it consumes
the pending `websocket.connect` and sends `websocket.close` (the client sees a
rejected handshake).

`receive` raises `WebSocketDisconnect(code, reason)` when the client
disconnects, and `WebSocketDisconnect(1006, "Connection is already closed")`
when called after a disconnect was already seen.

`receive_text` / `receive_bytes` raise `WebSocketError` when the frame is of
the other kind. `receive_json` accepts a text or a binary frame, decodes it
with `msgspec.json`, and validates against `type_spec` when given (a
`msgspec.ValidationError` or `msgspec.DecodeError` propagates on bad input,
which ends the handler through the 1011 path unless caught).

`send_*` raise `WebSocketError` unless the application side is `CONNECTED`
(before `accept()` or after `close()`). `send_json` encodes with `msgspec.json`
and sends a text frame.

`iter_text`, `iter_bytes` and `iter_json` loop on the matching `receive_*`
call and end quietly when the client disconnects. Frame-type mismatches still
raise `WebSocketError`.

### `WebSocketDisconnect` and `WebSocketError`

`WebSocketDisconnect(code=1000, reason="")` has `.code` and `.reason`.
`WebSocketError` signals an invalid state transition or a frame of the wrong
type. Both derive from `Exception`.

### Denying a handshake

`send_denial_response(response)` rejects the connection before `accept`.

- If the server advertises the `websocket.http.response` ASGI extension, it
  sends the response status, headers (plus `content-length`) and body, and the
  application state becomes `DISCONNECTED`. The body is sent only when
  `response.body` is `bytes`; otherwise it is empty.
- Otherwise it closes with code `4000 + status` (e.g. 401 becomes 4401), or
  1008 if that exceeds 4999, with reason `"HTTP <status>"`.

```python
from lettia.core import Response, WebSocket

async def only_with_token(ws: WebSocket) -> None:
    if "token" not in ws.headers:
        await ws.send_denial_response(Response(401, b"token required"))
        return
    await ws.accept()
```

## Contract routes: `@op.websocket`

```python
op.websocket(path, *, guard, tags=(), operation_id=None, openapi_extra=None)
```

`guard` is required and must be a `GuardSpec` (`public` for none), as for the
HTTP verbs. `path` must start with `/`. The decorated method has the shape
`(self, c: Context[G], ws: WebSocket) -> None`; the contract declares it and
the implementation overrides it.

```python
from typing import override

import msgspec
from lettia import App, Context, GuardSpec, RequestHead, WebSocket, op
from lettia.core import Forbidden, Unauthorized


class User(msgspec.Struct):
    name: str


bearer = GuardSpec[User]("bearer")


async def verify(head: RequestHead) -> User:
    token = head.headers.get("authorization")
    if token is None:
        raise Unauthorized("missing token")
    if token != "Bearer ok":
        raise Forbidden("bad token")
    return User("ann")


class ChatApi:
    @op.websocket("/chat/{room}", guard=bearer, tags=["chat"])
    async def chat(self, c: Context[User], ws: WebSocket) -> None: ...


class Chat(ChatApi):
    @override
    async def chat(self, c: Context[User], ws: WebSocket) -> None:
        await ws.accept()
        await ws.send_text(f"{c.principal.name}@{c.path_params['room']}")
        async for text in ws.iter_text():
            await ws.send_text(text)


app = App(guards=[bearer.use(verify)])
app.mount(Chat())
```

Behavior at connection time:

1. A `Request` is built from the upgrade scope (method `GET`, empty body) and
   the guard runs on it. Guards see headers, query and cookies as for HTTP.
2. On success the principal is placed in `c.principal`, `c.path_params` holds
   the path parameters, `c.state` is `ws.state`, and the implementation is
   called with `(c, ws)`. It must call `ws.accept()` itself.
3. If the guard raises `Unauthorized` or `Forbidden`, the handler is never
   entered and the connection is denied with `ws.send_denial_response` using
   the same problem response an HTTP route would return
   (`application/problem+json`, e.g. 401/403 with `detail`). A client without
   `websocket.http.response` support sees close code 4401 or 4403 instead.
   Other exceptions from a guard are not converted to a denial; they follow
   the unhandled-exception path of the core app (500 or 1011).
4. For `public`, no guard runs and `c.principal` is `None`.

Everything described under [Handler lifecycle](#handler-lifecycle) applies to
contract operations too, including that app and group middleware do not run.
Contract path parameters are not declared in a request struct for WebSocket
operations; read them from `c.path_params` or `ws.path_params` (strings).

### OpenAPI

WebSocket operations are skipped when the OpenAPI document is derived: they
produce no entry under `paths` (a contract with only WebSocket operations
yields an empty `paths`). See [openapi.md](openapi.md).

### Startup checks

`app.build()` (run by `TestClient` on entry and by the lifespan) reports, as a
`StartupError`, among the usual contract checks:

| Problem | Message |
| --- | --- |
| A named guard has no implementation | `guard '<name>' has no implementation` |
| Return annotation other than `None` | `the return type of a WebSocket operation must be None, got ...` |
| `*` in the path | `contract paths cannot contain '*'` |
| Not exactly three positional parameters, or unresolvable annotations | `the signature must be (self, c, ws)` / `cannot resolve the annotations: ...` |

For HTTP operations the signature message reads `(self, c, req)`. The parameter names are not checked, only that there are three positional-or-keyword parameters.

## Security

A browser opens a WebSocket from any page and sends the user's cookies along, so a cookie-authenticated endpoint is open to cross-site WebSocket hijacking unless the handshake's `Origin` is checked. CORS does not apply to WebSockets, and `cors()` and all app and group middleware are skipped for WebSocket routes. Check `Origin` in the guard (contract routes) or before `accept()` (core routes).

```python
ALLOWED_ORIGINS = frozenset({"https://app.example"})


def require_browser_origin(head: RequestHead) -> None:
    origins = head.headers.get_all("origin")
    if len(origins) != 1 or origins[0] not in ALLOWED_ORIGINS:
        raise Forbidden("origin not allowed")


async def verify(head: RequestHead) -> User:
    require_browser_origin(head)
    ...  # then the token or cookie check, as above
```

A core handler does the same before `accept()` and answers with `send_denial_response`:

```python
async def chat(ws: WebSocket) -> None:
    origins = ws.headers.get_all("origin")
    if len(origins) != 1 or origins[0] not in ALLOWED_ORIGINS:
        await ws.send_denial_response(Response(403, b"origin not allowed"))
        return
    await ws.accept()
```

- Match the origin exactly against an allowlist of `scheme://host[:port]`. A missing `Origin`, more than one `Origin` header, `null` and anything not in the list is denied. Do not match by substring or suffix.
- This recipe is for endpoints used from browsers. Browsers always send `Origin` on a handshake; other clients may not, and a non-browser client can send any value, so the check never replaces authentication. Give non-browser clients their own authenticated endpoint.
- The guard's `RequestHead` has `scheme` (`ws` or `wss`) and `host`, but `allowed_hosts` is not applied to them and the `Host` header is chosen by the client. Do not build the origin allow-list from `host`; keep the explicit list.
- Prefer a header or the first message for tokens. A token in the query string appears in server and proxy logs.

## Testing

`TestClient.websocket_connect(path, *, headers=None, subprotocols=())`
returns an async context manager. It works for core and contract apps and
advertises the `websocket.http.response` extension, so denials appear as HTTP
responses. See [testing.md](testing.md) for the client itself.

```python
import asyncio

from lettia.testing import TestClient


async def main() -> None:
    async with TestClient(app) as client:
        headers = {"Authorization": "Bearer ok"}
        async with client.websocket_connect("/chat/r1", headers=headers) as ws:
            assert ws.accepted
            assert await ws.receive_text() == "ann@r1"
            await ws.send_text("hi")
            assert await ws.receive_text() == "hi"

        async with client.websocket_connect("/chat/r1") as ws:   # no token
            assert not ws.accepted
            assert ws.response_status == 401
            assert b"missing token" in ws.response_body


asyncio.run(main())
```

Session attributes and methods:

| Name | Meaning |
| --- | --- |
| `accepted` | `True` once the app sent `websocket.accept`. |
| `accepted_subprotocol` | Subprotocol the app accepted, or `None`. |
| `close_code`, `close_reason` | Set when the app closed during the handshake or a `receive_*` saw a close frame, or by `close()`. |
| `response_status`, `response_headers`, `response_body` | Set when the handshake was denied with an HTTP response (`None`, `[]`, `b""` otherwise). |
| `send_text(str)`, `send_bytes(bytes)`, `send_json(obj)` | Deliver a frame to the app. |
| `receive_text()`, `receive_bytes()`, `receive_json(type_spec=None)` | Next frame from the app. Raise `WebSocketDisconnect(code, reason)` if the app closed. |
| `close(code=1000, reason="")` | Send a client disconnect. |

Leaving the `async with` block closes an accepted session and waits up to one
second for the app task to finish before cancelling it. Query strings in the
path (`"/rooms/a?x=1"`) are passed as `query_string`. The scope's `client` is
`("testclient", 50000)`.

To exercise the no-extension fallback (close codes 4401, 4404), drive the app
directly with queues and a scope whose `extensions` is `{}`:

```python
import asyncio

scope = {"type": "websocket", "path": "/chat/r1", "raw_path": b"/chat/r1",
         "headers": [], "extensions": {}}
app.build()
to_app, from_app = asyncio.Queue(), asyncio.Queue()
task = asyncio.create_task(app(scope, to_app.get, from_app.put))
await to_app.put({"type": "websocket.connect"})
print(await from_app.get())
# {'type': 'websocket.close', 'code': 4401, 'reason': 'HTTP 401'}
await task
```

## See also

- [contract.md](contract.md) — operations, mounting, startup checks
- [guards.md](guards.md) — defining and composing guards
- [core.md](core.md) — Core app, routing, middleware
- [testing.md](testing.md) — `TestClient`, `make_context`
- [openapi.md](openapi.md) — OpenAPI derivation
