"""The HTTP response: a mutable value object, unaware of ASGI ``send``."""

from collections.abc import AsyncIterator, Callable
from datetime import datetime

from lettia.core.cookies import SameSite, format_set_cookie
from lettia.core.headers import MutableHeaders

type ResponseBody = bytes | AsyncIterator[bytes] | None


class Response:
    """Status, headers and body of one response.

    Middleware owns the response it receives from ``next`` and may add headers to
    it; a response must not be shared between requests. Serialization (content
    length, chunking, dropping the body for ``HEAD``) belongs to the ASGI adapter.

    ``after_send`` is a synchronous, non-blocking notification the ASGI adapter
    calls once, after the whole response was sent and a streamed body was closed.
    It is not called when the handler, the send or the stream fails or the request
    is cancelled, and it only travels with the response that is actually sent: a
    middleware that returns a different response drops it. A middleware that sets
    it must wrap an existing callback, not replace it.

    ``watch_disconnect`` opts a streamed body into disconnect monitoring: the ASGI
    adapter then owns ``receive`` for the whole response, stops the stream when the
    client goes away (raising ``ClientDisconnect``) and does not call
    ``after_send``. It does not support a request body that is still being read
    while the response streams. Without it, streamed bodies are sent unmonitored.
    """

    __slots__ = ("after_send", "body", "headers", "status", "watch_disconnect")

    def __init__(
        self,
        status: int = 200,
        body: ResponseBody = None,
        headers: MutableHeaders | None = None,
        *,
        after_send: Callable[[], None] | None = None,
        watch_disconnect: bool = False,
    ) -> None:
        if not 100 <= status <= 599:
            raise ValueError(f"status must be in 100..599, got {status}")
        self.status = status
        self.body = body
        self.headers = MutableHeaders() if headers is None else headers
        self.after_send = after_send
        self.watch_disconnect = watch_disconnect

    def set_cookie(
        self,
        name: str,
        value: str,
        *,
        max_age: int | None = None,
        expires: datetime | None = None,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        httponly: bool = True,
        samesite: SameSite | None = "lax",
    ) -> None:
        """Add a ``Set-Cookie`` header; other cookies already set are kept.

        ``HttpOnly`` and ``SameSite=Lax`` are on by default; pass ``secure=True``
        outside local development. ``expires`` must be timezone-aware. The value
        is written as given, so encode anything that is not cookie-safe first.
        Raises ``ValueError`` for an invalid name, value or attribute.
        """
        if expires is not None and expires.tzinfo is None:
            raise ValueError("expires must be timezone-aware")
        self.headers.add(
            "set-cookie",
            format_set_cookie(
                name,
                value,
                max_age=max_age,
                expires=None if expires is None else int(expires.timestamp()),
                path=path,
                domain=domain,
                secure=secure,
                httponly=httponly,
                samesite=samesite,
            ),
        )

    def delete_cookie(
        self,
        name: str,
        *,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        httponly: bool = True,
        samesite: SameSite | None = "lax",
    ) -> None:
        """Ask the browser to remove a cookie.

        ``path`` and ``domain`` must match the ones the cookie was set with, or
        the browser keeps it.
        """
        self.headers.add(
            "set-cookie",
            format_set_cookie(
                name,
                "",
                max_age=0,
                expires=0,
                path=path,
                domain=domain,
                secure=secure,
                httponly=httponly,
                samesite=samesite,
            ),
        )
