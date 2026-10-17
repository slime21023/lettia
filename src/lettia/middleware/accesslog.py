"""``access_log``: one log record per request."""

import logging
from time import perf_counter

from lettia.core import ClientDisconnect, Context, Handler, Middleware, Response
from lettia.middleware.requestid import RequestId

_ACCESS_LOGGER = logging.getLogger("lettia.access")

# nginx's convention for "the client closed the connection before we answered".
_CLIENT_CLOSED = 499


def access_log(*, logger: logging.Logger = _ACCESS_LOGGER) -> Middleware:
    """Log method, path, status and duration after the response is produced.

    The path is the raw (still percent-encoded) path without the query string, so
    a request cannot forge log lines with ``%0A`` and secrets in the query do not
    reach the log. The record also carries ``method``, ``path``, ``status``,
    ``duration_ms``, ``client`` and ``request_id`` as attributes.
    """

    def middleware(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            start = perf_counter()
            try:
                response = await next_(c)
            except ClientDisconnect:
                _log(logger, c, _CLIENT_CLOSED, start)
                raise
            except Exception:
                _log(logger, c, 500, start)
                raise
            _log(logger, c, response.status, start)
            return response

        return run

    return middleware


def _log(logger: logging.Logger, c: Context, status: int, start: float) -> None:
    if not logger.isEnabledFor(logging.INFO):
        return
    request = c.request
    path = request.loggable_path
    duration_ms = (perf_counter() - start) * 1000
    rid = RequestId.get(c, None)
    message = f"{request.method} {path} {status} {duration_ms:.2f}ms"
    if rid is not None:
        message += f" request_id={rid}"
    logger.info(
        message,
        extra={
            "method": request.method,
            "path": path,
            "status": status,
            "duration_ms": duration_ms,
            "client": request.client[0] if request.client else None,
            "request_id": rid,
        },
    )
