"""Typed contracts for the ASGI 3.0 core and HTTP/WebSocket 2.5 messages."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Literal, NotRequired, Protocol, TypedDict, overload

type Header = tuple[bytes, bytes]
type Headers = list[Header]
type ScopeState = dict[str, object]


class ASGISpec(TypedDict):
    version: str
    spec_version: NotRequired[str]


class HTTPScope(TypedDict):
    type: Literal["http"]
    asgi: ASGISpec
    http_version: str
    method: str
    scheme: str
    path: str
    raw_path: NotRequired[bytes]
    query_string: bytes
    root_path: str
    headers: Headers
    client: NotRequired[tuple[str, int] | None]
    server: NotRequired[tuple[str, int | None] | None]
    state: NotRequired[ScopeState]


class WebSocketScope(TypedDict):
    type: Literal["websocket"]
    asgi: ASGISpec
    http_version: str
    scheme: str
    path: str
    raw_path: NotRequired[bytes]
    query_string: bytes
    root_path: str
    headers: Headers
    client: NotRequired[tuple[str, int] | None]
    server: NotRequired[tuple[str, int | None] | None]
    subprotocols: list[str]
    state: NotRequired[ScopeState]


class LifespanScope(TypedDict):
    type: Literal["lifespan"]
    asgi: ASGISpec
    state: NotRequired[ScopeState]


type ASGIScope = HTTPScope | WebSocketScope | LifespanScope


# Shared by framework components without adding a public routing API.
def _route_path(scope: HTTPScope | WebSocketScope) -> str:  # pyright: ignore[reportUnusedFunction]
    path = scope["path"]
    root = scope.get("root_path", "")
    for prefix in (root, root.rstrip("/")):
        if prefix and (path == prefix or path.startswith(prefix + "/")):
            return path[len(prefix) :] or "/"
    return path


class HTTPRequestEvent(TypedDict):
    type: Literal["http.request"]
    body: NotRequired[bytes]
    more_body: NotRequired[bool]


class HTTPDisconnectEvent(TypedDict):
    type: Literal["http.disconnect"]


type HTTPReceiveEvent = HTTPRequestEvent | HTTPDisconnectEvent


class HTTPResponseStartEvent(TypedDict):
    type: Literal["http.response.start"]
    status: int
    headers: Headers
    trailers: NotRequired[bool]


class HTTPResponseBodyEvent(TypedDict):
    type: Literal["http.response.body"]
    body: NotRequired[bytes]
    more_body: NotRequired[bool]


class HTTPResponseTrailersEvent(TypedDict):
    type: Literal["http.response.trailers"]
    headers: Headers
    more_trailers: NotRequired[bool]


type HTTPSendEvent = (
    HTTPResponseStartEvent | HTTPResponseBodyEvent | HTTPResponseTrailersEvent
)
type HTTPReceive = Callable[[], Awaitable[HTTPReceiveEvent]]
type HTTPSend = Callable[[HTTPSendEvent], Awaitable[None]]


class WebSocketConnectEvent(TypedDict):
    type: Literal["websocket.connect"]


class WebSocketReceiveEvent(TypedDict):
    type: Literal["websocket.receive"]
    bytes: NotRequired[bytes | None]
    text: NotRequired[str | None]


class WebSocketDisconnectEvent(TypedDict):
    type: Literal["websocket.disconnect"]
    code: int
    reason: NotRequired[str]


type WebSocketReceiveMessage = WebSocketReceiveEvent | WebSocketDisconnectEvent
type WebSocketReceive = Callable[
    [], Awaitable[WebSocketConnectEvent | WebSocketReceiveMessage]
]


class WebSocketAcceptEvent(TypedDict):
    type: Literal["websocket.accept"]
    subprotocol: NotRequired[str | None]
    headers: NotRequired[Headers]


class WebSocketSendEvent(TypedDict):
    type: Literal["websocket.send"]
    bytes: NotRequired[bytes | None]
    text: NotRequired[str | None]


class WebSocketCloseEvent(TypedDict):
    type: Literal["websocket.close"]
    code: NotRequired[int]
    reason: NotRequired[str]


type WebSocketSend = Callable[
    [WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent], Awaitable[None]
]


class LifespanStartupEvent(TypedDict):
    type: Literal["lifespan.startup"]


class LifespanShutdownEvent(TypedDict):
    type: Literal["lifespan.shutdown"]


type LifespanReceive = Callable[
    [], Awaitable[LifespanStartupEvent | LifespanShutdownEvent]
]


class LifespanStartupCompleteEvent(TypedDict):
    type: Literal["lifespan.startup.complete"]


class LifespanStartupFailedEvent(TypedDict):
    type: Literal["lifespan.startup.failed"]
    message: str


class LifespanShutdownCompleteEvent(TypedDict):
    type: Literal["lifespan.shutdown.complete"]


class LifespanShutdownFailedEvent(TypedDict):
    type: Literal["lifespan.shutdown.failed"]
    message: str


type LifespanSend = Callable[
    [
        LifespanStartupCompleteEvent
        | LifespanStartupFailedEvent
        | LifespanShutdownCompleteEvent
        | LifespanShutdownFailedEvent
    ],
    Awaitable[None],
]


class ASGIApp(Protocol):
    @overload
    async def __call__(
        self, scope: HTTPScope, receive: HTTPReceive, send: HTTPSend
    ) -> None: ...

    @overload
    async def __call__(
        self, scope: WebSocketScope, receive: WebSocketReceive, send: WebSocketSend
    ) -> None: ...

    @overload
    async def __call__(
        self, scope: LifespanScope, receive: LifespanReceive, send: LifespanSend
    ) -> None: ...


type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONDocument = Mapping[str, JSONValue] | list[JSONValue]
