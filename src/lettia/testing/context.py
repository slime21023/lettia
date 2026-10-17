"""Build a request context without ASGI, for calling handlers directly."""

from collections.abc import AsyncIterator, Mapping
from typing import Any, overload

from lettia.contract.context import Context
from lettia.core.request import Request


@overload
def make_context(
    *,
    method: str = "GET",
    path: str = "/",
    headers: Mapping[str, str] | None = None,
    query: str = "",
    body: bytes = b"",
    path_params: Mapping[str, str] | None = None,
) -> Context[None]: ...
@overload
def make_context[G](
    principal: G,
    *,
    method: str = "GET",
    path: str = "/",
    headers: Mapping[str, str] | None = None,
    query: str = "",
    body: bytes = b"",
    path_params: Mapping[str, str] | None = None,
) -> Context[G]: ...
def make_context(
    principal: Any = None,
    *,
    method: str = "GET",
    path: str = "/",
    headers: Mapping[str, str] | None = None,
    query: str = "",
    body: bytes = b"",
    path_params: Mapping[str, str] | None = None,
) -> Context[Any]:
    """A ``Context[G]`` for ``principal``, as a guard would have produced it.

    The request is built from the given parts and its body is served from memory
    (without a size limit), so a handler can be exercised like any async method.
    """

    async def chunks() -> AsyncIterator[bytes]:
        if body:
            yield body

    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    request = Request(
        method=method,
        path=path,
        query_string=query.encode(),
        headers=raw_headers,
        body=chunks(),
        max_body_size=None,
    )
    context: Context[Any] = Context(request, dict(path_params or {}))
    context.principal = principal
    return context
