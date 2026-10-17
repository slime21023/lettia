"""RFC 9457 problem+json responses and the default error handler."""

from collections.abc import Awaitable, Callable, Mapping

import msgspec

from lettia.core.context import Context
from lettia.core.errors import HTTPError
from lettia.core.headers import MutableHeaders
from lettia.core.response import Response

type ErrorHandler = Callable[[Context, Exception], Awaitable[Response]]

_encode_json = msgspec.json.Encoder().encode


def problem_response(
    status: int,
    title: str,
    detail: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response:
    body: dict[str, object] = {"type": "about:blank", "title": title, "status": status}
    if detail is not None:
        body["detail"] = detail
    response_headers = MutableHeaders()
    response_headers.set("content-type", "application/problem+json")
    for name, value in (headers or {}).items():
        response_headers.set(name, value)
    return Response(status, _encode_json(body), response_headers)


def default_error_handler(*, debug: bool) -> ErrorHandler:
    """Render ``HTTPError`` as given and anything else as a 500.

    Details of unexpected exceptions are only exposed in debug mode.
    """

    async def handle(c: Context, exc: Exception) -> Response:
        if isinstance(exc, HTTPError):
            return problem_response(exc.status, exc.title, exc.detail, exc.headers)
        detail = f"{type(exc).__name__}: {exc}" if debug else None
        return problem_response(500, "Internal Server Error", detail)

    return handle
