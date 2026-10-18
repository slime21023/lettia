"""Handler and middleware signatures shared by Core and contract routes."""

from collections.abc import Awaitable, Callable

from lettia.core.context import Context
from lettia.core.response import Response
from lettia.core.websocket import WebSocket

type Handler = Callable[[Context], Awaitable[Response]]
type WebSocketHandler = Callable[[WebSocket], Awaitable[None]]
type Middleware = Callable[[Handler], Handler]
