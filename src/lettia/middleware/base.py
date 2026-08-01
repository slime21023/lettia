from collections.abc import Awaitable, Callable
from typing import Any

from lettia.context import Context

Handler = Callable[[Context], Awaitable[Any]]
Middleware = Callable[[Handler], Handler]


def build_chain(handler: Handler, middlewares: list[Middleware]) -> Handler:
    chain = handler
    for middleware in reversed(middlewares):
        chain = middleware(chain)
    return chain
