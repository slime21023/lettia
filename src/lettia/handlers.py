"""Shared handler contracts used by routing and application composition."""

from collections.abc import Awaitable, Callable

from lettia.context import Context
from lettia.response import ResponseValue
from lettia.websocket import WebSocketContext

type HTTPHandler = Callable[[Context], ResponseValue | Awaitable[ResponseValue]]
type WebSocketHandler = Callable[[WebSocketContext], Awaitable[None]]
type RouteHandler = HTTPHandler | WebSocketHandler
type HTTPDecorator = Callable[[HTTPHandler], HTTPHandler]
type LifecycleHandler = Callable[[], Awaitable[None] | None]
type ErrorHandler = Callable[[Context, Exception], Awaitable[ResponseValue]]
