"""The ``op`` decorator: declares path, method and guard of a contract method."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import msgspec

from lettia.contract.context import Context
from lettia.contract.guards import GuardSpec
from lettia.contract.responses import Reply
from lettia.core.websocket import WebSocket

type _Handler[G, Req: msgspec.Struct | None, R: Reply] = Callable[
    [Any, Context[G], Req], Awaitable[R]
]
type _Decorator[G, Req: msgspec.Struct | None, R: Reply] = Callable[
    [_Handler[G, Req, R]], _Handler[G, Req, R]
]

type _WebSocketHandler[G] = Callable[[Any, Context[G], WebSocket], Awaitable[None]]
type _WebSocketDecorator[G] = Callable[[_WebSocketHandler[G]], _WebSocketHandler[G]]

_SPEC_ATTRIBUTE = "__lettia_op__"


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """What ``op`` declares about one contract method."""

    method: str
    path: str
    guard: GuardSpec[Any]
    tags: tuple[str, ...]
    operation_id: str | None
    openapi_extra: Mapping[str, object] | None


def operation_spec(function: object) -> OperationSpec | None:
    """The spec attached by ``op``, or ``None`` for an undecorated function."""
    spec = getattr(function, _SPEC_ATTRIBUTE, None)
    return spec if isinstance(spec, OperationSpec) else None


class _Route(Protocol):
    """Registers one HTTP method; ``guard`` is required."""

    def __call__[G, Req: msgspec.Struct | None, R: Reply](
        self,
        path: str,
        *,
        guard: GuardSpec[G],
        tags: Sequence[str] = (),
        operation_id: str | None = None,
        openapi_extra: Mapping[str, object] | None = None,
    ) -> _Decorator[G, Req, R]: ...


class _WebSocketRoute(Protocol):
    """Registers a WebSocket operation; ``guard`` is required."""

    def __call__[G](
        self,
        path: str,
        *,
        guard: GuardSpec[G],
        tags: Sequence[str] = (),
        operation_id: str | None = None,
        openapi_extra: Mapping[str, object] | None = None,
    ) -> _WebSocketDecorator[G]: ...


def _route(method: str) -> Callable[..., Any]:
    def declare(
        path: str,
        *,
        guard: GuardSpec[Any],
        tags: Sequence[str] = (),
        operation_id: str | None = None,
        openapi_extra: Mapping[str, object] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if not path.startswith("/"):
            raise ValueError(f"invalid path {path!r}: it must start with '/'")
        if not isinstance(cast(object, guard), GuardSpec):  # untyped callers
            raise TypeError(f"guard must be a GuardSpec, got {type(guard).__name__}")
        spec = OperationSpec(
            method,
            path,
            guard,
            tuple(tags),
            operation_id,
            openapi_extra,
        )

        def attach(function: Callable[..., Any]) -> Callable[..., Any]:
            setattr(function, _SPEC_ATTRIBUTE, spec)
            return function

        return attach

    return declare


class _Op:
    get: _Route
    post: _Route
    put: _Route
    patch: _Route
    delete: _Route
    websocket: _WebSocketRoute

    def __init__(self) -> None:
        self.get = cast(_Route, _route("GET"))
        self.post = cast(_Route, _route("POST"))
        self.put = cast(_Route, _route("PUT"))
        self.patch = cast(_Route, _route("PATCH"))
        self.delete = cast(_Route, _route("DELETE"))
        self.websocket = cast(_WebSocketRoute, _route("WEBSOCKET"))


op = _Op()
