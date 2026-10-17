"""``recover``: turn exceptions from inner middleware into responses."""

import logging

from lettia.core import (
    ClientDisconnect,
    Context,
    Handler,
    HTTPError,
    Middleware,
    Response,
)
from lettia.core.problem import default_error_handler

logger = logging.getLogger("lettia")


def recover(*, debug: bool = False) -> Middleware:
    """Answer exceptions raised further in with a response.

    ``App`` already turns handler exceptions into responses, so this matters for
    failures of *inner middleware*: place ``recover`` early in ``app.use(...)`` and
    the middleware before it sees a 500 response instead of an exception.
    """
    handle = default_error_handler(debug=debug)

    def middleware(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            try:
                return await next_(c)
            except ClientDisconnect:
                raise
            except Exception as exc:
                if not isinstance(exc, HTTPError):
                    logger.error(
                        "unhandled exception on %s %s",
                        c.request.method,
                        c.request.path,
                        exc_info=exc,
                    )
                return await handle(c, exc)

        return run

    return middleware
