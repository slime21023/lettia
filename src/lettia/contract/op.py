"""The ``op`` decorator: declares path, method and guard of a contract method."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import msgspec

from lettia.contract.context import Context
from lettia.contract.guards import GuardSpec
from lettia.contract.responses import Reply

type _Handler[G, Req: msgspec.Struct | None, R: Reply] = Callable[
    [Any, Context[G], Req], Awaitable[R]
]
type _Decorator[G, Req: msgspec.Struct | None, R: Reply] = Callable[
    [_Handler[G, Req, R]], _Handler[G, Req, R]
]

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


def _route(method: str) -> _Route:
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

    # The runtime closure is not generic; the Protocol carries the public typing.
    return cast(_Route, declare)


class _Op:
    get: _Route
    post: _Route
    put: _Route
    patch: _Route
    delete: _Route

    def __init__(self) -> None:
        self.get = _route("GET")
        self.post = _route("POST")
        self.put = _route("PUT")
        self.patch = _route("PATCH")
        self.delete = _route("DELETE")


op = _Op()
