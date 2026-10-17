"""The handler signature shared by Core routes and compiled contract routes."""

from collections.abc import Awaitable, Callable

from lettia.core.context import Context
from lettia.core.response import Response

type Handler = Callable[[Context], Awaitable[Response]]
