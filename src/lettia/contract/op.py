"""The ``op`` decorator: declares path, method and guard of a contract method."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

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


class _Op:
    get: _Route
    post: _Route
    put: _Route
    patch: _Route
    delete: _Route


op = _Op()
