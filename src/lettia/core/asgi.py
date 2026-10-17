"""ASGI adapter: builds the request, runs the handler, serializes the response."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, MutableMapping
from typing import Any

from lettia.core.context import Context
from lettia.core.errors import ClientDisconnect
from lettia.core.handler import Handler
from lettia.core.request import DEFAULT_MAX_BODY_SIZE, Request
from lettia.core.response import Response

type Scope = Mapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def asgi_app(
    handler: Handler, *, max_body_size: int | None = DEFAULT_MAX_BODY_SIZE
) -> ASGIApp:
    """Wrap ``handler`` as an ASGI application for HTTP requests.

    Errors raised by the handler propagate: converting them to responses is the
    job of ``App``'s error handler, which sits above this adapter.
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            raise NotImplementedError(f"unsupported ASGI scope: {scope['type']}")
        client = scope.get("client")
        request = Request(
            method=scope["method"],
            path=scope["path"],
            raw_path=scope.get("raw_path") or None,
            query_string=scope.get("query_string", b""),
            headers=scope["headers"],
            client=(client[0], client[1]) if client else None,
            body=_body_chunks(receive),
            max_body_size=max_body_size,
        )
        response = await handler(Context(request))
        await _send_response(response, request.method == "HEAD", send)

    return app


async def _body_chunks(receive: Receive) -> AsyncIterator[bytes]:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ClientDisconnect
        yield message.get("body", b"")
        if not message.get("more_body", False):
            return


def _has_no_body(status: int) -> bool:
    return status < 200 or status in (204, 304)


async def _send_response(response: Response, is_head: bool, send: Send) -> None:
    body = response.body
    headers = response.headers.raw
    drop_body = is_head or _has_no_body(response.status)

    if body is None or isinstance(body, bytes):
        if not _has_no_body(response.status) and not any(
            name == b"content-length" for name, _ in headers
        ):
            headers = [*headers, (b"content-length", str(len(body or b"")).encode())]
        await send(
            {
                "type": "http.response.start",
                "status": response.status,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"" if drop_body or body is None else body,
                "more_body": False,
            }
        )
        return

    await send(
        {"type": "http.response.start", "status": response.status, "headers": headers}
    )
    try:
        if not drop_body:
            async for chunk in body:
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    finally:
        await _aclose(body)


async def _aclose(body: AsyncIterator[bytes]) -> None:
    aclose = getattr(body, "aclose", None)
    if aclose is not None:
        await aclose()
