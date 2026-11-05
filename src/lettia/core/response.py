"""The HTTP response: a mutable value object, unaware of ASGI ``send``."""

from collections.abc import AsyncIterator, Callable

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
    """

    __slots__ = ("after_send", "body", "headers", "status")

    def __init__(
        self,
        status: int = 200,
        body: ResponseBody = None,
        headers: MutableHeaders | None = None,
        *,
        after_send: Callable[[], None] | None = None,
    ) -> None:
        if not 100 <= status <= 599:
            raise ValueError(f"status must be in 100..599, got {status}")
        self.status = status
        self.body = body
        self.headers = MutableHeaders() if headers is None else headers
        self.after_send = after_send
