"""Per-request state handed to every handler, with the response helpers."""

from collections.abc import AsyncIterator

import msgspec

from lettia.core.headers import MutableHeaders
from lettia.core.request import Request
from lettia.core.response import Response, ResponseBody

_encode_json = msgspec.json.Encoder().encode


# The media types the helpers use most are encoded once, not on every response.
_TEXT_TYPE = (b"content-type", b"text/plain; charset=utf-8")
_JSON_TYPE = (b"content-type", b"application/json")


def _with_content_type(status: int, body: ResponseBody, media_type: str) -> Response:
    headers = MutableHeaders()
    headers.set("content-type", media_type)
    return Response(status, body, headers)


class Context:
    """Request, path parameters and response helpers for one request.

    Extended with a principal by :class:`lettia.Context`.
    """

    __slots__ = ("_state", "path_params", "request")

    def __init__(
        self, request: Request, path_params: dict[str, str] | None = None
    ) -> None:
        self.request = request
        self.path_params: dict[str, str] = {} if path_params is None else path_params
        self._state: dict[object, object] | None = None

    @property
    def state(self) -> dict[object, object]:
        """Storage behind :class:`~lettia.core.Key`; use a key instead of this."""
        if self._state is None:
            self._state = {}
        return self._state

    def text(self, status: int, text: str) -> Response:
        return Response(status, text.encode(), MutableHeaders([_TEXT_TYPE]))

    def json(self, status: int, data: object) -> Response:
        return Response(status, _encode_json(data), MutableHeaders([_JSON_TYPE]))

    def no_content(self) -> Response:
        return Response(204)

    def stream(
        self, status: int, chunks: AsyncIterator[bytes], media_type: str
    ) -> Response:
        return _with_content_type(status, chunks, media_type)

    # Defined last: inside the class body, a method named ``bytes`` would shadow
    # the builtin type in every annotation that follows it.
    def bytes(
        self, status: int, data: bytes, media_type: str = "application/octet-stream"
    ) -> Response:
        return _with_content_type(status, data, media_type)
