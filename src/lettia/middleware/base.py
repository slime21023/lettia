from collections.abc import Awaitable, Callable

from lettia.context import Context
from lettia.response import Response

Handler = Callable[[Context], Awaitable[Response]]
Middleware = Callable[[Handler], Handler]


def build_chain(handler: Handler, middlewares: list[Middleware]) -> Handler:
    chain = handler
    for middleware in reversed(middlewares):
        chain = middleware(chain)
    return chain
