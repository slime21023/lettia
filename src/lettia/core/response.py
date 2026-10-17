"""The HTTP response: a mutable value object, unaware of ASGI ``send``."""

from collections.abc import AsyncIterator

from lettia.core.headers import MutableHeaders

type ResponseBody = bytes | AsyncIterator[bytes] | None


class Response:
    """Status, headers and body of one response.

    Middleware owns the response it receives from ``next`` and may add headers to
    it; a response must not be shared between requests. Serialization (content
    length, chunking, dropping the body for ``HEAD``) belongs to the ASGI adapter.
    """

    __slots__ = ("body", "headers", "status")

    def __init__(
        self,
        status: int = 200,
        body: ResponseBody = None,
        headers: MutableHeaders | None = None,
    ) -> None:
        if not 100 <= status <= 599:
            raise ValueError(f"status must be in 100..599, got {status}")
        self.status = status
        self.body = body
        self.headers = MutableHeaders() if headers is None else headers
