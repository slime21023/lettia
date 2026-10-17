"""Handler and middleware signatures shared by Core and contract routes."""

from collections.abc import Awaitable, Callable

from lettia.core.context import Context
from lettia.core.response import Response

type Handler = Callable[[Context], Awaitable[Response]]
type Middleware = Callable[[Handler], Handler]
